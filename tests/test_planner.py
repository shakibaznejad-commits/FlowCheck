import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import httpx
import pytest

from backend.main import app
import backend.planner as planner
from backend.planner_prompt import PLANNER_SYSTEM_PROMPT
from backend.schemas import PlanScope, TestPlan as PlanSchema


def post_plan(payload: dict) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/api/test-plan", json=payload)

    return asyncio.run(send())


def valid_plan(flow: str) -> dict:
    return {
        "flow": flow,
        "summary": f"Check the {flow} form rejects invalid credentials.",
        "tests": [
            {
                "name": f"{flow.title()} with invalid credentials",
                "goal": "Verify invalid credentials are rejected.",
                "prerequisites": [],
                "description": "Submit invalid credentials and check for a clear error.",
                "steps": [
                    {"action": "navigate", "target": "target website URL", "value": None},
                    {"action": "fill", "target": "email field", "value": "invalid-email"},
                    {"action": "click", "target": "submit button", "value": None},
                ],
                "expected_result": "The form rejects the input and explains the problem.",
                "assertions": [
                    {
                        "text": "Invalid credentials",
                        "expectation": "visible",
                        "timeout_ms": 1500,
                    },
                    {
                        "text": "Login successful",
                        "expectation": "not_visible",
                        "timeout_ms": 1500,
                    },
                ],
            }
        ],
    }


def five_scenario_login_plan() -> dict:
    def case(
        name: str,
        goal: str,
        description: str,
        expected_result: str,
        prerequisites: list[str],
        fills: list[tuple[str, str]],
        assertion_kind: str,
        assertion_target: str | None = None,
    ) -> dict:
        steps = [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            *[
                {"action": "fill", "target": target, "value": value}
                for target, value in fills
            ],
            {"action": "submit", "target": "Log in", "value": None},
        ]
        return {
            "name": name,
            "goal": goal,
            "prerequisites": prerequisites,
            "description": description,
            "steps": steps,
            "expected_result": expected_result,
            "assertions": [
                {
                    "kind": assertion_kind,
                    "text": "",
                    "target": assertion_target,
                    "expectation": "visible",
                    "timeout_ms": 1000,
                }
            ],
        }

    plan = valid_plan("login")
    plan["tests"] = [
        case(
            "Successful login",
            "Verify successful login using the authorized test account.",
            "Submit the supplied valid test credentials.",
            "The login form becomes hidden and authenticated account content appears.",
            ["valid_test_email", "valid_test_password"],
            [("Email", "{{valid_test_email}}"), ("Password", "{{valid_test_password}}")],
            "login_form_hidden",
        ),
        case(
            "Known email with wrong password",
            "Verify a known email with an incorrect password is rejected.",
            "Use the known test account and a negative password.",
            "The login attempt is rejected and authenticated content stays inaccessible.",
            ["valid_test_email", "negative_test_password"],
            [("Email", "{{valid_test_email}}"), ("Password", "{{negative_test_password}}")],
            "login_rejected",
            "Password",
        ),
        case(
            "Unknown email",
            "Verify an unregistered email is rejected.",
            "Use a generated unregistered email and negative password.",
            "The login attempt is rejected and authenticated content stays inaccessible.",
            ["negative_test_email", "negative_test_password"],
            [("Email", "{{negative_test_email}}"), ("Password", "{{negative_test_password}}")],
            "login_rejected",
            "Email",
        ),
        case(
            "Empty email",
            "Verify login is rejected when the email field is empty.",
            "Submit with the email field left empty.",
            "The form indicates the missing email or rejects the login.",
            ["negative_test_password"],
            [("Password", "{{negative_test_password}}")],
            "login_rejected",
            "Email",
        ),
        case(
            "Empty password",
            "Verify login is rejected when the password field is empty.",
            "Submit with the password field left empty.",
            "The form indicates the missing password or rejects the login.",
            ["valid_test_email"],
            [("Email", "{{valid_test_email}}")],
            "login_rejected",
            "Password",
        ),
    ]
    return plan


