import asyncio

import httpx
import pytest

from backend.auth import get_current_user
from backend.database import User
from backend.main import app
from backend import database
from backend.ai_usage import UsageLedger


def send(path: str, payload: dict | None = None) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, json=payload)

    return asyncio.run(request())


PLAN = {
    "flow": "login",
    "summary": "Check login behavior.",
    "scope": "focused",
    "tests": [{
        "scenario_id": "login-check",
        "name": "Login check",
        "goal": "Check login.",
        "prerequisites": [],
        "description": "Open login.",
        "steps": [{"action": "navigate", "target": "login page", "value": "https://example.com/login"}],
        "expected_result": "The page loads.",
        "assertions": [],
    }],
}


def test_unapproved_user_is_denied_on_all_ai_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLOWCHECK_AI_APPROVED_USER_IDS", "2")
    execution = {"status": "failed", "run_id": "a" * 32, "tests": [], "evidence": []}
    analysis = {
        "status": "inconclusive", "summary": "No verified result.", "root_cause": None,
        "evidence": [], "reproduction_steps": ["Open the page"],
        "recommendation": "Add a verifiable assertion.", "analysis_source": "fallback",
    }
    requests = [
        ("/api/test-plan", {"url": "https://example.com/login", "goal": "Test login."}),
        ("/api/execute-plan", {"url": "https://example.com/login", "plan": PLAN}),
        ("/api/analyze-results", {"goal": "Test login.", "plan": PLAN, "execution": execution}),
        ("/api/generate-report", {
            "url": "https://example.com/login", "goal": "Test login.", "plan": PLAN,
            "execution": execution, "analysis": analysis,
        }),
    ]
    for path, body in requests:
        response = send(path, body)
        assert response.status_code == 403, (path, response.status_code, response.text)


def test_ai_allowlist_defaults_to_deny(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOWCHECK_AI_APPROVED_USER_IDS", raising=False)
    response = send("/api/test-plan", {"url": "https://example.com/login", "goal": "Test login."})
    assert response.status_code == 403


@pytest.mark.parametrize("allowlist", ["1,not-a-number", "0", "1,,2", "-3"])
def test_malformed_allowlist_fails_closed(monkeypatch: pytest.MonkeyPatch, allowlist: str) -> None:
    monkeypatch.setenv("FLOWCHECK_AI_APPROVED_USER_IDS", allowlist)
    response = send("/api/test-plan", {"url": "https://example.com/login", "goal": "Test login."})
    assert response.status_code == 403


def test_approved_browser_run_is_accounted_atomically_and_quota_returns_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.main as main
    from backend.schemas import ExecutionResult

    monkeypatch.setenv("FLOWCHECK_AI_APPROVED_USER_IDS", "1")
    monkeypatch.setenv("FLOWCHECK_AI_DAILY_EXECUTIONS", "1")
    monkeypatch.setattr(main, "scenario_verification_issues", lambda _test, _flow: [])
    calls = []

    def fake_execute(**kwargs):
        calls.append(kwargs)
        return ExecutionResult.model_validate({
            "status": "failed", "run_id": "b" * 32, "tests": [], "evidence": [],
            "effective_plan": PLAN,
        }, strict=False)

    monkeypatch.setattr(main, "execute_test_plan", fake_execute)
    body = {"url": "https://example.com/login", "plan": PLAN}
    first = send("/api/execute-plan", body)
    second = send("/api/execute-plan", body)

    assert first.status_code == 200, first.text
    assert second.status_code == 429
    assert len(calls) == 1
    assert calls[0]["user_id"] == 1
    usage = UsageLedger(database.engine).snapshot(1)
    assert usage.execution_started == 1
    assert usage.execution_completed == 1


def test_newly_registered_user_does_not_receive_ai_access(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.auth import get_current_user

    monkeypatch.delenv("FLOWCHECK_AI_APPROVED_USER_IDS", raising=False)
    app.dependency_overrides.pop(get_current_user, None)
    async def signup_then_plan() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            signup = await client.post("/api/auth/signup", json={
                "email": "new-user@example.com", "password": "VeryStrongPass123!"
            })
            response = await client.post("/api/test-plan", json={
                "url": "https://example.com/login", "goal": "Test login."
            })
            return signup, response

    signup, response = asyncio.run(signup_then_plan())
    assert signup.status_code == 201, signup.text
    assert response.status_code == 403
