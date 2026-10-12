from fastapi.testclient import TestClient

from backend.main import app
from backend.schemas import ExecutionResult, TestPlan as PlanSchema


def plan_payload():
    return {
        "flow": "login",
        "scope": "focused",
        "summary": "Check rejection of a wrong password.",
        "tests": [{
            "name": "Wrong password",
            "goal": "Verify wrong passwords are rejected.",
            "prerequisites": ["test_email", "test_password"],
            "description": "Submit an invalid password.",
            "steps": [
                {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
                {"action": "fill", "target": "Email", "value": "{{test_email}}"},
                {"action": "fill", "target": "Password", "value": "{{test_password}}"},
                {"action": "click", "target": "Log in", "value": None},
            ],
            "expected_result": "The login is rejected.",
            "assertions": [{"text": "Invalid credentials", "expectation": "visible", "timeout_ms": 1000}],
        }],
    }


def test_execute_persists_run_before_analysis_without_test_secrets(monkeypatch):
    import backend.main as main

    raw_plan = plan_payload()
    raw_plan["tests"][0]["steps"][1]["value"] = "person@example.test"
    raw_plan["tests"][0]["steps"][2]["value"] = "PrivatePass123!"
    plan = PlanSchema.model_validate(raw_plan, strict=False)
    received = {}

    def fake_execute(**kwargs):
        received.update(kwargs)
        return ExecutionResult.model_validate({
        "status": "passed", "agent_status": "completed", "run_id": "a" * 32,
        "evidence": [], "tests": [], "investigation": [], "effective_plan": kwargs["plan"].model_dump(mode="json"),
        }, strict=False)

    monkeypatch.setattr(main, "execute_test_plan", fake_execute)
    with TestClient(app) as client:
        response = client.post("/api/execute-plan", json={
            "url": "https://example.com/login", "goal": "Check wrong password",
            "plan": raw_plan, "authorized_test_account": True, "test_data": {
                "valid_test_email": "person@example.test", "negative_test_password": "private-secret-value",
            },
        })
        assert response.status_code == 200, response.text
        runs = client.get("/api/runs").json()
        assert len(runs) == 1
        detail = client.get(f"/api/runs/{'a' * 32}").json()
        assert received["plan"].tests[0].steps[1].value == "{{valid_test_email}}"
        assert received["plan"].tests[0].steps[2].value == "{{negative_test_password}}"
        assert detail["agent_status"] == "completed"
        assert "private-secret-value" not in str(detail)
        assert "person@example.test" not in str(detail)


def test_saved_suite_replay_is_owned_and_keeps_placeholders():
    import backend.main as main
    main.app.dependency_overrides.clear()
    with TestClient(app) as owner:
        assert owner.post("/api/auth/signup", json={
            "email": "suite-owner@example.com", "password": "safe-password-123",
        }).status_code == 201
        saved = owner.post("/api/suites", json={
            "name": "Login rejection", "url": "https://example.com/login",
            "goal": "Check wrong password", "plan": plan_payload(),
        })
        assert saved.status_code == 201, saved.text
        suite_id = saved.json()["id"]
        assert saved.json()["plan"]["tests"][0]["steps"][2]["value"] == "{{negative_test_password}}"
        assert owner.get(f"/api/suites/{suite_id}").status_code == 200

    with TestClient(app) as other:
        assert other.post("/api/auth/signup", json={
            "email": "suite-other@example.com", "password": "safe-password-123",
        }).status_code == 201
        assert other.get("/api/suites").json() == []
        assert other.get(f"/api/suites/{suite_id}").status_code == 404


def test_history_compare_is_deterministic_and_marks_resolved_scenario():
    import time
    import backend.database as database
    from backend.database import Session, TestRun as RunRecord

    plan = PlanSchema.model_validate(plan_payload(), strict=False)
    scenario = plan.tests[0]
    def run(run_id, assertion_status):
        return RunRecord(
            id=run_id, user_id=1, target_url="https://example.com/login",
            target_domain="example.com", goal="Check wrong password", flow="login",
            status="failed" if assertion_status == "failed" else "passed",
            created_at=int(time.time()), completed_at=int(time.time()),
            plan_json=plan.model_dump(mode="json"),
            execution_json={"tests": [{
                "name": scenario.name, "scenario_id": scenario.scenario_id,
                "status": assertion_status,
                "steps": [
                    {
                        "action": step.action.value,
                        "target": step.target,
                        "status": "passed",
                        "message": "Step completed successfully.",
                    }
                    for step in scenario.steps
                ],
                "assertions": [{
                    "text": "Invalid credentials", "expectation": "visible",
                    "timeout_ms": 1000, "status": assertion_status,
                    "observed_text": "Invalid credentials",
                    "message": "Observed assertion evidence",
                }],
            }]},
            analysis_json={}, report_json={}, evidence_run_id=run_id,
            agent_status="completed", scope="focused", source_run_id=None,
        )

    with Session(database.engine) as db:
        db.add_all([run("b" * 32, "failed"), run("c" * 32, "passed")])
        db.commit()
    with TestClient(app) as client:
        response = client.get(f"/api/runs/{'b' * 32}/compare/{'c' * 32}")
        assert response.status_code == 200, response.text
        assert response.json()["comparable"] is True
        assert response.json()["scenarios"][0]["change"] == "resolved"


def test_history_comparison_explains_added_and_removed_scenarios_and_coverage():
    import time
    import backend.database as database
    from backend.database import Session, TestRun as RunRecord
    from backend.schemas import PlannedTest

    base_plan = PlanSchema.model_validate(plan_payload(), strict=False)
    existing = base_plan.tests[0]
    added_payload = existing.model_dump(mode="json")
    added_payload.update({
        "name": "Empty email",
        "goal": "Verify empty email is rejected.",
        "description": "Submit without an email.",
        "expected_result": "The empty email is rejected.",
        "scenario_id": "empty-email",
        "prerequisites": [],
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "assertions": [{"kind": "login_rejected", "target": "Email", "timeout_ms": 500}],
    })
    added = PlannedTest.model_validate(added_payload, strict=False)
    old_plan = base_plan
    new_plan = PlanSchema.model_validate({
        "flow": "login", "scope": "broad", "summary": base_plan.summary,
        "tests": [existing.model_dump(mode="json"), added.model_dump(mode="json")],
    }, strict=False)
    now = int(time.time())

    def record(run_id, plan):
        return RunRecord(
            id=run_id, user_id=1, target_url="https://example.com/login",
            target_domain="example.com", goal="Compare login coverage", flow="login",
            status="passed", created_at=now, completed_at=now,
            plan_json=plan.model_dump(mode="json"),
            execution_json={"tests": []}, analysis_json={}, report_json={},
            evidence_run_id=run_id, agent_status="completed", scope=plan.scope.value,
            source_run_id=None,
        )

    with Session(database.engine) as db:
        db.add_all([record("d" * 32, old_plan), record("e" * 32, new_plan)])
        db.commit()

    with TestClient(app) as client:
        response = client.get(f"/api/runs/{'d' * 32}/compare/{'e' * 32}")
    assert response.status_code == 200, response.text
    comparison = response.json()
    assert comparison["comparable"] is True
    assert comparison["coverage"] == {
        "before": 1, "after": 2, "added": ["empty-email"], "removed": [],
    }
    assert comparison["scenarios"][-1]["change"] == "added"
    assert comparison["scenarios"][-1]["before"] == "not_planned"
