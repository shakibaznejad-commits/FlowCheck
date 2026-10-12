import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.main import app


def analyze(payload: dict) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/analyze-results", json=payload)

    return asyncio.run(send())


def analysis_payload(execution_status: str = "failed") -> dict:
    assertion_message = (
        'Assertion failed: expected text "Login successful" to be not visible. '
        'Observed visible DOM excerpt: "Member sign in Login successful"'
    )
    return {
        "goal": "Test login with an invalid password",
        "plan": {
            "flow": "login",
            "summary": "Check rejection of invalid login credentials.",
            "tests": [
                {
                    "name": "Login with invalid password",
                    "description": "Submit a wrong password.",
                    "steps": [
                        {"action": "navigate", "target": "login page", "value": None},
                        {"action": "fill", "target": "password field", "value": "wrong"},
                        {"action": "click", "target": "Log in", "value": None},
                    ],
                    "expected_result": "The login is rejected.",
                    "assertions": [
                        {
                            "text": "Login successful",
                            "expectation": "not_visible",
                            "timeout_ms": 1000,
                        }
                    ],
                }
            ],
        },
        "execution": {
            "status": execution_status,
            "run_id": "a" * 32,
            "evidence": [],
            "tests": [
                {
                    "name": "Login with invalid password",
                    "status": execution_status,
                    "steps": [
                        {
                            "action": "navigate",
                            "target": "login page",
                            "status": "passed",
                            "message": "Navigated to https://example.com/login",
                        },
                        {
                            "action": "fill",
                            "target": "password field",
                            "status": "passed",
                            "message": "Step completed successfully.",
                        },
                    ],
                    "assertions": [
                        {
                            "text": "Login successful",
                            "expectation": "not_visible",
                            "timeout_ms": 1000,
                            "status": execution_status,
                            "observed_text": "Member sign in Login successful",
                            "message": assertion_message,
                        }
                    ],
                }
            ],
        },
    }


def analysis_output() -> dict:
    assertion_message = (
        'Assertion failed: expected text "Login successful" to be not visible. '
        'Observed visible DOM excerpt: "Member sign in Login successful"'
    )
    return {
        "status": "failed",
        "summary": "The page showed a login success indication for invalid credentials.",
        "root_cause": "Invalid credentials were presented as a successful login.",
        "evidence": [assertion_message],
        "reproduction_steps": ["Open the login page", "Submit invalid credentials"],
        "recommendation": "Reject invalid credentials and display an error.",
    }


def patch_provider(monkeypatch: pytest.MonkeyPatch, output: str) -> list[dict]:
    import backend.analyzer as analyzer

    captured: list[dict] = []

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            captured.append(kwargs)
            return output

    monkeypatch.setattr(analyzer, "OpenAIProvider", FakeProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    return captured


def test_analyzes_browser_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = analysis_output()
    calls = patch_provider(monkeypatch, json.dumps(expected))

    response = analyze(analysis_payload())

    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in expected} == expected
    assert response.json()["analysis_source"] == "provider"
    assert calls and "login successful" in calls[0]["user_message"].lower()
    assert calls[0]["schema_name"] == "flowcheck_result_analysis"
    assert calls[0]["schema"]["required"] == [
        "status",
        "summary",
        "root_cause",
        "evidence",
        "reproduction_steps",
        "recommendation",
    ]
    assert "one flat JSON object" in calls[0]["system_prompt"]
    assert "no more than 10 evidence entries" in calls[0]["system_prompt"]
    assert "Do not return a `tests` array" in calls[0]["system_prompt"]
    assert "Observed visible DOM excerpt" in calls[0]["user_message"]
    logs = capsys.readouterr().out
    assert "ANALYZER REQUEST START" in logs
    assert "ANALYZER INPUT goal_present=True tests=1 execution_status=failed steps=2" in logs
    assert "ANALYZER PROVIDER REQUEST START" in logs
    assert "ANALYZER RESPONSE RECEIVED bytes=" in logs
    assert "========== VALIDATED ANALYZER RESPONSE ==========" in logs
    assert "status=failed" in logs