def test_broad_login_plan_allows_six_scenarios_with_bounded_action_budget() -> None:
    payload = five_scenario_login_plan()
    logout = deepcopy(payload["tests"][0])
    logout.update({
        "name": "Logout invalidates the session",
        "goal": "Verify that logging out invalidates the authenticated session.",
        "description": "Log in with the authorized account, then log out.",
        "expected_result": "The protected workspace is inaccessible after logout.",
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
            {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
            {"action": "click", "target": "Log out", "value": None},
        ],
        "assertions": [
            {"kind": "login_form_visible", "target": None, "text": "", "expectation": "visible", "timeout_ms": 1000}
        ],
    })
    payload["tests"].append(logout)
    payload["scope"] = PlanScope.BROAD.value

    plan = PlanSchema.model_validate(payload, strict=False)

    assert len(plan.tests) == 6
    assert sum(len(item.steps) for item in plan.tests) == 23


def patch_openai(monkeypatch: pytest.MonkeyPatch, output: str) -> None:
    import backend.llm_provider as llm_provider

    class FakeOpenAI:
        def __init__(self, api_key: str, **kwargs: object) -> None:
            assert api_key == "test-key"

            def create(**request: object) -> SimpleNamespace:
                response_format = request["response_format"]
                assert response_format["type"] == "json_schema"
                assert response_format["json_schema"]["strict"] is True
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content=output))]
                )

            self.chat = SimpleNamespace(
                completions=SimpleNamespace(
                    create=create
                )
            )

    monkeypatch.setattr(llm_provider, "OpenAI", FakeOpenAI)


@pytest.mark.parametrize("flow", ["login", "signup"])
def test_plans_login_and_signup_goals(monkeypatch: pytest.MonkeyPatch, flow: str) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    plan = valid_plan(flow)
    patch_openai(monkeypatch, json.dumps(plan))

    response = post_plan(
        {"url": "https://example.com/account", "goal": f"Test {flow} with invalid credentials"}
    )

    assert response.status_code == 200
    assert response.json()["flow"] == flow
    assert response.json()["tests"][0]["name"] == plan["tests"][0]["name"]


