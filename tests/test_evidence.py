import asyncio
from pathlib import Path

import httpx
import pytest

from backend.evidence import resolve_evidence_file
from backend.main import app
import backend.database as database
from backend.database import Session


RUN_ID = "a" * 32


def get_evidence(path: str) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path)

    return asyncio.run(send())


def test_serves_only_generated_screenshot_inside_evidence_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import backend.evidence as evidence

    image = tmp_path / RUN_ID / "test-01-final.png"
    image.parent.mkdir()
    image.write_bytes(b"png image")
    monkeypatch.setattr(evidence, "get_artifacts_root", lambda: tmp_path)
    with Session(database.engine) as db:
        db.add(
            database.TestRun(
                id="b" * 32,
                user_id=1,
                target_url="https://example.com",
                goal="Test an example flow",
                flow="login",
                status="failed",
                created_at=1,
                completed_at=1,
                plan_json={},
                execution_json={},
                analysis_json={},
                report_json={},
                evidence_run_id=RUN_ID,
            )
        )
        db.commit()

    response = get_evidence(f"/api/evidence/{RUN_ID}/test-01-final.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"png image"


@pytest.mark.parametrize(
    "filename",
    ["..", "../.env", "..%2F..%2F.env", "other.png"],
)
def test_rejects_path_traversal_and_unrecognized_filenames(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    filename: str,
) -> None:
    import backend.evidence as evidence

    monkeypatch.setattr(evidence, "get_artifacts_root", lambda: tmp_path)

    assert resolve_evidence_file(RUN_ID, filename) is None


def test_missing_screenshot_returns_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import backend.evidence as evidence

    monkeypatch.setattr(evidence, "get_artifacts_root", lambda: tmp_path)

    response = get_evidence(f"/api/evidence/{RUN_ID}/test-01-final.png")

    assert response.status_code == 404


def test_screenshot_capture_masks_form_inputs_and_supplied_credentials(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from backend import evidence

    class FakePage:
        def __init__(self) -> None:
            self.options = None

        def locator(self, selector: str):
            return ("locator", selector)

        def get_by_text(self, text: str, exact: bool = False):
            return ("text", text, exact)

        def screenshot(self, path: str, **options) -> None:
            self.options = options
            Path(path).write_bytes(b"png")

    page = FakePage()
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    evidence.capture_screenshot(
        page,
        run_id="a" * 32,
        filename="test-01-final.png",
        label="Final screenshot",
        test_index=0,
        step_index=None,
        secrets=("PrivateTestPassword123!",),
    )

    assert page.options["mask"] == [
        ("locator", "input, textarea"),
        ("text", "PrivateTestPassword123!", False),
    ]
    assert page.options["mask_color"] == "#333333"


def test_evidence_endpoint_rejects_encoded_path_traversal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import backend.evidence as evidence

    monkeypatch.setattr(evidence, "get_artifacts_root", lambda: tmp_path)

    response = get_evidence(f"/api/evidence/{RUN_ID}/..%2F..%2F.env")

    assert response.status_code == 404


def test_rejects_invalid_run_id(tmp_path: Path) -> None:
    assert resolve_evidence_file("..", "test-01-final.png") is None
