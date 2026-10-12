from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import executor
from backend.schemas import ActionType, AgentDecision, PlannedStep, TestPlan as FlowTestPlan


def login_plan() -> FlowTestPlan:
    return FlowTestPlan.model_validate(
        {
            "flow": "login",
            "summary": "Try an invalid login.",
            "tests": [
                {
                    "name": "Invalid login",
                    "description": "Find and fill the email field.",
                    "steps": [
                        {"action": "navigate", "target": "Login page", "value": None},
                        {"action": "fill", "target": "Email", "value": "invalid@example.com"},
                    ],
                    "expected_result": "The user remains signed out.",
                }
            ],
        },
        strict=False,
    )


class EmptyLocator:
    first = None

    def count(self) -> int:
        return 0


class FakePage:
    def __init__(self, screenshot_fails: bool = False) -> None:
        self.screenshot_fails = screenshot_fails
        self.screenshots: list[Path] = []

    def goto(self, *args: object, **kwargs: object) -> None:
        pass

    def get_by_label(self, *args: object, **kwargs: object) -> EmptyLocator:
        return EmptyLocator()

    get_by_role = get_by_label
    get_by_placeholder = get_by_label
    get_by_text = get_by_label

    def screenshot(self, path: str, **kwargs: object) -> None:
        if self.screenshot_fails:
            raise RuntimeError("screenshot unavailable")
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"png")
        self.screenshots.append(destination)


def install_fake_browser(
    monkeypatch: pytest.MonkeyPatch,
    page: FakePage,
) -> None:
    browser = SimpleNamespace(
        new_context=lambda **kwargs: SimpleNamespace(
            new_page=lambda: page,
            close=lambda: None,
        ),
        close=lambda: None,
    )
    playwright = SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: browser))

    class FakePlaywrightContext:
        def __enter__(self) -> SimpleNamespace:
            return playwright

        def __exit__(self, *args: object) -> None:
            pass

    monkeypatch.setattr(executor, "sync_playwright", FakePlaywrightContext)


def test_captures_failure_and_final_screenshot_with_step_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert result.status.value == "failed"
    assert len(result.run_id) == 32
    assert [item.filename for item in result.evidence] == [
        "test-01-failure.png",
        "test-01-final.png",
    ]
    assert result.evidence[0].test_index == 0
    assert result.evidence[0].step_index == 1
    assert result.evidence[1].test_index == 0
    assert result.evidence[1].step_index is None
    assert all(path.is_file() for path in page.screenshots)


def test_300_second_deadline_preserves_completed_scenario_results_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    clock = {"now": 0.0}
    monkeypatch.setattr(executor.time, "monotonic", lambda: clock["now"])
    original_capture = executor.capture_screenshot

    def capture_then_expire(page, *, filename: str, **kwargs):
        captured = original_capture(page, filename=filename, **kwargs)
        if filename == "test-01-final.png":
            clock["now"] = 301.0
        return captured

    monkeypatch.setattr(executor, "capture_screenshot", capture_then_expire)
    plan = login_plan().model_copy(deep=True)
    plan.scope = "broad"
    second = plan.tests[0].model_copy(update={
        "scenario_id": "second-login-check", "name": "Second login check"
    })
    plan.tests.append(second)

    result = executor.execute_test_plan(
        "https://example.com/login", plan, execution_deadline_seconds=300
    )

    assert len(result.tests) == 2
    assert result.tests[0].scenario_status.value != "not_run"
    assert result.tests[1].scenario_status.value == "not_run"
    assert [item.filename for item in result.evidence] == [
        "test-01-failure.png", "test-01-final.png"
    ]
    assert all(path.is_file() for path in page.screenshots)


def test_capture_failure_does_not_change_browser_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    page = FakePage(screenshot_fails=True)
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert result.status.value == "failed"
    assert result.tests[0].steps[-1].message == 'Could not find UI element "Email".'
    assert result.evidence == []
    assert "SCREENSHOT CAPTURE FAILED" in capsys.readouterr().out


