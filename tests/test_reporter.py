import asyncio
import json

import httpx
import pytest

from backend.main import app

RUN_ID = "a" * 32
SCREENSHOT_EVIDENCE = {
    "type": "screenshot",
    "label": "Failure after Email lookup",
    "filename": "test-01-failure.png",
    "test_index": 0,
    "step_index": 1,
}


def generate_report(payload: dict) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/generate-report", json=payload)

    return asyncio.run(send())


def report_payload(
    execution_status: str = "failed",
    analysis_status: str = "failed",
) -> dict:
    observed_message = (
        'Assertion failed: expected text "Login successful" to be not visible. '
        'Observed visible DOM excerpt: "Member sign in Login successful"'
    )
    return {
        "url": "https://example.com/login",
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
            "run_id": RUN_ID,
            "evidence": [SCREENSHOT_EVIDENCE],
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
                    "assertions": (
                        []
                        if analysis_status == "blocked"
                        else [
                            {
                                "text": "Login successful",
                                "expectation": "not_visible",
                                "timeout_ms": 1000,
                                "status": "failed",
                                "observed_text": "Member sign in Login successful",
                                "message": observed_message,
                            }
                        ]
                    ),
                }
            ],
        },
        "analysis": {
            "status": analysis_status,
            "summary": "The page showed a login success indication for invalid credentials." if analysis_status == "failed" else "The login outcome could not be verified.",
            "root_cause": "Invalid credentials were presented as a successful login." if analysis_status == "failed" else None,
            "evidence": [observed_message] if analysis_status == "failed" else [],
            "reproduction_steps": ["Open the login page", "Submit invalid credentials"],
            "recommendation": "Reject invalid credentials and display an error." if analysis_status == "failed" else "Add a structured visible-text assertion and rerun.",
        },
    }


def valid_report() -> dict:
    return {
        "status": "failed",
        "title": "Invalid login was reported as successful",
        "flow": "login",
        "expected_results": ["The login is rejected."],
        "actual_result": "The page showed a login success indication for invalid credentials.",
        "root_cause": "Invalid credentials were presented as a successful login.",
        "evidence": [
            'Assertion failed: expected text "Login successful" to be not visible. '
            'Observed visible DOM excerpt: "Member sign in Login successful"'
        ],
        "severity": "high",
        "reproduction_steps": ["Open the login page", "Submit invalid credentials"],
        "recommendation": "Reject invalid credentials and display an error.",
        "run_id": RUN_ID,
        "screenshot_evidence": [SCREENSHOT_EVIDENCE],
    }