def test_provider_failure_returns_evidence_based_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import backend.analyzer as analyzer
    from backend.llm_provider import ProviderRequestError

    class FailedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            raise ProviderRequestError("provider unavailable")

    monkeypatch.setattr(analyzer, "OpenAIProvider", FailedProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["analysis_source"] == "fallback"
    logs = capsys.readouterr().out
    assert "========== ANALYZER PROVIDER ERROR ==========" in logs
    assert "ProviderRequestError" in logs
    assert "provider unavailable" in logs
    assert "ANALYZER FALLBACK" in logs


def test_provider_timeout_returns_evidence_based_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.analyzer as analyzer

    class TimeoutProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            raise TimeoutError("provider timed out")

    monkeypatch.setattr(analyzer, "OpenAIProvider", TimeoutProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["analysis_source"] == "fallback"
    assert response.json()["status"] == "failed"


def test_missing_provider_key_returns_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["analysis_source"] == "fallback"


def test_malformed_analysis_output_returns_fallback_after_bounded_repair(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "FlowCheck-Private-Analyzer-9927"
    patch_provider(
        monkeypatch,
        json.dumps({"status": "failed", "summary": secret}),
    )

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["analysis_source"] == "fallback"
    logs = capsys.readouterr().out
    assert "========== ANALYZER VALIDATION ERROR ==========" in logs
    assert secret not in logs


def test_failed_browser_result_is_still_analyzed(monkeypatch: pytest.MonkeyPatch) -> None:
    output = analysis_output()
    calls = patch_provider(monkeypatch, json.dumps(output))
    payload = analysis_payload("failed")

    response = analyze(payload)

    assert response.status_code == 200
    assert calls
    sent_request = json.loads(calls[0]["user_message"])
    assert (
        sent_request["execution"]["tests"][0]["assertions"][0]["observed_text"]
        == "Member sign in Login successful"
    )


def test_unverified_provider_evidence_returns_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = analysis_output()
    output["evidence"] = ["The page showed an invalid password message."]
    patch_provider(monkeypatch, json.dumps(output))

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["analysis_source"] == "fallback"
    logs = capsys.readouterr().out
    assert "========== ANALYZER BUSINESS RULE ERROR ==========" in logs
    assert "Analysis evidence was not present in browser observations." in logs


def test_returns_inconclusive_when_planned_assertion_was_not_executed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_provider(monkeypatch, json.dumps(analysis_output()))
    payload = analysis_payload("passed")
    payload["execution"]["tests"][0]["assertions"] = []

    response = analyze(payload)

    assert response.status_code == 200
    assert response.json()["status"] == "inconclusive"
    assert not calls


def test_returns_blocked_when_browser_could_not_reach_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_provider(monkeypatch, json.dumps(analysis_output()))
    payload = analysis_payload("failed")
    payload["execution"]["tests"][0]["assertions"] = []
    payload["execution"]["tests"][0]["steps"][1].update(
        status="failed", message='Could not find UI element "Password".'
    )

    response = analyze(payload)

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert not calls


def test_returns_inconclusive_if_a_planned_test_has_no_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = patch_provider(monkeypatch, json.dumps(analysis_output()))
    payload = analysis_payload("passed")
    payload["plan"]["scope"] = "broad"
    additional_plan_test = dict(payload["plan"]["tests"][0])
    additional_plan_test.update(
        name="Second check without verification",
        assertions=[],
    )
    payload["plan"]["tests"].append(additional_plan_test)
    payload["execution"]["tests"].append(
        {
            "name": additional_plan_test["name"],
            "status": "passed",
            "steps": [
                {
                    "action": "navigate",
                    "target": "login page",
                    "status": "passed",
                    "message": "Navigated to https://example.com/login",
                }
            ],
        }
    )

    response = analyze(payload)

    assert response.status_code == 200
    assert response.json()["status"] == "inconclusive"
    assert not calls


def test_provider_status_cannot_override_failed_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = analysis_output()
    output.update(status="passed", root_cause=None)
    patch_provider(monkeypatch, json.dumps(output))

    response = analyze(analysis_payload("failed"))

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["analysis_source"] == "fallback"


@pytest.mark.parametrize("bad_output", ["{truncated", json.dumps({"status": "failed"})])
def test_malformed_and_schema_mismatch_fall_back(
    monkeypatch: pytest.MonkeyPatch, bad_output: str
) -> None:
    calls = patch_provider(monkeypatch, bad_output)

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["analysis_source"] == "fallback"
    assert response.json()["status"] == "failed"
    assert len(calls) == 2


def test_provider_evidence_over_ten_items_falls_back_without_losing_failed_assertions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = analysis_output()
    output["evidence"] = [output["evidence"][0]] + [
        f"extra observation {index}" for index in range(13)
    ]
    patch_provider(monkeypatch, json.dumps(output))

    response = analyze(analysis_payload())

    assert response.status_code == 200
    assert response.json()["analysis_source"] == "fallback"
    assert analysis_payload()["execution"]["tests"][0]["assertions"][0]["message"] in response.json()["evidence"]


def test_mixed_multi_scenario_results_remain_independent_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_provider(monkeypatch, "not json")
    payload = analysis_payload()
    payload["plan"]["scope"] = "broad"
    first = payload["plan"]["tests"][0]
    first["scenario_id"] = "scenario-fail"
    second = json.loads(json.dumps(first))
    second.update(name="Valid login", scenario_id="scenario-pass")
    payload["plan"]["tests"].append(second)
    second_execution = json.loads(json.dumps(payload["execution"]["tests"][0]))
    second_execution.update(name="Valid login", scenario_id="scenario-pass")
    second_execution["assertions"][0].update(
        status="passed", message="Expected login result was observed."
    )
    for execution in (payload["execution"]["tests"][0], second_execution):
        execution["steps"].append({
            "action": "click",
            "target": "Log in",
            "status": "passed",
            "message": "Step completed successfully.",
        })
    payload["execution"]["tests"][0]["scenario_id"] = "scenario-fail"
    payload["execution"]["tests"].append(second_execution)

    response = analyze(payload)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["analysis_source"] == "fallback"
    assert payload["execution"]["tests"][0]["assertions"][0]["message"] in response.json()["evidence"]


def test_fallback_report_saves_scenario_results_to_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patch_provider(monkeypatch, "not json")

    async def run_pipeline() -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            payload = analysis_payload()
            payload["execution"]["evidence"] = [{
                "type": "screenshot", "label": "Failure", "filename": "test-01-failure.png",
                "test_index": 0, "step_index": 2,
            }]
            analysis = await client.post("/api/analyze-results", json=payload)
            report_payload = {
                "url": "https://example.com/login",
                "goal": payload["goal"],
                "plan": payload["plan"],
                "execution": payload["execution"],
                "analysis": analysis.json(),
            }
            report = await client.post("/api/generate-report", json=report_payload)
            history = await client.get(f"/api/runs/{payload['execution']['run_id']}")
            return analysis, report, history

    analysis, report, history = asyncio.run(run_pipeline())

    assert analysis.status_code == 200, analysis.text
    assert report.status_code == 200, report.text
    assert history.status_code == 200, history.text
    assert report.json()["narrative_source"] == "fallback"
    assert report.json()["scenario_results"][0]["status"] == "fail"
    assert report.json()["screenshot_evidence"][0]["filename"] == "test-01-failure.png"
    assert history.json()["analysis"]["analysis_source"] == "fallback"
    assert history.json()["report"]["scenario_results"][0]["status"] == "fail"