def test_login_submit_target_resolves_log_in_and_sign_in_label_variants() -> None:
    class SignInLocator:
        first = "sign-in-button"

        def count(self) -> int:
            return 1

    class LoginPage:
        def get_by_label(self, *args: object, **kwargs: object) -> EmptyLocator:
            return EmptyLocator()

        def get_by_role(self, role: str, name: str, **kwargs: object):
            if role == "button" and name.casefold() == "sign in":
                return SignInLocator()
            return EmptyLocator()

        get_by_placeholder = get_by_label
        get_by_text = get_by_label

    result = executor._find_element(LoginPage(), "Log in")

    assert result == "sign-in-button"


@pytest.mark.parametrize(
    ("category", "supplied_value"),
    [
        ("valid_test_email", "qa@example.invalid"),
        ("valid_test_password", "dummy-password"),
        ("negative_test_email", "flowcheck@example.invalid"),
        ("negative_test_password", "dummy-negative-password"),
    ],
)
def test_fill_resolves_bare_test_data_category(
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    supplied_value: str,
) -> None:
    filled: list[str] = []

    class CaptureLocator:
        def fill(self, value: str, **_kwargs: object) -> None:
            filled.append(value)

    monkeypatch.setattr(executor, "_find_element", lambda *_args: CaptureLocator())
    step = PlannedStep(action=ActionType.FILL, target="Email", value=category)

    result = executor._execute_step(
        object(),
        step,
        "https://example.invalid",
        test_data={category: supplied_value},
    )

    assert result.status.value == "passed"
    assert filled == [supplied_value]


def test_captures_final_screenshot_after_successful_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = login_plan().model_copy(
        update={
            "tests": [login_plan().tests[0].model_copy(update={"steps": [login_plan().tests[0].steps[0]]})]
        }
    )
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))

    result = executor.execute_test_plan("https://example.com/login", plan)

    assert result.status.value == "passed"
    assert [item.filename for item in result.evidence] == ["test-01-final.png"]
    assert result.evidence[0].step_index is None


def test_navigate_uses_absolute_target_url_when_value_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NavigationPage:
        destination: str | None = None

        def goto(self, url: str, **kwargs: object) -> None:
            self.destination = url

    page = NavigationPage()
    destination = "https://example.com/login?demoMode=ambiguous"
    monkeypatch.setattr(executor, "validate_target_url", lambda url, **kwargs: url)
    step = SimpleNamespace(
        action=executor.ActionType.NAVIGATE,
        target=destination,
        value=None,
    )

    result = executor._execute_step(
        page=page,
        step=step,
        default_url="https://example.com/login",
    )

    assert result.status == executor.StepExecutionStatus.PASSED
    assert page.destination == destination


def test_long_investigator_error_does_not_discard_browser_results(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        executor,
        "decide_next_action",
        lambda **kwargs: (_ for _ in ()).throw(
            executor.InvestigatorDecisionError("provider output validation details " * 20)
        ),
    )

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert result.status == executor.StepExecutionStatus.FAILED
    assert len(result.investigation) == 1
    assert len(result.investigation[0].outcome) <= 300
    assert result.investigation[0].evidence
    assert all(item.strip() for item in result.investigation[0].evidence)


def test_inspect_decision_adds_real_follow_up_page_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in form is ready")
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))

    def inspect_once(**kwargs):
        observed = kwargs["test"].observations[0].evidence[0]
        return AgentDecision.model_validate({
            "decision": "inspect",
            "rationale": "The initial browser action left the outcome unverified.",
            "evidence": [observed],
            "actions": [],
            "new_test": None,
        }, strict=False)

    monkeypatch.setattr(executor, "decide_next_action", inspect_once)
    result = executor.execute_test_plan(
        "https://example.com/login",
        assertion_plan([]),
    )

    inspection = result.tests[0].observations[-1]
    assert inspection.action == "inspect"
    assert "Member sign in form is ready" in inspection.visible_text
    assert any("Member sign in form is ready" in item for item in result.investigation[0].evidence)
    assert result.tests[0].scenario_status.value == "inconclusive"


