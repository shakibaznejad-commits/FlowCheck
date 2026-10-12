import json

import pytest

from backend.investigator import InvestigatorDecisionError, decide_next_action
from backend.schemas import TestExecutionResult as FlowTestExecutionResult


def sample_execution() -> FlowTestExecutionResult:
    return FlowTestExecutionResult.model_validate(
        {
            "name": "Login check",
            "status": "passed",
            "steps": [
                {
                    "action": "navigate",
                    "target": "login page",
                    "status": "passed",
                    "message": "Navigated to https://example.com/login",
                }
            ],
            "assertions": [],
            "observations": [
                {
                    "step_index": 0,
                    "action": "navigate",
                    "url": "https://example.com/login",
                    "title": "Login",
                    "visible_text": "Email Password Log in",
                    "controls": [],
                    "buttons": [],
                    "alerts": [],
                    "navigation_changes": [],
                    "console_errors": [],
                    "failed_requests": [],
                    "http_errors": [],
                    "evidence": ["Visible page text excerpt: \"Email Password Log in\""],
                }
            ],
        },
        strict=False,
    )


def patch_provider(monkeypatch: pytest.MonkeyPatch, response: dict) -> None:
    import backend.investigator as investigator

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            assert kwargs["schema_name"] == "flowcheck_investigation_decision"
            return json.dumps(response)

    monkeypatch.setattr(investigator, "OpenAIProvider", FakeProvider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")


def test_accepts_structured_decision_with_observed_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "decision": "inspect",
        "rationale": "Inspecting again will confirm the rendered login controls.",
        "evidence": ["Visible page text excerpt: \"Email Password Log in\""],
        "actions": [],
        "new_test": None,
    }
    patch_provider(monkeypatch, response)

    decision = decide_next_action(
        goal="Check the login form.",
        test=sample_execution(),
        remaining_tests=2,
        remaining_actions=10,
    )

    assert decision.decision.value == "inspect"
    assert decision.evidence == response["evidence"]


def test_rejects_decision_that_cites_unobserved_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "decision": "continue",
        "rationale": "The form loaded; continue the planned check.",
        "evidence": ["The password was accepted."],
        "actions": [],
        "new_test": None,
    }
    patch_provider(monkeypatch, response)

    with pytest.raises(InvestigatorDecisionError, match="not observed"):
        decide_next_action(
            goal="Check the login form.",
            test=sample_execution(),
            remaining_tests=2,
            remaining_actions=10,
        )


def test_rejects_arbitrary_generated_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "decision": "alternative_path",
        "rationale": "Use the visible login button.",
        "evidence": ["Visible page text excerpt: \"Email Password Log in\""],
        "actions": [
            {"action": "run_javascript", "target": "page", "value": "alert(1)"}
        ],
        "new_test": None,
    }
    patch_provider(monkeypatch, response)

    with pytest.raises(InvestigatorDecisionError, match="decision schema"):
        decide_next_action(
            goal="Check the login form.",
            test=sample_execution(),
            remaining_tests=2,
            remaining_actions=10,
        )


def test_new_scenario_uses_required_provider_schema_and_backend_generated_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "decision": "new_test",
        "rationale": "A bounded page check will add evidence to the requested test.",
        "evidence": ["Visible page text excerpt: \"Email Password Log in\""],
        "actions": [],
        "new_test": {
            "name": "Visible login form",
            "goal": "Verify the login form is rendered.",
            "prerequisites": [],
            "description": "Open the page and inspect its visible form.",
            "steps": [{"action": "navigate", "target": "login page", "value": None}],
            "expected_result": "The login form is visible.",
            "assertions": [],
        },
    }
    patch_provider(monkeypatch, response)

    decision = decide_next_action(
        goal="Test the login form.",
        test=sample_execution(),
        remaining_tests=1,
        remaining_actions=4,
    )

    assert decision.new_test is not None
    assert decision.new_test.scenario_id.startswith("scenario-")