def test_planner_replaces_generated_credentials_before_returning_them(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "FlowCheck-Private-Password-9472"
    plan = valid_plan("login")
    plan["tests"][0]["steps"][1]["value"] = secret
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    patch_openai(monkeypatch, json.dumps(plan))

    response = post_plan(
        {"url": "https://example.com/login", "goal": "Test login with wrong password"}
    )

    assert response.status_code == 200
    assert secret not in response.text
    assert response.json()["tests"][0]["steps"][1]["value"] == "{{negative_test_email}}"
    assert response.json()["tests"][0]["prerequisites"] == ["negative_test_email"]
    assert secret not in capsys.readouterr().out


def test_planner_repairs_missing_assertions_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    initial = valid_plan("login")
    initial["tests"][0].pop("assertions")
    repaired = valid_plan("login")
    outputs = [json.dumps(initial), json.dumps(repaired)]
    calls: list[dict] = []

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    response = post_plan({
        "url": "https://example.com/login",
        "goal": "Only test the empty-email case on the login page.",
    })

    assert response.status_code == 200, response.text
    assert len(calls) == 2
    assert response.json()["tests"][0]["assertions"]
    assert "verification" in calls[1]["user_message"].lower()
    assert response.json()["scope"] == "focused"
    assert len(response.json()["tests"]) == 1


def test_planner_rejects_a_plan_still_missing_verification_after_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    plan = valid_plan("login")
    plan["tests"][0]["assertions"] = []
    outputs = [json.dumps(plan), json.dumps(plan)]
    calls = 0

    class RepeatingProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            nonlocal calls
            calls += 1
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", RepeatingProvider)
    with pytest.raises(planner.PlannerOutputError, match="unverifiable or inconsistent"):
        planner.create_test_plan(
            "https://example.com/login",
            "Only test the empty-email case on the login page.",
        )
    assert calls == 2


def test_planner_repairs_empty_field_scenario_that_still_fills_the_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    initial = valid_plan("login")
    initial_test = initial["tests"][0]
    initial_test["name"] = "Reject empty email"
    initial_test["goal"] = "Leave Email empty and verify rejected login."
    initial_test["prerequisites"] = ["test_email", "test_password"]
    initial_test["steps"].insert(
        1, {"action": "fill", "target": "Email", "value": "{{test_email}}"}
    )
    repaired = valid_plan("login")
    repaired_test = repaired["tests"][0]
    repaired_test["name"] = "Reject empty email"
    repaired_test["goal"] = "Leave Email empty and verify rejected login."
    repaired_test["prerequisites"] = ["test_password"]
    repaired_test["steps"] = [
        {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
        {"action": "fill", "target": "Password", "value": "{{test_password}}"},
        {"action": "submit", "target": "Log in", "value": None},
    ]
    repaired_test["assertions"] = [
        {"kind": "control_invalid", "target": "Email", "timeout_ms": 1500},
    ]
    outputs = [json.dumps(initial), json.dumps(repaired)]
    calls: list[dict] = []

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    plan = planner.create_test_plan(
        "https://example.com/login",
        "Only test the empty-email case on the login page.",
    )

    assert len(calls) == 2
    assert "omit its fill action" in calls[1]["user_message"]
    assert plan.tests[0].prerequisites == [planner.PrerequisiteType.NEGATIVE_TEST_PASSWORD]
    assert all(step.target != "Email" for step in plan.tests[0].steps)
    assert {item.kind.value for item in plan.tests[0].assertions} == {"control_invalid"}


def test_broad_login_plan_repairs_missing_unknown_email_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    initial = valid_plan("login")
    wrong_password = deepcopy(initial["tests"][0])
    wrong_password.update(
        {
            "name": "Known email with wrong password",
            "goal": "Verify a known email is rejected with an incorrect password.",
            "description": "Submit the known test email with a wrong password.",
            "expected_result": "The login form remains visible and shows status feedback.",
        }
    )
    positive = deepcopy(initial["tests"][0])
    positive.update(
        {
            "name": "Successful login",
            "goal": "Verify an authorized test account can log in.",
            "description": "Use valid test account credentials.",
            "expected_result": "The login form becomes hidden.",
            "prerequisites": ["valid_test_email", "valid_test_password"],
            "steps": [
                {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
                {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
                {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
                {"action": "submit", "target": "Log in", "value": None},
            ],
            "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
        }
    )
    initial["tests"] = [positive, wrong_password]
    repaired = deepcopy(initial)
    unknown_email = deepcopy(initial["tests"][1])
    unknown_email.update(
        {
            "name": "Reject unregistered email",
            "goal": "Verify an unknown email is rejected.",
            "description": "Submit an unregistered test email and a supplied test password.",
            "expected_result": "The login form remains visible and shows status feedback.",
            "prerequisites": ["test_password"],
            "steps": [
                {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
                {"action": "fill", "target": "Email", "value": "{{test_email}}"},
                {"action": "fill", "target": "Password", "value": "{{test_password}}"},
                {"action": "submit", "target": "Log in", "value": None},
            ],
            "assertions": [
                {"kind": "login_form_visible", "timeout_ms": 1500},
                {"kind": "status_visible", "timeout_ms": 1500},
            ],
        }
    )
    repaired["tests"].append(unknown_email)
    outputs = [json.dumps(initial), json.dumps(repaired)]
    calls: list[dict] = []

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    plan = planner.create_test_plan(
        "https://example.com/login",
        "Test the login functionality of this website.",
    )

    assert len(calls) == 2
    assert "independent unregistered-email scenario" in calls[1]["user_message"]
    assert len(plan.tests) == 3
    assert any("unregistered" in test.name.casefold() for test in plan.tests)


def test_planner_prompt_avoids_guessing_unspecified_page_copy() -> None:
    assert "must not invent" in PLANNER_SYSTEM_PROMPT
    assert "status_visible" in PLANNER_SYSTEM_PROMPT
    assert "choose a short conventional rejection phrase" not in PLANNER_SYSTEM_PROMPT


def test_broad_login_coverage_requires_distinct_wrong_password_scenario() -> None:
    payload = valid_plan("login")
    valid_case = deepcopy(payload["tests"][0])
    valid_case.update(
        {
            "name": "Valid credentials",
            "goal": "Verify valid credentials are accepted.",
            "prerequisites": ["test_email", "test_password"],
            "description": "Use the supplied valid credentials.",
            "expected_result": "The login form becomes hidden.",
        }
    )
    unknown_case = deepcopy(payload["tests"][0])
    unknown_case.update(
        {
            "name": "Unregistered email",
            "goal": "Verify an unknown email is rejected.",
            "description": "Use an email known to be unregistered.",
            "expected_result": "The login form remains visible with status feedback.",
        }
    )
    payload.update({"scope": "broad", "tests": [valid_case, unknown_case]})
    plan = PlanSchema.model_validate(payload, strict=False)

    issues = planner._verification_issues(plan, planner.PlanScope.BROAD)

    assert "broad login coverage is missing a known-email/wrong-password scenario" in issues


def test_empty_field_scenarios_accept_rejection_assertion_for_matching_field() -> None:
    plan_data = valid_plan("login")
    test = plan_data["tests"][0]
    test.update({
        "name": "Empty email",
        "goal": "Leave the email field empty and verify login is rejected.",
        "description": "Submit with the email field empty.",
        "expected_result": "The login remains rejected.",
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "assertions": [
            {"kind": "login_rejected", "target": "Email", "timeout_ms": 500},
        ],
    })
    plan = PlanSchema.model_validate(plan_data, strict=False)

    issues = planner._verification_issues(plan, planner.PlanScope.FOCUSED)

    assert not any("control_invalid assertion for Email" in issue for issue in issues)


def test_empty_field_scenarios_reject_unrelated_form_assertions() -> None:
    plan_data = valid_plan("login")
    test = plan_data["tests"][0]
    test.update({
        "name": "Empty email",
        "goal": "Leave the email field empty and verify login is rejected.",
        "description": "Submit with the email field empty.",
        "expected_result": "The login remains rejected.",
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "fill", "target": "Password", "value": "{{negative_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "assertions": [{"kind": "login_form_visible", "timeout_ms": 500}],
    })
    plan = PlanSchema.model_validate(plan_data, strict=False)

    issues = planner._verification_issues(plan, planner.PlanScope.FOCUSED)

    assert "scenario 1 needs a control_invalid assertion for Email" in issues


def test_legacy_empty_field_plan_accepts_form_and_status_rejection_checks() -> None:
    plan_data = valid_plan("login")
    test = plan_data["tests"][0]
    test.update({
        "name": "Reject login with empty email",
        "goal": "Leave Email empty and verify login is rejected.",
        "description": "Check that form controls remain visible and status feedback appears.",
        "expected_result": "The login form remains visible and displays status feedback.",
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "assertions": [
            {"kind": "login_form_visible", "timeout_ms": 500},
            {"kind": "status_visible", "timeout_ms": 500},
        ],
    })
    plan = PlanSchema.model_validate(plan_data, strict=False)

    issues = planner._verification_issues(plan, planner.PlanScope.FOCUSED)

    assert not any("control_invalid assertion for Email" in issue for issue in issues)


def test_alternative_empty_field_outcome_does_not_become_conjunctive() -> None:
    plan_data = valid_plan("login")
    test = plan_data["tests"][0]
    test.update({
        "name": "Empty email",
        "goal": "Verify an empty email attempt is rejected.",
        "description": "Check native validity or application rejection.",
        "expected_result": "The field is invalid or visible rejection feedback appears.",
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "assertions": [
            {"kind": "login_rejected", "target": "Email", "timeout_ms": 500},
            {"kind": "control_invalid", "target": "Email", "timeout_ms": 500},
        ],
    })
    plan = PlanSchema.model_validate(plan_data, strict=False)

    normalized = planner._normalize_alternative_empty_field_checks(plan)

    assert [item.kind.value for item in normalized.tests[0].assertions] == ["login_rejected"]


def test_broad_login_coverage_requires_positive_login_prerequisites() -> None:
    payload = valid_plan("login")
    positive = deepcopy(payload["tests"][0])
    positive.update({
        "name": "Successful login",
        "goal": "Verify an authorized test account can log in.",
        "description": "Use valid test account credentials.",
        "prerequisites": ["valid_test_email", "valid_test_password"],
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
            {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "expected_result": "The login form becomes hidden.",
        "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
    })
    invalid_password = deepcopy(payload["tests"][0])
    invalid_password.update({
        "name": "Known email with wrong password",
        "goal": "Reject a known email paired with an incorrect password.",
        "description": "Use a known account and wrong password.",
        "expected_result": "The form remains visible with feedback.",
        "assertions": [
            {"kind": "login_form_visible", "timeout_ms": 1000},
            {"kind": "status_visible", "timeout_ms": 1000},
        ],
    })
    unknown = deepcopy(invalid_password)
    unknown.update({
        "name": "Unknown email",
        "goal": "Reject an unregistered email.",
        "description": "Use an unregistered email and a negative password.",
        "expected_result": "The form remains visible with feedback.",
        "assertions": [
            {"kind": "login_form_visible", "timeout_ms": 1000},
            {"kind": "status_visible", "timeout_ms": 1000},
        ],
    })
    plan = PlanSchema.model_validate({
        **payload,
        "scope": "broad",
        "tests": [invalid_password, unknown],
    }, strict=False)

    issues = planner._verification_issues(plan, planner.PlanScope.BROAD)

    assert "broad login coverage is missing a successful-login scenario with valid test-account prerequisites and a verified transition" in issues


def test_logout_scenario_is_not_misclassified_as_a_positive_login_case() -> None:
    plan = PlanSchema.model_validate(
        {
            "flow": "login",
            "summary": "Verify login and logout behavior.",
            "tests": [
                {
                    "name": "Logout after successful login",
                    "goal": "Verify the user can log in and then log out.",
                    "prerequisites": ["valid_test_email", "valid_test_password"],
                    "description": "Authenticate with the authorized account, then sign out.",
                    "steps": [
                        {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
                        {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
                        {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
                        {"action": "submit", "target": "Log in", "value": None},
                        {"action": "click", "target": "Log out", "value": None},
                    ],
                    "expected_result": "The session is invalidated and login is required again.",
                    "assertions": [{"kind": "login_form_visible", "timeout_ms": 1000}],
                }
            ],
        },
        strict=False,
    )

    issues = planner.scenario_verification_issues(plan.tests[0], plan.flow)

    assert not any("login_form_hidden assertion" in issue for issue in issues)


def test_broad_persian_login_coverage_is_recognized() -> None:
    payload = valid_plan("login")
    valid = deepcopy(payload["tests"][0])
    valid.update({
        "name": "ورود موفق",
        "goal": "ورود موفق با حساب تست مجاز را بررسی کن.",
        "description": "از ایمیل و رمز معتبر حساب تست استفاده کن.",
        "prerequisites": ["valid_test_email", "valid_test_password"],
        "steps": [
            {"action": "navigate", "target": "صفحه ورود", "value": "https://example.com/login"},
            {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
            {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "expected_result": "ورود موفق و فرم بسته می‌شود.",
        "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
    })
    wrong = deepcopy(payload["tests"][0])
    wrong.update({
        "name": "رمز عبور نادرست",
        "goal": "رمز عبور اشتباه برای ایمیل ثبت‌شده رد شود.",
        "description": "از ایمیل حساب موجود و رمز منفی استفاده کن.",
    })
    unknown = deepcopy(wrong)
    unknown.update({
        "name": "ایمیل ثبت‌نشده",
        "goal": "ایمیل ثبت‌نشده رد شود.",
        "description": "از یک ایمیل ثبت‌نشده استفاده کن.",
    })
    plan = PlanSchema.model_validate({
        **payload, "scope": "broad", "tests": [valid, wrong, unknown]
    }, strict=False)

    assert planner._verification_issues(plan, planner.PlanScope.BROAD) == []


def test_recognizes_logging_in_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv(
        "FLOWCHECK_TRUSTED_TEST_ORIGINS",
        "http://host.docker.internal:8081",
    )
    plan = valid_plan("login")
    patch_openai(monkeypatch, json.dumps(plan))

    response = post_plan(
        {
            "url": "http://host.docker.internal:8081/",
            "goal": (
                "Test that logging in with invalid credentials is rejected "
                "and an error message appears."
            ),
        }
    )

    assert response.status_code == 200
    assert response.json()["flow"] == "login"
    assert response.json()["tests"][0]["name"] == plan["tests"][0]["name"]


@pytest.mark.parametrize(
    "malformed_plan",
    [
        {"flow": "login", "summary": "Missing tests"},
        {
            **valid_plan("login"),
            "tests": [
                {
                    **valid_plan("login")["tests"][0],
                    "steps": [{"action": "run_javascript", "target": "page", "value": None}],
                }
            ],
        },
    ],
)
def test_rejects_malformed_model_output(
    monkeypatch: pytest.MonkeyPatch, malformed_plan: dict
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    patch_openai(monkeypatch, json.dumps(malformed_plan))

    response = post_plan(
        {"url": "https://example.com/login", "goal": "Test login with wrong password"}
    )

    assert response.status_code == 502
    assert "invalid structured plan" in response.json()["detail"].lower()


def test_missing_openai_key_returns_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    response = post_plan(
        {"url": "https://example.com/login", "goal": "Test login with wrong password"}
    )

    assert response.status_code == 503
    assert "LLM_API_KEY" in response.json()["detail"]


def test_rejects_unsupported_flow_before_calling_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.planner as planner

    monkeypatch.setenv("LLM_API_KEY", "test-key")
    provider_called = False

    class FakeProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            nonlocal provider_called
            provider_called = True
            return json.dumps(valid_plan("login"))

    monkeypatch.setattr(planner, "OpenAIProvider", FakeProvider)
    response = post_plan(
        {"url": "https://example.com/contact", "goal": "Submit a contact form"}
    )

    assert response.status_code == 422
    assert "login and signup" in response.json()["detail"].lower()
    assert not provider_called


@pytest.mark.parametrize(
    ("goal", "expected_scope"),
    [
        ("بخش لاگین سایت رو بررسی کن و نتیجه رو بهم بگو.", "broad"),
        ("Test the login functionality of this website and report any problems.", "broad"),
        ("فقط حالت ایمیل خالی رو در صفحه ورود بررسی کن.", "focused"),
        ("Only test the empty-email case on the login page.", "focused"),
        ("Please check it.", "ambiguous"),
    ],
)
def test_detects_adaptive_goal_scope(goal: str, expected_scope: str) -> None:
    detector = getattr(planner, "detect_scope", None)
    assert callable(detector), "Planner must expose a deterministic scope detector."

    assert detector(goal).value == expected_scope


def test_persian_broad_login_plan_is_bounded_and_has_stable_scenario_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    broad_plan = valid_plan("login")
    broad_plan["tests"][0]["name"] = "Known email with wrong password"
    broad_plan["tests"][0]["goal"] = "Reject a known email with a wrong password."
    broad_plan["tests"][0]["description"] = "Submit a known email with an incorrect password."
    second_test = deepcopy(broad_plan["tests"][0])
    second_test["name"] = "Login rejects an unregistered email"
    second_test["goal"] = "Reject an unregistered email."
    second_test["description"] = "Submit an unknown email and check that access is rejected."
    positive_test = deepcopy(broad_plan["tests"][0])
    positive_test.update({
        "name": "Successful login",
        "goal": "Verify successful login with an authorized test account.",
        "description": "Use the valid test account credentials.",
        "prerequisites": ["valid_test_email", "valid_test_password"],
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
            {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "expected_result": "The login form becomes hidden.",
        "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
    })
    broad_plan["tests"].extend([second_test, positive_test])
    patch_openai(monkeypatch, json.dumps(broad_plan))

    response = post_plan(
        {
            "url": "https://example.com/login",
            "goal": "بخش لاگین سایت رو بررسی کن و نتیجه رو بهم بگو.",
        }
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scope"] == "broad"
    assert 2 <= len(payload["tests"]) <= 6
    ids = [test["scenario_id"] for test in payload["tests"]]
    assert len(ids) == len(set(ids))


def test_plan_rejects_broad_scope_with_only_one_scenario() -> None:
    payload = valid_plan("login")
    payload["scope"] = "broad"

    with pytest.raises(ValueError, match="broad scope requires multiple scenarios"):
        PlanSchema.model_validate(payload, strict=False)


def test_plan_rejects_focused_scope_with_more_than_one_scenario() -> None:
    payload = valid_plan("login")
    payload["tests"].append(deepcopy(payload["tests"][0]))

    with pytest.raises(ValueError, match="focused scope requires exactly one scenario"):
        PlanSchema.model_validate(payload, strict=False)


@pytest.mark.parametrize(
    "goal",
    [
        (
        "Test the complete login functionality of this website. Identify relevant "
        "positive and negative scenarios, including successful login, invalid "
        "credentials, and empty fields. Verify the actual authenticated state, not just "
        "whether a button was clicked."
        ),
        "Exercise the whole sign-in flow: successful access, rejected bad credentials, and blank fields.",
        "عملکرد کامل ورود را با سناریوهای موفق و ناموفق، اطلاعات نامعتبر و فیلدهای خالی بررسی کن.",
        "همه حالت‌های صفحه ورود را بررسی کن؛ ورود موفق، رمز اشتباه و خالی بودن فیلدها را هم پوشش بده.",
    ],
)
def test_broad_login_with_explicit_categories_is_not_misclassified_as_focused(goal: str) -> None:
    assert planner.detect_scope(goal) == planner.PlanScope.BROAD


@pytest.mark.parametrize(
    "goal",
    [
        "Only test the empty-email case on the login page.",
        "فقط حالت ایمیل خالی را در صفحه ورود بررسی کن.",
        "Just check the wrong-password scenario for login.",
        "صرفاً سناریوی رمز اشتباه ورود را بررسی کن.",
    ],
)
def test_explicit_single_case_requests_remain_focused(goal: str) -> None:
    assert planner.detect_scope(goal) == planner.PlanScope.FOCUSED


def test_negated_just_does_not_turn_a_broad_goal_into_focused_scope() -> None:
    goal = (
        "Test the complete login functionality of this website. Include successful "
        "login, incorrect password, unknown email, empty email, empty password, and "
        "logout behavior. Verify the actual authenticated state, not just whether a "
        "button was clicked."
    )

    assert planner.detect_scope(goal) == planner.PlanScope.BROAD


def test_planner_repairs_broad_scope_with_single_scenario_without_dropping_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    initial = valid_plan("signup")
    corrected = deepcopy(initial)
    second = deepcopy(corrected["tests"][0])
    second.update({
        "name": "Reject duplicate account",
        "goal": "Verify an existing account cannot be registered again.",
        "description": "Check the signup response for an existing account.",
        "expected_result": "The signup form remains visible with rejection feedback.",
    })
    corrected["tests"].append(second)
    outputs = [json.dumps(initial), json.dumps(corrected)]
    calls: list[dict] = []

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    result = planner.create_test_plan(
        "https://example.com/signup",
        "Test signup functionality across all important steps.",
    )

    assert result.scope == planner.PlanScope.BROAD
    assert len(result.tests) == 2
    assert len(calls) == 2
    assert "broad scope requires 2 to 6 independent scenarios" in calls[1]["user_message"]


def test_broad_goal_repairs_to_five_independent_login_scenarios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    goal = (
        "Test the complete login functionality of this website. Include successful "
        "login, incorrect password, unknown email, empty email, empty password, and "
        "logout behavior. Verify the actual authenticated state, not just whether a "
        "button was clicked."
    )
    calls: list[dict] = []
    outputs = [json.dumps(valid_plan("login")), json.dumps(five_scenario_login_plan())]

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    result = planner.create_test_plan("https://example.com/login", goal)

    assert result.scope == planner.PlanScope.BROAD
    assert [item.name for item in result.tests] == [
        "Successful login",
        "Known email with wrong password",
        "Unknown email",
        "Empty email",
        "Empty password",
    ]
    assert len(calls) == 2
    assert "preserve every requested scenario category" in calls[1]["user_message"].casefold()


def test_planner_repairs_focused_scope_with_multiple_scenarios_to_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    multiple = valid_plan("login")
    extra = deepcopy(multiple["tests"][0])
    extra["name"] = "Unrequested extra check"
    multiple["tests"].append(extra)
    calls: list[dict] = []
    outputs = [json.dumps(multiple), json.dumps(valid_plan("login"))]

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    result = planner.create_test_plan(
        "https://example.com/login",
        "Only test login with invalid credentials.",
    )

    assert result.scope == planner.PlanScope.FOCUSED
    assert len(result.tests) == 1
    assert len(calls) == 2
    assert "focused scope requires exactly one scenario" in calls[1]["user_message"]


@pytest.mark.parametrize("invalid_response", ["{invalid json", '{"flow":"login","extra":"x"}'])
def test_planner_retries_malformed_or_schema_invalid_response_once(
    monkeypatch: pytest.MonkeyPatch,
    invalid_response: str,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    calls: list[dict] = []
    outputs = [invalid_response, json.dumps(valid_plan("login"))]

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)

    result = planner.create_test_plan(
        "https://example.com/login",
        "Only test login with invalid credentials.",
    )

    assert result.scope == planner.PlanScope.FOCUSED
    assert len(calls) == 2
    assert "previous response did not match the required schema" in calls[1]["user_message"]


def test_planner_allows_second_bounded_schema_repair_when_assertion_shape_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    invalid = valid_plan("login")
    invalid["tests"][0]["assertions"][0] = {
        "kind": "login_form_hidden",
        "text": "",
        "target": None,
        "expectation": "not_visible",
        "timeout_ms": 1000,
    }
    calls: list[dict] = []
    outputs = [json.dumps(invalid), json.dumps(invalid), json.dumps(valid_plan("login"))]

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)
    result = planner.create_test_plan(
        "https://example.com/login", "Test login with invalid credentials"
    )

    assert result.tests
    assert len(calls) == 3
    assert "login_form_hidden assertions use their structured visibility state" in calls[2]["user_message"]


def test_planner_scope_repair_does_not_include_sensitive_invalid_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    sensitive_value = "FlowCheck-Private-Password-9472"
    calls: list[dict] = []
    outputs = [
        json.dumps({"flow": "login", "summary": "bad", "tests": [], "debug": sensitive_value}),
        json.dumps(valid_plan("login")),
    ]

    class SequencedProvider:
        def __init__(self, **kwargs: object) -> None:
            pass

        def generate_json(self, **kwargs: object) -> str:
            calls.append(kwargs)
            return outputs.pop(0)

    monkeypatch.setattr(planner, "OpenAIProvider", SequencedProvider)

    result = planner.create_test_plan(
        "https://example.com/login",
        "Only test login with invalid credentials.",
    )

    assert result.flow == planner.FlowType.LOGIN
    assert sensitive_value not in calls[1]["user_message"]
    assert sensitive_value not in capsys.readouterr().out




@pytest.mark.parametrize(
    "goal",
    [
        "Test the complete login functionality, including successful login, invalid credentials, and empty fields.",
        "عملکرد کامل ورود را شامل ورود موفق، اطلاعات نامعتبر و فیلدهای خالی بررسی کن.",
    ],
)
def test_broad_planner_requires_every_explicitly_requested_login_category(goal: str) -> None:
    payload = valid_plan("login")
    positive = deepcopy(payload["tests"][0])
    positive.update({
        "name": "Successful login",
        "goal": "Verify successful login with an authorized test account.",
        "description": "Use the valid test account credentials.",
        "prerequisites": ["valid_test_email", "valid_test_password"],
        "steps": [
            {"action": "navigate", "target": "login page", "value": "https://example.com/login"},
            {"action": "fill", "target": "Email", "value": "{{valid_test_email}}"},
            {"action": "fill", "target": "Password", "value": "{{valid_test_password}}"},
            {"action": "submit", "target": "Log in", "value": None},
        ],
        "expected_result": "The login form becomes hidden.",
        "assertions": [{"kind": "login_form_hidden", "timeout_ms": 1000}],
    })
    wrong_password = deepcopy(payload["tests"][0])
    wrong_password.update({
        "name": "Known email with wrong password",
        "goal": "Reject a known email paired with an incorrect password.",
        "description": "Use a known account and wrong password.",
    })
    unknown_email = deepcopy(wrong_password)
    unknown_email.update({
        "name": "Unknown email",
        "goal": "Reject an unregistered email.",
        "description": "Use an unregistered email and a negative password.",
    })
    plan = PlanSchema.model_validate({
        **payload,
        "scope": "broad",
        "tests": [positive, wrong_password, unknown_email],
    }, strict=False)

    issues = planner._verification_issues(plan, planner.PlanScope.BROAD, goal=goal)

    assert "requested login coverage is missing an independent empty-email scenario" in issues
    assert "requested login coverage is missing an independent empty-password scenario" in issues