class ActionLocator:
    first = None

    def __init__(self) -> None:
        self.first = self

    def count(self) -> int:
        return 1

    def click(self, **kwargs: object) -> None:
        pass

    def fill(self, value: str, **kwargs: object) -> None:
        pass

    def check(self, **kwargs: object) -> None:
        pass


class VisibleTextLocator(ActionLocator):
    def __init__(self, page: "AssertionPage", text: str) -> None:
        super().__init__()
        self.page = page
        self.text = text

    def count(self) -> int:
        return int(self.text.casefold() in self.page.visible_text.casefold())

    def wait_for(self, state: str, timeout: int) -> None:
        self.page.waited_for.append(timeout)
        if not self.count():
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

            raise PlaywrightTimeoutError("text did not become visible")


class BodyTextLocator:
    def __init__(self, page: "AssertionPage") -> None:
        self.page = page

    def inner_text(self, **kwargs: object) -> str:
        return self.page.visible_text


class AssertionPage(FakePage):
    def __init__(self, visible_text: str, appears_after_wait: str | None = None) -> None:
        super().__init__()
        self.visible_text = visible_text
        self.appears_after_wait = appears_after_wait
        self.waited_for: list[int] = []

    def get_by_label(self, *args: object, **kwargs: object) -> ActionLocator:
        return ActionLocator()

    get_by_role = get_by_label
    get_by_placeholder = get_by_label

    def get_by_text(self, text: str, **kwargs: object) -> VisibleTextLocator:
        return VisibleTextLocator(self, text)

    def locator(self, selector: str) -> BodyTextLocator:
        assert selector == "body"
        return BodyTextLocator(self)

    def wait_for_timeout(self, timeout: int) -> None:
        self.waited_for.append(timeout)
        if self.appears_after_wait:
            self.visible_text += f" {self.appears_after_wait}"


class StructuralLocator(ActionLocator):
    def __init__(self, *, visible: bool, text: str = "") -> None:
        super().__init__()
        self.visible = visible
        self.text = text

    def count(self) -> int:
        return int(self.visible)

    def is_visible(self) -> bool:
        return self.visible

    def inner_text(self, **_kwargs: object) -> str:
        return self.text


class RejectionStatePage(AssertionPage):
    def get_by_label(self, label: str, **_kwargs: object) -> StructuralLocator:
        return StructuralLocator(visible=label in {"Email", "Password"})

    def get_by_role(self, role: str, **_kwargs: object) -> StructuralLocator:
        if role == "status":
            return StructuralLocator(visible=True, text="Email not found")
        return StructuralLocator(visible=False)


def assertion_plan(assertions: list[dict]) -> FlowTestPlan:
    return FlowTestPlan.model_validate(
        {
            "flow": "login",
            "summary": "Verify invalid login feedback.",
            "tests": [
                {
                    "name": "Reject invalid login",
                    "description": "Submit invalid credentials and verify visible feedback.",
                    "steps": [
                        {"action": "navigate", "target": "Login page", "value": None},
                        {"action": "click", "target": "Log in", "value": None},
                    ],
                    "expected_result": "Invalid credentials are rejected.",
                    "assertions": assertions,
                }
            ],
        },
        strict=False,
    )


def execute_with_assertions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    page: AssertionPage,
    assertions: list[dict],
):
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    return executor.execute_test_plan(
        "https://example.com/login",
        assertion_plan(assertions),
    )


def test_unexpected_login_success_fails_and_captures_assertion_screenshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in Login successful")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [{"text": "Login successful", "expectation": "not_visible", "timeout_ms": 300}],
    )

    assertion = result.tests[0].assertions[0]
    assert result.status.value == "failed"
    assert assertion.status.value == "failed"
    assert "Login successful" in assertion.observed_text
    assert "Login successful" in assertion.message
    assert [item.filename for item in result.evidence] == [
        "test-01-failure.png",
        "test-01-final.png",
    ]
    assert result.evidence[0].label.startswith("Assertion failure:")
    assert "Login successful" in result.evidence[0].label


