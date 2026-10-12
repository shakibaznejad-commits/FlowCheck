import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient

from backend.executor import resolve_scenario_test_data
from backend.main import app
from backend.schemas import ExecutePlanRequest, TestPlan as FlowTestPlan
from backend.sensitive_data import sanitize_goal, sanitize_plan_credentials


def login_plan() -> FlowTestPlan:
    return FlowTestPlan.model_validate(
        {
            "flow": "login",
            "summary": "Check positive and negative login cases.",
            "scope": "broad",
            "tests": [
                {
                    "name": "Valid credentials",
                    "goal": "Verify valid credentials log in.",
                    "prerequisites": ["test_email", "test_password"],
                    "description": "Submit the valid account credentials.",
                    "steps": [
                        {"action": "navigate", "target": "login page", "value": None},
                        {"action": "fill", "target": "Email", "value": "{{test_email}}"},
                        {"action": "fill", "target": "Password", "value": "{{test_password}}"},
                        {"action": "submit", "target": "Log in", "value": None},
                    ],
                    "expected_result": "The account is authenticated.",
                    "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
                },
                {
                    "name": "Known email with wrong password",
                    "goal": "Verify the known email is rejected with a wrong password.",
                    "prerequisites": ["test_email", "test_password"],
                    "description": "Submit the authorized test email with a locally generated negative password; do not reuse the negative test password.",
                    "steps": [
                        {"action": "navigate", "target": "login page", "value": None},
                        {"action": "fill", "target": "Email", "value": "{{test_email}}"},
                        {"action": "fill", "target": "Password", "value": "{{test_password}}"},
                        {"action": "submit", "target": "Log in", "value": None},
                    ],
                    "expected_result": "The login remains rejected.",
                    "assertions": [{"kind": "status_visible", "timeout_ms": 1000}],
                },
            ],
        },
        strict=False,
    )


def test_login_plan_separates_reference_credentials_from_negative_inputs() -> None:
    safe_plan = sanitize_plan_credentials(login_plan())

    positive, negative = safe_plan.tests
    assert [step.value for step in positive.steps if step.action.value == "fill"] == [
        "{{valid_test_email}}",
        "{{valid_test_password}}",
    ]
    assert set(item.value for item in positive.prerequisites) == {
        "valid_test_email",
        "valid_test_password",
    }
    assert [step.value for step in negative.steps if step.action.value == "fill"] == [
        "{{valid_test_email}}",
        "{{negative_test_password}}",
    ]
    assert set(item.value for item in negative.prerequisites) == {
        "valid_test_email",
        "negative_test_password",
    }
    assert "do not reuse" not in negative.description


def test_empty_field_scenario_text_matches_the_credential_category() -> None:
    plan = login_plan().model_copy(update={"tests": [login_plan().tests[0]]})
    empty_email = plan.tests[0].model_copy(update={
        "name": "Empty email",
        "goal": "Leave Email empty, fill Password with a negative test value, and submit.",
        "description": "Use a negative test value in Password.",
    })
    empty_email.steps = [
        step for step in empty_email.steps
        if step.target != "Email"
    ]
    empty_email = empty_email.model_copy(update={
        "steps": [
            *empty_email.steps[:-1],
            empty_email.steps[-1],
        ]
    })
    empty_password = empty_email.model_copy(update={
        "name": "Empty password",
        "goal": "Fill Email with a negative test value, leave Password empty, and submit.",
        "description": "Use a negative test value in Email.",
        "steps": login_plan().tests[0].steps,
    })
    plan = plan.model_copy(update={"tests": [empty_email, empty_password]})

    safe_plan = sanitize_plan_credentials(plan)

    assert "negative test password" in safe_plan.tests[0].goal.lower()
    assert "valid test account email" in safe_plan.tests[1].goal.lower()
    assert "valid_test_password" not in safe_plan.tests[0].model_dump_json()
    assert "valid_test_email" in safe_plan.tests[1].model_dump_json()


