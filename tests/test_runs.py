from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from backend.main import app
from backend.database import TestRun as DatabaseTestRun
from backend.schemas import FinalReport
from tests.test_reporter import report_payload, valid_report


def signup(client: TestClient, email: str) -> None:
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "safe-password-123"},
    )
    assert response.status_code == 201, response.text


def test_completed_failed_execution_is_saved_and_serialized(
    monkeypatch,
) -> None:
    import backend.main as main

    expected_report = valid_report()
    monkeypatch.setattr(main, "generate_report", lambda _request, user_id=None: FinalReport.model_validate(expected_report, strict=False))

    with TestClient(app) as client:
        signup(client, "owner@example.com")
        response = client.post("/api/generate-report", json=report_payload())

        assert response.status_code == 200, response.text
        assert {key: response.json()[key] for key in expected_report} == expected_report

        listing = client.get("/api/runs")
        assert listing.status_code == 200, listing.text
        assert len(listing.json()) == 1
        run = listing.json()[0]
        assert run["target_url"] == "https://example.com/login"
        assert run["flow"] == "login"
        assert run["status"] == "failed"

        detail = client.get(f"/api/runs/{run['id']}")
        assert detail.status_code == 200, detail.text
        saved = detail.json()
        assert saved["goal"] == report_payload()["goal"]
        assert saved["execution"]["status"] == "failed"
        assert saved["analysis"]["status"] == "failed"
        assert {key: saved["report"][key] for key in expected_report} == expected_report
        assert saved["evidence_run_id"] == expected_report["run_id"]
        assert saved["execution"]["evidence"][0]["filename"] == "test-01-failure.png"
        assert isinstance(saved["created_at"], int)
        assert isinstance(saved["completed_at"], int)


def test_history_and_evidence_are_private_to_the_owner(monkeypatch, tmp_path) -> None:
    import backend.main as main
    import backend.evidence as evidence

    monkeypatch.setattr(main, "generate_report", lambda _request, user_id=None: FinalReport.model_validate(valid_report(), strict=False))
    screenshot = tmp_path / ("a" * 32) / "test-01-failure.png"
    screenshot.parent.mkdir()
    screenshot.write_bytes(b"private screenshot")
    monkeypatch.setattr(evidence, "get_artifacts_root", lambda: tmp_path)
    with TestClient(app) as owner:
        signup(owner, "owner@example.com")
        assert owner.post("/api/generate-report", json=report_payload()).status_code == 200
        run_id = owner.get("/api/runs").json()[0]["id"]
        owner_evidence = owner.get(f"/api/evidence/{'a' * 32}/test-01-failure.png")
        assert owner_evidence.status_code == 200
        assert owner_evidence.content == b"private screenshot"

    with TestClient(app) as other_user:
        signup(other_user, "other@example.com")
        assert other_user.get("/api/runs").json() == []
        assert other_user.get(f"/api/runs/{run_id}").status_code == 404
        assert other_user.get(f"/api/evidence/{'a' * 32}/test-01-failure.png").status_code == 404


def test_history_requires_authentication() -> None:
    with TestClient(app) as client:
        assert client.get("/api/runs").status_code == 401
        assert client.get("/api/runs/unknown").status_code == 401


def test_run_pin_persists_for_owner_and_is_private(monkeypatch) -> None:
    import backend.main as main

    monkeypatch.setattr(
        main,
        "generate_report",
        lambda _request, user_id=None: FinalReport.model_validate(valid_report(), strict=False),
    )
    with TestClient(app) as owner:
        signup(owner, "pin-owner@example.com")
        assert owner.post("/api/generate-report", json=report_payload()).status_code == 200
        run_id = owner.get("/api/runs").json()[0]["id"]

        pinned = owner.patch(f"/api/runs/{run_id}/pin", json={"pinned": True})
        assert pinned.status_code == 200, pinned.text
        assert pinned.json() == {"id": run_id, "pinned": True}
        assert owner.get("/api/runs").json()[0]["pinned"] is True
        assert owner.get(f"/api/runs/{run_id}").json()["pinned"] is True

        unpinned = owner.patch(f"/api/runs/{run_id}/pin", json={"pinned": False})
        assert unpinned.status_code == 200
        assert owner.get("/api/runs").json()[0]["pinned"] is False

    with TestClient(app) as another_user:
        signup(another_user, "pin-other@example.com")
        assert another_user.patch(f"/api/runs/{run_id}/pin", json={"pinned": True}).status_code == 404


def test_reporter_failure_does_not_mark_completed_browser_agent_failed(
    monkeypatch,
) -> None:
    import backend.main as main

    payload = report_payload()
    run_id = payload["execution"]["run_id"]

    def fail_report(_request, user_id=None):
        raise main.ReporterOutputError("malformed provider JSON")

    monkeypatch.setattr(main, "generate_report", fail_report)
    with Session(main.engine) as db:
        db.add(DatabaseTestRun(
            id=run_id,
            user_id=1,
            target_url=payload["url"],
            goal=payload["goal"],
            flow="login",
            status="failed",
            created_at=1,
            completed_at=1,
            plan_json=payload["plan"],
            execution_json=payload["execution"],
            analysis_json=payload["analysis"],
            report_json={},
            evidence_run_id=run_id,
            agent_status="completed",
            target_domain="example.com",
            scope="focused",
        ))
        db.commit()

    with TestClient(app) as client:
        signup(client, "report-owner@example.com")
        response = client.post("/api/generate-report", json=payload)

        assert response.status_code == 502
        detail = client.get(f"/api/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["agent_status"] == "completed"