def test_expected_login_rejection_passes_text_assertions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in Error: Invalid email or password")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [
            {"text": "Login successful", "expectation": "not_visible", "timeout_ms": 250},
            {"text": "Invalid email or password", "expectation": "visible", "timeout_ms": 500},
        ],
    )

    assert result.status.value == "passed"
    assert [item.status.value for item in result.tests[0].assertions] == ["passed", "passed"]
    assert "Invalid email or password" in result.tests[0].assertions[1].observed_text
    assert page.waited_for == [250, 500]
    assert result.tests[0].steps[-1].message == 'Clicked "Log in".'


def test_generic_rejection_assertions_accept_site_specific_status_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = RejectionStatePage("Member sign in Email not found")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [
            {"kind": "login_form_visible", "timeout_ms": 500},
            {"kind": "status_visible", "timeout_ms": 500},
        ],
    )

    scenario = result.tests[0]
    assert scenario.scenario_status.value == "pass"
    assert [item.status.value for item in scenario.assertions] == ["passed", "passed"]
    assert [item.target for item in scenario.assertions] == [None, None]
    assert "Email not found" in scenario.assertions[1].observed_text


def test_login_form_hidden_assertion_detects_success_transition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = RejectionStatePage("Welcome, Demo User")
    page.get_by_label = lambda *_args, **_kwargs: StructuralLocator(visible=False)
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [{"kind": "login_form_hidden", "timeout_ms": 500}],
    )

    assertion = result.tests[0].assertions[0]
    assert assertion.status.value == "passed"
    assert "not visible" in assertion.message.lower()


def test_authenticated_assertion_requires_a_post_login_signal_not_only_a_hidden_form(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class AuthenticatedPage(RejectionStatePage):
        def get_by_label(self, label: str, **_kwargs: object) -> StructuralLocator:
            return StructuralLocator(visible=False)

    page = AuthenticatedPage("Welcome, Demo User. Your account is ready. Log out")
    result = execute_with_assertions(
        monkeypatch, tmp_path, page,
        [{"kind": "login_authenticated", "timeout_ms": 250}],
    )
    assertion = result.tests[0].assertions[0]
    assert assertion.status.value == "passed"
    assert "Welcome, Demo User" in assertion.observed_text

    weak_page = AuthenticatedPage("Something went wrong. Please try again later.")
    weak_result = execute_with_assertions(
        monkeypatch, tmp_path, weak_page,
        [{"kind": "login_authenticated", "timeout_ms": 250}],
    )
    assert weak_result.tests[0].assertions[0].status.value == "failed"


def test_empty_field_rejection_accepts_native_validity_or_visible_rejection_feedback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class InvalidEmailLocator(StructuralLocator):
        def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "valid": False, "required": True, "ariaInvalid": False,
                "validationMessage": "Please fill out this field.",
            }

    class NativeValidationPage(RejectionStatePage):
        def get_by_label(self, label: str, **_kwargs: object) -> StructuralLocator:
            if label == "Email":
                return InvalidEmailLocator(visible=True)
            return StructuralLocator(visible=True)

        def get_by_role(self, role: str, **_kwargs: object) -> StructuralLocator:
            return StructuralLocator(visible=False)

    native_page = NativeValidationPage("Member sign in")
    native = execute_with_assertions(
        monkeypatch, tmp_path, native_page,
        [{"kind": "login_rejected", "target": "Email", "timeout_ms": 250}],
    )
    assert native.tests[0].assertions[0].status.value == "passed"
    assert "native browser validity" in native.tests[0].assertions[0].message.lower()

    app_page = RejectionStatePage("Member sign in Email not found")
    app_result = execute_with_assertions(
        monkeypatch, tmp_path, app_page,
        [{"kind": "login_rejected", "target": "Email", "timeout_ms": 250}],
    )
    assert app_result.tests[0].assertions[0].status.value == "passed"
    assert "Email not found" in app_result.tests[0].assertions[0].observed_text