def test_execute_request_accepts_separate_credential_categories() -> None:
    request = ExecutePlanRequest.model_validate(
        {
            "url": "https://example.com/login",
            "plan": {},
            "authorized_test_account": True,
            "test_data": {
                "valid_test_email": "qa@example.test",
                "valid_test_password": "CorrectHorseBatteryStaple!",
                "negative_test_email": "flowcheck@example.invalid",
                "negative_test_password": "generated-invalid-pass",
            },
        }
    )

    assert request.authorized_test_account is True
    assert set(request.test_data) == {
        "valid_test_email",
        "valid_test_password",
        "negative_test_email",
        "negative_test_password",
    }


@pytest.mark.parametrize(
    ("valid_key", "negative_key", "valid_value", "negative_value"),
    [
        ("valid_test_email", "negative_test_email", "qa@example.test", "qa@example.test"),
        ("valid_test_email", "negative_test_email", "qa@example.test", "QA@EXAMPLE.TEST"),
        ("valid_test_password", "negative_test_password", "same-secret-value", "same-secret-value"),
    ],
)
def test_execute_request_rejects_reusing_valid_values_as_negative_inputs(
    valid_key: str,
    negative_key: str,
    valid_value: str,
    negative_value: str,
) -> None:
    with pytest.raises(ValidationError, match="must differ"):
        ExecutePlanRequest.model_validate(
            {
                "url": "https://example.com/login",
                "plan": {},
                "test_data": {valid_key: valid_value, negative_key: negative_value},
            }
        )


def test_goal_redacts_password_assignment_before_planner_provider() -> None:
    secret = "NeverSendThisPassword123!"
    safe = sanitize_goal(f"Test login with password: {secret}")
    assert secret not in safe
    assert "[redacted]" in safe
    assert sanitize_goal("Leave Password empty and verify rejected login") == "Leave Password empty and verify rejected login"


def test_goal_redaction_preserves_plain_password_language_and_redacts_unicode_assignments() -> None:
    ordinary = "Password is an empty field; رمز عبور خالی است."
    assert sanitize_goal(ordinary) == ordinary

    secret = "رمز-محرمانه-۱۲۳"
    assigned = f"Password: RepeatMe123!; رمز عبور: {secret}; repeat RepeatMe123!"
    sanitized = sanitize_goal(assigned)
    assert "RepeatMe123!" not in sanitized
    assert secret not in sanitized
    assert sanitized.count("[redacted]") >= 3


def test_empty_login_cases_isolate_the_empty_field() -> None:
    base = login_plan()
    empty_email = base.tests[0].model_copy(update={
        "name": "Empty email",
        "goal": "Leave Email empty and verify rejection.",
        "description": "Leave Email empty, fill Password with a valid test password, and submit.",
        "expected_result": "The form rejects the empty email.",
    })
    empty_password = base.tests[1].model_copy(update={
        "name": "Empty password",
        "goal": "Leave Password empty and verify rejection.",
        "description": "Fill Email with a valid test email, leave Password empty, and submit.",
        "expected_result": "Password is empty.",
    })
    safe = sanitize_plan_credentials(base.model_copy(update={
        "tests": [empty_email, empty_password], "scope": "broad",
    }))
    assert {item.value for item in safe.tests[0].prerequisites} == {"negative_test_password"}
    assert all(step.target != "Email" for step in safe.tests[0].steps)
    assert "negative test password" in safe.tests[0].description
    assert "valid test password" not in safe.tests[0].description
    assert {item.value for item in safe.tests[1].prerequisites} == {"valid_test_email"}
    assert all(step.target != "Password" for step in safe.tests[1].steps)
    assert "valid test account email" in safe.tests[1].description
    assert "negative test email" not in safe.tests[1].description
    assert "Password is empty" in safe.tests[1].expected_result