def patch_provider(monkeypatch: pytest.MonkeyPatch, content: str) -> list[dict]:
    import backend.reporter as reporter

    calls: list[dict] = []

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return content

    monkeypatch.setattr(reporter, "OpenAIProvider", FakeProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    return calls


def test_generates_report_from_failed_browser_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = valid_report()
    calls = patch_provider(monkeypatch, json.dumps(expected))

    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert {key: response.json()[key] for key in expected} == expected
    assert len(response.json()["scenario_results"]) == 1
    assert response.json()["scenario_results"][0]["status"] == "fail"
    assert calls[0]["schema_name"] == "flowcheck_final_report"
    assert "scenario_results" not in calls[0]["schema"]["properties"]
    assert "structured report" in calls[0]["system_prompt"].lower()
    assert "JSON array" in calls[0]["system_prompt"]


def test_reporter_logs_do_not_expose_report_content(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "FlowCheck-Private-Report-3381"
    output = valid_report()
    output["actual_result"] = secret
    patch_provider(monkeypatch, json.dumps(output))
    payload = report_payload()
    payload["analysis"]["summary"] = secret

    response = generate_report(payload)

    assert response.status_code == 200
    assert secret in response.text
    assert secret not in capsys.readouterr().out


def test_generates_blocked_report(monkeypatch: pytest.MonkeyPatch) -> None:
    output = valid_report()
    output.update(
        status="blocked",
        title="Login outcome could not be verified",
        actual_result="The login outcome could not be verified.",
        root_cause=None,
        evidence=[],
        recommendation="Add a structured visible-text assertion and rerun.",
        severity="not_assessed",
    )
    patch_provider(monkeypatch, json.dumps(output))

    response = generate_report(report_payload("passed", "blocked"))

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "blocked"


def test_reporter_preserves_screenshot_evidence_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = valid_report()
    patch_provider(monkeypatch, json.dumps(expected))

    response = generate_report(report_payload())

    assert response.status_code == 200
    assert response.json()["run_id"] == RUN_ID
    assert response.json()["screenshot_evidence"] == [SCREENSHOT_EVIDENCE]


def test_provider_failure_returns_deterministic_report(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.reporter as reporter

    class FailedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(reporter, "OpenAIProvider", FailedProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert response.json()["narrative_source"] == "fallback"
    assert response.json()["status"] == "failed"
    assert response.json()["screenshot_evidence"] == [SCREENSHOT_EVIDENCE]
    assert response.json()["scenario_results"][0]["status"] == "fail"
    assert "unavailable" in response.json()["recommendation"].lower()


@pytest.mark.parametrize("output", ["{not-json", '{"status":"failed"}'])
def test_rejects_malformed_or_schema_mismatched_provider_output(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    patch_provider(monkeypatch, output)

    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert response.json()["narrative_source"] == "fallback"
    assert response.json()["status"] == "failed"


def test_reporter_retries_invalid_json_once_then_uses_validated_provider_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.reporter as reporter

    outputs = ["{\"status\": \"failed\"} {\"status\": \"failed\"}", json.dumps(valid_report())]
    calls: list[dict] = []

    class RepairProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(reporter, "OpenAIProvider", RepairProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert response.json()["narrative_source"] == "provider"
    assert len(calls) == 2
    assert "strict json validation" in calls[1]["system_prompt"].lower()


def test_reporter_falls_back_after_repeated_multiple_json_objects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.reporter as reporter

    malformed = '{"status":"failed"} {"status":"passed"}'
    calls: list[dict] = []

    class MalformedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return malformed

    monkeypatch.setattr(reporter, "OpenAIProvider", MalformedProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    response = generate_report(report_payload())

    assert response.status_code == 200
    assert len(calls) == 2
    assert response.json()["narrative_source"] == "fallback"
    assert response.json()["status"] == "failed"
    assert response.json()["screenshot_evidence"] == [SCREENSHOT_EVIDENCE]
    assert response.json()["scenario_results"][0]["status"] == "fail"


def test_rejects_invented_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    output = valid_report()
    output["evidence"] = ["The page displayed an invalid password warning."]
    patch_provider(monkeypatch, json.dumps(output))

    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert response.json()["narrative_source"] == "fallback"


def test_rejects_report_status_that_claims_pass_when_analysis_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = valid_report()
    output.update(status="passed", severity="info")
    patch_provider(monkeypatch, json.dumps(output))

    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert response.json()["narrative_source"] == "fallback"


def test_missing_provider_key_returns_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    response = generate_report(report_payload())

    assert response.status_code == 200, response.text
    assert response.json()["narrative_source"] == "fallback"
    assert response.json()["status"] == "failed"


def test_inconclusive_report_has_no_assessed_defect_severity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    response = generate_report(report_payload("passed", "inconclusive"))

    assert response.status_code == 200, response.text
    assert response.json()["severity"] == "not_assessed"


def test_benign_native_validation_mismatch_is_not_high_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    payload = report_payload()
    planned = payload["plan"]["tests"][0]
    planned.update({
        "name": "Empty email field",
        "goal": "Verify empty email is rejected.",
        "description": "Submit with Email empty.",
        "expected_result": "The empty email is rejected.",
        "assertions": [{
            "kind": "control_invalid", "text": "", "target": "Email",
            "expectation": "visible", "timeout_ms": 500,
        }],
    })
    payload["execution"]["tests"][0].update({
        "name": "Empty email field",
        "assertions": [{
            "kind": "control_invalid", "text": "", "target": "Email",
            "expectation": "visible", "timeout_ms": 500, "status": "failed",
            "observed_text": "",
            "message": 'Assertion failed: expected form control "Email" to be invalid. Observed browser validity: valid=True, aria-invalid=False, required=False, validation message="".',
        }],
    })
    payload["analysis"].update({
        "status": "failed", "summary": "The Email control was not marked invalid.",
        "root_cause": None, "evidence": [],
        "recommendation": "Confirm whether browser-native or application feedback is expected.",
    })
    response = generate_report(payload)
    assert response.status_code == 200, response.text
    assert response.json()["severity"] == "medium"
    assert response.json()["scenario_results"][0]["status"] == "fail"


def test_confirmed_unexpected_login_success_remains_high_severity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    response = generate_report(report_payload())
    assert response.status_code == 200, response.text
    assert response.json()["severity"] == "high"