def test_screenshot_label_for_structured_assertion_names_the_control_and_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in")
    result = execute_with_assertions(
        monkeypatch, tmp_path, page,
        [{"kind": "control_invalid", "target": "Email", "timeout_ms": 250}],
    )
    assert result.evidence[0].label == "Assertion failure: Email control is not invalid"


def test_native_required_field_assertion_uses_browser_validity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class ValidityLocator(ActionLocator):
        def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "valid": False,
                "required": True,
                "validationMessage": "Please fill out this field.",
            }

    class NativeValidationPage(AssertionPage):
        def get_by_label(self, *_args: object, **_kwargs: object) -> ValidityLocator:
            return ValidityLocator()

    page = NativeValidationPage("Member sign in")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [{"kind": "control_invalid", "target": "Email", "timeout_ms": 250}],
    )

    assertion = result.tests[0].assertions[0]
    assert assertion.status.value == "passed"
    assert "Please fill out this field." in assertion.message
    assert "control" in assertion.message.lower()


def test_control_invalid_assertion_accepts_accessible_aria_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class AccessibleInvalidLocator(ActionLocator):
        def evaluate(self, _script: str) -> dict[str, object]:
            return {
                "valid": True,
                "required": False,
                "ariaInvalid": True,
                "validationMessage": "",
            }

    class AccessibleInvalidPage(AssertionPage):
        def get_by_label(self, *_args: object, **_kwargs: object) -> AccessibleInvalidLocator:
            return AccessibleInvalidLocator()

    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        AccessibleInvalidPage("Sign in"),
        [{"kind": "control_invalid", "target": "Email", "timeout_ms": 250}],
    )

    assertion = result.tests[0].assertions[0]
    assert assertion.status.value == "passed"
    assert "aria-invalid=true" in assertion.message.lower()


def test_missing_expected_ui_fails_with_observed_page_excerpt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in Please try again")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [{"text": "Invalid email or password", "expectation": "visible", "timeout_ms": 250}],
    )

    assertion = result.tests[0].assertions[0]
    assert assertion.status.value == "failed"
    assert "Please try again" in assertion.observed_text
    assert "observed visible dom excerpt" in assertion.message.lower()


def test_not_visible_assertion_waits_full_window_for_late_success_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in", appears_after_wait="Login successful")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [{"text": "Login successful", "expectation": "not_visible", "timeout_ms": 750}],
    )

    assertion = result.tests[0].assertions[0]
    assert page.waited_for == [750]
    assert assertion.status.value == "failed"
    assert "Login successful" in assertion.observed_text


def test_visible_assertion_timeout_captures_actual_page_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = AssertionPage("Member sign in Please try again")
    result = execute_with_assertions(
        monkeypatch,
        tmp_path,
        page,
        [{"text": "Invalid credentials", "expectation": "visible", "timeout_ms": 250}],
    )

    assertion = result.tests[0].assertions[0]
    assert assertion.status.value == "failed"
    assert "timed out" in assertion.message.lower()
    assert "Please try again" in assertion.observed_text
    assert result.evidence[0].label.startswith("Assertion failure:")