def test_missing_negative_inputs_are_generated_locally_and_separately() -> None:
    negative = sanitize_plan_credentials(login_plan()).tests[1]
    data = resolve_scenario_test_data(
        negative,
        {"valid_test_email": "qa@example.test", "valid_test_password": "private-valid-pass"},
    )
    assert data["valid_test_email"] == "qa@example.test"
    assert data["negative_test_password"] != "private-valid-pass"
    assert "negative_test_email" not in data

    unknown = login_plan().tests[0].model_copy(update={
        "name": "Unknown email login",
        "goal": "Reject an unregistered email.",
        "description": "Submit an unknown email and invalid password.",
        "expected_result": "The login is rejected.",
    })
    unknown_plan = login_plan().model_copy(update={"tests": [unknown], "scope": "focused"})
    unknown_safe = sanitize_plan_credentials(unknown_plan).tests[0]
    unknown_data = resolve_scenario_test_data(unknown_safe, {})
    assert unknown_data["negative_test_email"].endswith("@example.invalid")
    assert "qa@example.test" not in unknown_data.values()


def test_execute_endpoint_requires_explicit_authorization_for_reference_account() -> None:
    plan = {
        "flow": "login",
        "scope": "focused",
        "summary": "Verify an authorized login.",
        "tests": [{
            "name": "Valid login",
            "goal": "Verify successful login with valid credentials.",
            "prerequisites": ["valid_test_email", "valid_test_password"],
            "description": "Use the authorized test account.",
            "steps": [
                {"action": "navigate", "target": "login page", "value": None},
                {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
                {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
                {"action": "submit", "target": "Log in", "value": None},
            ],
            "expected_result": "The account is authenticated.",
            "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
        }],
    }
    response = TestClient(app).post("/api/execute-plan", json={
        "url": "https://example.com/login",
        "plan": plan,
        "test_data": {
            "valid_test_email": "qa@example.test",
            "valid_test_password": "private-valid-pass",
        },
    })
    assert response.status_code == 422
    assert "authorized" in response.json()["detail"].lower()


def test_execute_endpoint_requires_reference_credentials_before_browser_start(monkeypatch) -> None:
    import backend.main as main

    monkeypatch.setattr(
        main,
        "execute_test_plan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("browser must not start")),
    )
    response = TestClient(app).post("/api/execute-plan", json={
        "url": "https://example.com/login",
        "authorized_test_account": True,
        "plan": {
            "flow": "login",
            "scope": "focused",
            "summary": "Verify authorized login.",
            "tests": [{
                "name": "Successful login",
                "goal": "Verify valid credentials log in.",
                "prerequisites": ["valid_test_email", "valid_test_password"],
                "description": "Use valid test account credentials.",
                "steps": [
                    {"action": "navigate", "target": "Login page", "value": None},
                    {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
                    {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
                    {"action": "submit", "target": "Log in", "value": None},
                ],
                "expected_result": "The login succeeds.",
                "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
            }],
        },
    })

    assert response.status_code == 422
    assert "credentials" in response.json()["detail"].lower()


def test_execute_endpoint_rejects_scenarios_without_assertions_before_browser_start(
    monkeypatch,
) -> None:
    import backend.main as main

    monkeypatch.setattr(
        main,
        "execute_test_plan",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("browser must not start")),
    )
    response = TestClient(app).post("/api/execute-plan", json={
        "url": "https://example.com/login",
        "plan": {
            "flow": "login",
            "scope": "focused",
            "summary": "Check empty email login rejection.",
            "tests": [{
                "name": "Empty email",
                "goal": "Leave email empty and verify rejection.",
                "prerequisites": [],
                "description": "Submit without an email.",
                "steps": [
                    {"action": "navigate", "target": "Login page", "value": None},
                    {"action": "submit", "target": "Log in", "value": None},
                ],
                "expected_result": "The required email is rejected.",
                "assertions": [],
            }],
        },
    })

    assert response.status_code == 422
    assert "verification" in response.json()["detail"].lower()