def test_focused_agent_does_not_add_unapproved_follow_up_test(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    calls = 0

    def decision(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentDecision.model_validate(
                {
                    "decision": "new_test",
                    "rationale": "A second focused scenario can check whether the target page loads.",
                    "evidence": ['Could not find UI element "Email".'],
                    "actions": [],
                    "new_test": {
                        "name": "Check target page loads",
                        "description": "Open the target page and record its visible state.",
                        "steps": [
                            {"action": "navigate", "target": "target page", "value": None}
                        ],
                        "expected_result": "The target page loads.",
                        "assertions": [],
                    },
                },
                strict=False,
            )
        return AgentDecision.model_validate(
            {
                "decision": "continue",
                "rationale": "No additional scenario is needed.",
                "evidence": ["Navigated to https://example.com/login"],
                "actions": [],
                "new_test": None,
            },
            strict=False,
        )

    monkeypatch.setattr(executor, "decide_next_action", decision)

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert len(result.tests) == 1
    assert len(result.effective_plan.tests) == 1
    assert result.investigation[0].decision.value == "new_test"
    assert "does not authorize" in result.investigation[0].outcome


def test_focused_agent_stops_after_one_unapproved_follow_up_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    calls = 0

    def add_test(**kwargs):
        nonlocal calls
        calls += 1
        return AgentDecision.model_validate(
            {
                "decision": "new_test",
                "rationale": "A focused page-open scenario can add evidence for the requested flow.",
                "evidence": ["Navigated to https://example.com/login"],
                "actions": [],
                "new_test": {
                    "name": f"Follow-up {calls}",
                    "description": "Open the target and capture its visible state.",
                    "steps": [
                        {"action": "navigate", "target": "target page", "value": None}
                    ],
                    "expected_result": "The target page loads.",
                    "assertions": [],
                },
            },
            strict=False,
        )

    monkeypatch.setattr(executor, "decide_next_action", add_test)

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert calls == 1
    assert len(result.investigation) == 1
    assert len(result.tests) == 1


def test_investigator_finish_does_not_skip_selected_scenarios(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_plan = {
        "flow": "login",
        "scope": "broad",
        "summary": "Run two independent login scenarios.",
        "tests": [
            {
                "name": name,
                "description": "Open the target and observe the page.",
                "steps": [
                    {"action": "navigate", "target": "target page", "value": None}
                ],
                "expected_result": "The target page is observed.",
                "assertions": [],
            }
            for name in ("First selected scenario", "Second selected scenario")
        ],
    }
    plan = FlowTestPlan.model_validate(raw_plan, strict=False)
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(
        executor,
        "decide_next_action",
        lambda **kwargs: AgentDecision.model_validate(
            {
                "decision": "finish",
                "rationale": "No assertion was available for this scenario.",
                "evidence": ["The requested action completed."],
                "actions": [],
                "new_test": None,
            },
            strict=False,
        ),
    )

    result = executor.execute_test_plan(
        "https://example.com/login",
        plan,
        selected_scenario_ids=[test.scenario_id for test in plan.tests],
    )

    assert [test.name for test in result.tests] == [
        "First selected scenario",
        "Second selected scenario",
    ]
    assert all(test.scenario_status.value == "inconclusive" for test in result.tests)
    assert result.investigation[0].decision.value == "continue"


def test_agent_can_queue_only_structured_alternative_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    calls = 0

    def choose_path(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return AgentDecision.model_validate(
                {
                    "decision": "alternative_path",
                    "rationale": "The planned email label was absent, so inspect the visible login action through a separate path.",
                    "evidence": ['Could not find UI element "Email".'],
                    "actions": [
                        {"action": "click", "target": "Log in", "value": None}
                    ],
                    "new_test": None,
                },
                strict=False,
            )
        return AgentDecision.model_validate(
            {
                "decision": "finish",
                "rationale": "The alternate path did not reach a verifiable login outcome.",
                "evidence": ['Could not find UI element "Log in".'],
                "actions": [],
                "new_test": None,
            },
            strict=False,
        )

    monkeypatch.setattr(executor, "decide_next_action", choose_path)

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert len(result.tests) == 1
    assert "does not authorize" in result.investigation[0].outcome
    assert result.investigation[0].decision.value == "alternative_path"


def test_browser_stops_before_an_action_when_time_budget_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    page = FakePage()
    install_fake_browser(monkeypatch, page)
    monkeypatch.setenv("FLOWCHECK_ARTIFACTS_DIR", str(tmp_path))
    clock_values = iter([0.0, 301.0, 301.0, 301.0])
    monkeypatch.setattr(executor.time, "monotonic", lambda: next(clock_values, 301.0))

    result = executor.execute_test_plan("https://example.com/login", login_plan())

    assert result.tests[0].steps[0].status.value == "failed"
    assert "300-second time budget" in result.tests[0].steps[0].message
