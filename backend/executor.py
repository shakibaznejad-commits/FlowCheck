import uuid
import time
import re
import secrets

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from .evidence import capture_screenshot
from .investigator import (
    InvestigatorDecisionError,
    decide_next_action,
)
from .observations import (
    ObservationSignals,
    attach_observation_listeners,
    capture_page_observation,
)
from .schemas import (
    ActionType,
    AgentStatus,
    AssertionKind,
    AssertionExecutionResult,
    ExecutionResult,
    InvestigationChoice,
    InvestigationRecord,
    PlannedStep,
    PlanScope,
    StepExecutionResult,
    StepExecutionStatus,
    TestExecutionResult,
    TestPlan,
    TextAssertion,
    TextExpectation,
    ScreenshotEvidence,
    ScenarioStatus,
    MAX_PLAN_SCENARIOS,
    MAX_BROWSER_ACTIONS,
)


def resolve_scenario_test_data(
    planned_test, supplied: dict[str, str] | None
) -> dict[str, str]:
    """Resolve one scenario's transient data, generating only negative values locally."""
    values = dict(supplied or {})
    required = {item.value for item in planned_test.prerequisites}
    required.update(re.findall(
        r"\{\{(test_email|test_password|valid_test_email|valid_test_password|negative_test_email|negative_test_password)\}\}",
        " ".join(step.value or "" for step in planned_test.steps),
    ))
    if "test_email" in required and "test_email" not in values and "valid_test_email" in values:
        values["test_email"] = values["valid_test_email"]
    if "test_password" in required and "test_password" not in values and "valid_test_password" in values:
        values["test_password"] = values["valid_test_password"]
    if "negative_test_email" in required and "negative_test_email" not in values:
        values["negative_test_email"] = f"flowcheck-{secrets.token_hex(6)}@example.invalid"
    if "negative_test_password" in required and "negative_test_password" not in values:
        candidate = "Fc!" + secrets.token_urlsafe(18)
        while candidate in values.values():
            candidate = "Fc!" + secrets.token_urlsafe(18)
        values["negative_test_password"] = candidate
    return values
from .scenario_evaluation import evaluate_scenario
from .url_policy import (
    TargetScopeError,
    redact_sensitive_text,
    redact_url,
    validate_target_url,
)
from .ai_usage import UsageLimitExceeded
from .ai_authorization import AIUserNotApproved


class BrowserExecutionError(Exception):
    """Raised when browser execution cannot start or complete."""


BROWSER_EXECUTION_DEADLINE_SECONDS = 300


def _text_excerpt(text: str, expected: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= 500:
        return normalized

    position = normalized.casefold().find(expected.casefold())
    if position < 0:
        return normalized[:497] + "..."

    start = max(0, position - 160)
    excerpt = normalized[start : start + 497]
    if start:
        excerpt = "..." + excerpt[3:]
    if start + 497 < len(normalized):
        excerpt = excerpt[:494] + "..."
    return excerpt


def _assertion_failure_label(assertion: TextAssertion) -> str:
    if assertion.kind == AssertionKind.CONTROL_INVALID:
        return f"Assertion failure: {assertion.target or 'Form'} control is not invalid"
    if assertion.kind == AssertionKind.LOGIN_REJECTED:
        return f"Assertion failure: empty {assertion.target or 'login'} was not rejected"
    if assertion.kind == AssertionKind.LOGIN_AUTHENTICATED:
        return "Assertion failure: authenticated state was not confirmed"
    if assertion.kind == AssertionKind.LOGIN_FORM_VISIBLE:
        return "Assertion failure: login form was not visible"
    if assertion.kind == AssertionKind.LOGIN_FORM_HIDDEN:
        return "Assertion failure: login form remained visible"
    if assertion.kind == AssertionKind.STATUS_VISIBLE:
        return "Assertion failure: rejection status was not visible"
    expectation = assertion.expectation.value.replace("_", " ")
    return f'Assertion failure: expected "{assertion.text}" to be {expectation}'[:160]


def _login_form_visible(page: Page) -> tuple[bool, str]:
    visible_states: list[bool] = []
    for label in ("Email", "Password"):
        locator = page.get_by_label(label, exact=False)
        visible_states.append(locator.count() > 0 and locator.first.is_visible())
    return all(visible_states), (
        f"Email visible={str(visible_states[0]).lower()}; "
        f"Password visible={str(visible_states[1]).lower()}"
    )


def _visible_rejection_text(page: Page) -> str:
    for role in ("status", "alert"):
        locator = page.get_by_role(role)
        if locator.count() and locator.first.is_visible():
            text = locator.first.inner_text(timeout=1_000).strip()
            if text:
                return text
    return ""


def _authenticated_signal(page: Page) -> tuple[bool, str]:
    body = page.locator("body").inner_text(timeout=2_000)
    sign_out = re.search(r"\b(log\s*out|sign\s*out|logout|signout)\b", body, re.IGNORECASE)
    welcome = re.search(r"\bwelcome\s*,?\s+[\w][\w .'-]{0,60}", body, re.IGNORECASE)
    account_state = re.search(r"\b(dashboard|my account|account overview|profile)\b", body, re.IGNORECASE)
    confirmed = bool(sign_out or (welcome and account_state))
    return confirmed, " ".join(body.split())[:500]


def _execute_assertion(
    page: Page,
    assertion: TextAssertion,
    secrets: tuple[str, ...] = (),
    test_data: dict[str, str] | None = None,
) -> AssertionExecutionResult:
    if assertion.kind in {
        AssertionKind.LOGIN_FORM_VISIBLE,
        AssertionKind.LOGIN_FORM_HIDDEN,
    }:
        is_visible, observed = _login_form_visible(page)
        expected_visible = assertion.kind == AssertionKind.LOGIN_FORM_VISIBLE
        passed = is_visible == expected_visible
        if not passed:
            page.wait_for_timeout(assertion.timeout_ms)
            is_visible, observed = _login_form_visible(page)
            passed = is_visible == expected_visible
        expectation_label = "visible" if expected_visible else "not visible"
        return AssertionExecutionResult(
            kind=assertion.kind,
            text="",
            target=None,
            expectation=assertion.expectation,
            timeout_ms=assertion.timeout_ms,
            status=StepExecutionStatus.PASSED if passed else StepExecutionStatus.FAILED,
            observed_text=observed,
            message=(
                f'Assertion {"passed" if passed else "failed"}: expected the labeled login '
                f'controls to be {expectation_label}. Observed {observed}.'
            ),
        )

    if assertion.kind == AssertionKind.LOGIN_AUTHENTICATED:
        form_visible, form_state = _login_form_visible(page)
        signal, body = _authenticated_signal(page)
        if form_visible or not signal:
            page.wait_for_timeout(assertion.timeout_ms)
            form_visible, form_state = _login_form_visible(page)
            signal, body = _authenticated_signal(page)
        passed = not form_visible and signal
        excerpt = redact_sensitive_text(_text_excerpt(body, "welcome"), secrets)
        return AssertionExecutionResult(
            kind=assertion.kind,
            text="",
            target=None,
            expectation=assertion.expectation,
            timeout_ms=assertion.timeout_ms,
            status=StepExecutionStatus.PASSED if passed else StepExecutionStatus.FAILED,
            observed_text=excerpt,
            message=(
                f'Assertion {"passed" if passed else "failed"}: expected an authenticated page state '
                f'with the login controls hidden and a post-login signal. Observed {form_state}; '
                f'authenticated signal={str(signal).lower()}; visible text excerpt: "{excerpt}".'
            ),
        )

    if assertion.kind == AssertionKind.LOGIN_REJECTED:
        form_visible, form_state = _login_form_visible(page)
        target = assertion.target or ""
        native_invalid = False
        validation_message = ""
        try:
            control = page.get_by_label(target, exact=False)
            if control.count():
                validity = control.first.evaluate(
                    "element => ({valid: element.checkValidity(), ariaInvalid: element.getAttribute('aria-invalid') === 'true', validationMessage: element.validationMessage || ''})"
                )
                native_invalid = isinstance(validity, dict) and (
                    validity.get("valid") is False or validity.get("ariaInvalid") is True
                )
                validation_message = str(validity.get("validationMessage") or "") if isinstance(validity, dict) else ""
        except Exception:
            native_invalid = False
        feedback = _visible_rejection_text(page)
        if not form_visible or not (native_invalid or feedback):
            page.wait_for_timeout(assertion.timeout_ms)
            form_visible, form_state = _login_form_visible(page)
            feedback = _visible_rejection_text(page)
            if not native_invalid:
                try:
                    control = page.get_by_label(target, exact=False)
                    if control.count():
                        validity = control.first.evaluate(
                            "element => ({valid: element.checkValidity(), ariaInvalid: element.getAttribute('aria-invalid') === 'true', validationMessage: element.validationMessage || ''})"
                        )
                        native_invalid = isinstance(validity, dict) and (
                            validity.get("valid") is False or validity.get("ariaInvalid") is True
                        )
                        validation_message = str(validity.get("validationMessage") or "") if isinstance(validity, dict) else ""
                except Exception:
                    native_invalid = False
        passed = form_visible and (native_invalid or bool(feedback))
        authenticated_signal = False
        authenticated_text = ""
        if not form_visible:
            try:
                authenticated_signal, authenticated_text = _authenticated_signal(page)
            except Exception:
                authenticated_signal = False
        observed = feedback or validation_message or authenticated_text
        safe_observed = redact_sensitive_text(observed, secrets)[:500]
        return AssertionExecutionResult(
            kind=assertion.kind,
            text="",
            target=target,
            expectation=assertion.expectation,
            timeout_ms=assertion.timeout_ms,
            status=StepExecutionStatus.PASSED if passed else StepExecutionStatus.FAILED,
            observed_text=safe_observed,
            message=(
                f'Assertion {"passed" if passed else "failed"}: expected the login attempt with empty '
                f'"{target}" to be rejected while the login form remains visible. Observed {form_state}; '
                f'native browser validity={str(native_invalid).lower()}; '
                f'authenticated signal={str(authenticated_signal).lower()}; '
                f'visible rejection feedback or page text="{safe_observed}".'
            ),
        )

    if assertion.kind == AssertionKind.STATUS_VISIBLE:
        status_text = ""

        def read_status() -> str:
            for role in ("status", "alert"):
                locator = page.get_by_role(role)
                if locator.count() and locator.first.is_visible():
                    text = locator.first.inner_text(timeout=1_000).strip()
                    if text:
                        return text
            return ""

        status_text = read_status()
        if not status_text:
            page.wait_for_timeout(assertion.timeout_ms)
            status_text = read_status()
        excerpt = redact_sensitive_text(status_text, secrets)[:500]
        passed = bool(excerpt)
        return AssertionExecutionResult(
            kind=assertion.kind,
            text="",
            target=assertion.target,
            expectation=assertion.expectation,
            timeout_ms=assertion.timeout_ms,
            status=StepExecutionStatus.PASSED if passed else StepExecutionStatus.FAILED,
            observed_text=excerpt,
            message=(
                f'Assertion {"passed" if passed else "failed"}: expected non-empty visible '
                f'status or alert feedback. Observed text: "{excerpt}".'
            ),
        )

    if assertion.kind == AssertionKind.CONTROL_INVALID:
        target = assertion.target or ""
        try:
            locator = page.get_by_label(target, exact=False)
            if locator.count() == 0:
                return AssertionExecutionResult(
                    kind=assertion.kind,
                    text=assertion.text,
                    target=target,
                    expectation=assertion.expectation,
                    timeout_ms=assertion.timeout_ms,
                    status=StepExecutionStatus.FAILED,
                    observed_text="",
                    message=f'Assertion failed: could not find form control "{target}" to inspect validity.',
                )
            validity = locator.evaluate(
                "element => ({ valid: element.checkValidity(), "
                "required: Boolean(element.required), "
                "ariaInvalid: element.getAttribute('aria-invalid') === 'true', "
                "validationMessage: element.validationMessage || '' })"
            )
            is_invalid = isinstance(validity, dict) and (
                validity.get("valid") is False or validity.get("ariaInvalid") is True
            )
            validation_message = (
                str(validity.get("validationMessage") or "")
                if isinstance(validity, dict)
                else ""
            )
            excerpt = redact_sensitive_text(validation_message, secrets)[:200]
            message = (
                f'Assertion {"passed" if is_invalid else "failed"}: '
                f'expected form control "{target}" to be invalid. '
                f'Observed browser validity: valid={validity.get("valid") if isinstance(validity, dict) else "unknown"}, '
                f'aria-invalid={validity.get("ariaInvalid") if isinstance(validity, dict) else "unknown"}, '
                f'required={validity.get("required") if isinstance(validity, dict) else "unknown"}, '
                f'validation message="{excerpt}".'
            )
            return AssertionExecutionResult(
                kind=assertion.kind,
                text=assertion.text,
                target=target,
                expectation=assertion.expectation,
                timeout_ms=assertion.timeout_ms,
                status=(
                    StepExecutionStatus.PASSED if is_invalid else StepExecutionStatus.FAILED
                ),
                observed_text=excerpt,
                message=message,
            )
        except Exception as exc:
            return AssertionExecutionResult(
                kind=assertion.kind,
                text=assertion.text,
                target=target,
                expectation=assertion.expectation,
                timeout_ms=assertion.timeout_ms,
                status=StepExecutionStatus.FAILED,
                observed_text="",
                message=(
                    f'Assertion failed: could not inspect browser validity for '
                    f'"{redact_sensitive_text(target, secrets)}" ({type(exc).__name__}).'
                ),
            )

    resolved_text = assertion.text
    for key, secret in (test_data or {}).items():
        resolved_text = resolved_text.replace("{{" + key + "}}", secret)
    try:
        if assertion.expectation == TextExpectation.VISIBLE:
            page.get_by_text(resolved_text, exact=False).wait_for(
                state="visible",
                timeout=assertion.timeout_ms,
            )
        else:
            # Give delayed page feedback the full configured window to appear.
            page.wait_for_timeout(assertion.timeout_ms)

        visible_text = page.locator("body").inner_text(timeout=5_000)
        excerpt = redact_sensitive_text(
            _text_excerpt(visible_text, resolved_text), secrets
        )
        is_visible = resolved_text.casefold() in visible_text.casefold()
        passed = (
            is_visible
            if assertion.expectation == TextExpectation.VISIBLE
            else not is_visible
        )
        expectation_label = assertion.expectation.value.replace("_", " ")
        message = (
            f'Assertion {"passed" if passed else "failed"}: '
            f'expected text "{redact_sensitive_text(assertion.text, secrets)}" to be {expectation_label}. '
            f'Observed visible DOM excerpt: "{excerpt}"'
        )
        return AssertionExecutionResult(
            kind=assertion.kind,
            text=assertion.text,
            target=assertion.target,
            expectation=assertion.expectation,
            timeout_ms=assertion.timeout_ms,
            status=(
                StepExecutionStatus.PASSED if passed else StepExecutionStatus.FAILED
            ),
            observed_text=excerpt,
            message=message,
        )
    except PlaywrightTimeoutError:
        visible_text = page.locator("body").inner_text(timeout=5_000)
        excerpt = redact_sensitive_text(
            _text_excerpt(visible_text, resolved_text), secrets
        )
        expectation_label = assertion.expectation.value.replace("_", " ")
        return AssertionExecutionResult(
            kind=assertion.kind,
            text=assertion.text,
            target=assertion.target,
            expectation=assertion.expectation,
            timeout_ms=assertion.timeout_ms,
            status=StepExecutionStatus.FAILED,
            observed_text=excerpt,
            message=(
                f'Assertion timed out: expected text "{redact_sensitive_text(assertion.text, secrets)}" to be '
                f'{expectation_label} within {assertion.timeout_ms} ms. '
                f'Observed visible DOM excerpt: "{excerpt}"'
            ),
        )


def _find_element(page: Page, target: str):
    """
    Resolve a human-readable target into a Playwright locator.
    No model-generated CSS or XPath selectors are used.
    """

    target_variants = [target]
    normalized_target = re.sub(r"[\s_-]+", " ", target.strip().casefold())
    if normalized_target in {"log in", "login", "sign in", "signin"}:
        target_variants.extend(
            label for label in ("Log in", "Sign in")
            if label.casefold() != normalized_target
        )

    for variant in target_variants:
        candidates = [
            page.get_by_label(variant, exact=False),
            page.get_by_role("button", name=variant, exact=False),
            page.get_by_placeholder(variant, exact=False),
            page.get_by_text(variant, exact=False),
        ]
        for locator in candidates:
            try:
                if locator.count() > 0:
                    return locator.first
            except Exception:
                continue

    return None


def _execute_step(
    page: Page,
    step: PlannedStep,
    default_url: str,
    test_data: dict[str, str] | None = None,
    timeout_ms: int = 10_000,
) -> StepExecutionResult:

    try:
        if step.action == ActionType.NAVIGATE:
            destination = step.value or (
                step.target
                if step.target.lower().startswith(("http://", "https://"))
                else default_url
            )
            validate_target_url(destination, authorized_origin=default_url)

            page.goto(
                destination,
                wait_until="domcontentloaded",
                timeout=min(timeout_ms, 20_000),
            )

            return StepExecutionResult(
                action=step.action,
                target=step.target,
                status=StepExecutionStatus.PASSED,
                message=f"Navigated to {redact_url(destination)}",
            )

        locator = _find_element(
            page,
            step.target,
        )

        if locator is None:
            return StepExecutionResult(
                action=step.action,
                target=step.target,
                status=StepExecutionStatus.FAILED,
                message=f'Could not find UI element "{step.target}".',
            )

        if step.action == ActionType.FILL:
            if step.value is None:
                return StepExecutionResult(
                    action=step.action,
                    target=step.target,
                    status=StepExecutionStatus.FAILED,
                    message="Fill action requires a value.",
                )

            resolved_value = step.value
            available_test_data = test_data or {}
            if resolved_value in available_test_data:
                resolved_value = available_test_data[resolved_value]
            for key, secret in available_test_data.items():
                resolved_value = resolved_value.replace("{{" + key + "}}", secret)
            locator.fill(
                resolved_value,
                timeout=timeout_ms,
            )
            message = f'Filled "{step.target}".'

        elif step.action == ActionType.CLICK:
            locator.click(
                timeout=timeout_ms,
            )
            message = f'Clicked "{step.target}".'

        elif step.action == ActionType.SUBMIT:
            locator.click(
                timeout=timeout_ms,
            )
            message = f'Submitted using "{step.target}".'

        elif step.action == ActionType.CHECK:
            locator.check(
                timeout=timeout_ms,
            )
            message = f'Checked "{step.target}".'

        else:
            return StepExecutionResult(
                action=step.action,
                target=step.target,
                status=StepExecutionStatus.FAILED,
                message=f"Unsupported action: {step.action}",
            )

        return StepExecutionResult(
            action=step.action,
            target=step.target,
            status=StepExecutionStatus.PASSED,
            message=message,
        )

    except PlaywrightTimeoutError:
        return StepExecutionResult(
            action=step.action,
            target=step.target,
            status=StepExecutionStatus.FAILED,
            message=f'Timed out while interacting with "{step.target}".',
        )

    except AIUserNotApproved:
        raise
    except Exception as exc:
        return StepExecutionResult(
            action=step.action,
            target=step.target,
            status=StepExecutionStatus.FAILED,
            message=f"Browser action failed ({type(exc).__name__}).",
        )


def execute_test_plan(
    url: str,
    plan: TestPlan,
    goal: str | None = None,
    test_data: dict[str, str] | None = None,
    selected_scenario_ids: list[str] | None = None,
    execution_deadline_seconds: int = BROWSER_EXECUTION_DEADLINE_SECONDS,
    user_id: int | None = None,
) -> ExecutionResult:

    test_results: list[TestExecutionResult] = []
    evidence: list[ScreenshotEvidence] = []
    investigation: list[InvestigationRecord] = []
    effective_plan = plan.model_copy(deep=True)
    retries_queued = 0
    started_at = time.monotonic()
    wall_started = time.perf_counter()
    time_budget_seconds = execution_deadline_seconds
    run_id = uuid.uuid4().hex
    selected_ids = set(selected_scenario_ids or [])
    agent_status = AgentStatus.COMPLETED
    investigator_quota_blocked = False

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                executable_path="/usr/bin/chromium",
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
            )

            authorized_origin = validate_target_url(url)

            def guard_request(route) -> None:
                try:
                    validate_target_url(
                        route.request.url,
                        authorized_origin=authorized_origin,
                    )
                except TargetScopeError:
                    route.abort("blockedbyclient")
                    return
                route.continue_()

            for test_index, planned_test in enumerate(effective_plan.tests):
                if test_index > 0 and time.monotonic() - started_at >= time_budget_seconds:
                    for pending in effective_plan.tests[test_index:]:
                        test_results.append(TestExecutionResult(
                            scenario_id=pending.scenario_id,
                            name=pending.name,
                            status=StepExecutionStatus.FAILED,
                            scenario_status=ScenarioStatus.NOT_RUN,
                            actual_result="Scenario was not run because the browser time budget was exhausted.",
                            uncertainty="Execution budget was exhausted before this scenario started.",
                            steps=[], assertions=[], observations=[],
                            reproduction_steps=[],
                        ))
                    break
                if selected_ids and planned_test.scenario_id not in selected_ids:
                    test_results.append(TestExecutionResult(
                        scenario_id=planned_test.scenario_id,
                        name=planned_test.name,
                        status=StepExecutionStatus.FAILED,
                        scenario_status=ScenarioStatus.NOT_RUN,
                        actual_result="Scenario was not selected for this run.",
                        uncertainty="Excluded during test setup.",
                        steps=[], assertions=[], observations=[],
                        reproduction_steps=[],
                    ))
                    continue
                required = {item.value for item in planned_test.prerequisites}
                scenario_data = resolve_scenario_test_data(planned_test, test_data)
                missing = sorted(required - set(scenario_data))
                if missing:
                    test_results.append(TestExecutionResult(
                        scenario_id=planned_test.scenario_id,
                        name=planned_test.name,
                        status=StepExecutionStatus.FAILED,
                        scenario_status=ScenarioStatus.BLOCKED,
                        actual_result="Scenario was not started because required test data was not supplied.",
                        uncertainty="Required authorized test data is missing.",
                        steps=[], assertions=[], observations=[],
                        reproduction_steps=[],
                    ))
                    continue
                # Independent contexts prevent cookie/local-storage leakage
                # between login scenarios. Browser.close() releases all contexts.
                context = browser.new_context(
                    viewport={"width": 1440, "height": 900}
                )
                try:
                    context.route("**/*", guard_request)
                except AttributeError:
                    # Lightweight test doubles may not expose route interception.
                    pass
                page = context.new_page()
                step_results: list[StepExecutionResult] = []
                assertion_results: list[AssertionExecutionResult] = []
                observations = []
                signals: ObservationSignals = attach_observation_listeners(page)
                sensitive_values = tuple(
                    step.value
                    for step in planned_test.steps
                    if step.value
                ) + tuple(scenario_data.values())

                for step_index, step in enumerate(planned_test.steps):
                    if time.monotonic() - started_at >= time_budget_seconds:
                        result = StepExecutionResult(
                            action=step.action,
                            target=step.target,
                            status=StepExecutionStatus.FAILED,
                            message=f"The browser run reached its {time_budget_seconds}-second time budget.",
                        )
                        step_results.append(result)
                        break

                    remaining_seconds = time_budget_seconds - (time.monotonic() - started_at)
                    if remaining_seconds <= 0:
                        step_results.append(StepExecutionResult(
                            action=step.action,
                            target=step.target,
                            status=StepExecutionStatus.FAILED,
                            message=f"The browser run reached its {time_budget_seconds}-second time budget.",
                        ))
                        break

                    result = _execute_step(
                        page=page,
                        step=step,
                        default_url=url,
                        test_data=scenario_data,
                        timeout_ms=max(1, min(10_000, int(remaining_seconds * 1000))),
                    )

                    step_results.append(result)

                    observations.append(
                        capture_page_observation(
                            page,
                            step_index=step_index,
                            action=step.action.value,
                            signals=signals,
                            secrets=sensitive_values,
                        )
                    )

                    if result.status == StepExecutionStatus.FAILED:
                        failure_evidence = capture_screenshot(
                            page,
                            run_id=run_id,
                            filename=f"test-{test_index + 1:02d}-failure.png",
                            label=f"Failure after step {step_index + 1}: {step.target}"[:160],
                            test_index=test_index,
                            step_index=step_index,
                            secrets=sensitive_values,
                        )
                        if failure_evidence:
                            evidence.append(failure_evidence)
                        break

                steps_succeeded = bool(step_results) and all(
                    step.status == StepExecutionStatus.PASSED
                    for step in step_results
                )
                if steps_succeeded and time.monotonic() - started_at < time_budget_seconds:
                    for assertion in planned_test.assertions:
                        assertion_result = _execute_assertion(
                            page, assertion, sensitive_values, scenario_data
                        )
                        assertion_results.append(assertion_result)
                        observations.append(
                            capture_page_observation(
                                page,
                                step_index=max(0, len(planned_test.steps) - 1),
                                action="assertion",
                                signals=signals,
                                secrets=sensitive_values,
                            )
                        )
                        if (
                            assertion_result.status == StepExecutionStatus.FAILED
                        and not any(
                                item.filename == f"test-{test_index + 1:02d}-failure.png"
                                for item in evidence
                            )
                        ):
                            failure_evidence = capture_screenshot(
                                page,
                                run_id=run_id,
                                filename=f"test-{test_index + 1:02d}-failure.png",
                                label=_assertion_failure_label(assertion),
                                test_index=test_index,
                                step_index=max(0, len(planned_test.steps) - 1),
                                secrets=sensitive_values,
                            )
                            if failure_evidence:
                                evidence.append(failure_evidence)

                final_evidence = capture_screenshot(
                    page,
                    run_id=run_id,
                    filename=f"test-{test_index + 1:02d}-final.png",
                    label=f"Final browser state after {planned_test.name}"[:160],
                    test_index=test_index,
                    step_index=None,
                    secrets=sensitive_values,
                )
                if final_evidence:
                    evidence.append(final_evidence)

                test_status = (
                    StepExecutionStatus.PASSED
                    if steps_succeeded
                    and all(
                        assertion.status == StepExecutionStatus.PASSED
                        for assertion in assertion_results
                    )
                    else StepExecutionStatus.FAILED
                )

                test_result = TestExecutionResult(
                        scenario_id=planned_test.scenario_id,
                        name=planned_test.name,
                        status=test_status,
                        scenario_status=evaluate_scenario(
                            planned_test,
                            assertion_results,
                            step_results,
                            observations,
                        ),
                        actual_result=(
                            assertion_results[-1].message
                            if assertion_results
                            else step_results[-1].message if step_results
                            else "No browser action was executed."
                        )[:1000],
                        uncertainty=(
                            "No planned text assertion verified the expected behavior."
                            if not planned_test.assertions or not assertion_results
                            else None
                        ),
                        reproduction_steps=[
                            f"{step.action.value}: {step.target}"
                            for step in planned_test.steps
                        ][:12],
                        steps=step_results,
                        assertions=assertion_results,
                        observations=observations[:20],
                    )
                test_results.append(test_result)

                if time.monotonic() - started_at >= time_budget_seconds:
                    break

                is_verified_pass = bool(planned_test.assertions) and bool(assertion_results) and all(
                    item.status == StepExecutionStatus.PASSED
                    for item in assertion_results
                )
                has_runtime_signal = any(
                    observation.console_errors
                    or observation.failed_requests
                    or observation.http_errors
                    or observation.alerts
                    for observation in test_result.observations
                )
                needs_investigation = not is_verified_pass or has_runtime_signal
                if len(investigation) < 4 and needs_investigation and investigator_quota_blocked:
                    investigation.append(InvestigationRecord(
                        decision=InvestigationChoice.CONTINUE,
                        rationale="Further investigation stopped because the user's daily Provider quota is exhausted.",
                        evidence=[item.message for item in test_result.assertions[:3]],
                        outcome="Browser execution continued from observed evidence; no further AI investigation request was sent.",
                    ))
                elif len(investigation) < 4 and needs_investigation:
                    action_count = sum(
                        len(item.steps) for item in effective_plan.tests
                    )
                    try:
                        decision = decide_next_action(
                            goal=goal or effective_plan.summary,
                            test=test_result,
                            remaining_tests=max(0, 5 - len(effective_plan.tests)),
                            remaining_actions=max(0, MAX_BROWSER_ACTIONS - action_count),
                            **({"user_id": user_id} if user_id is not None else {}),
                        )
                    except UsageLimitExceeded:
                        investigator_quota_blocked = True
                        agent_status = AgentStatus.SKIPPED
                        investigation.append(InvestigationRecord(
                            decision=InvestigationChoice.CONTINUE,
                            rationale="Investigator stopped because the user's daily Provider quota is exhausted.",
                            evidence=[item.message for item in test_result.assertions[:3]],
                            outcome="No AI follow-up was sent; remaining planned browser scenarios continue.",
                        ))
                    except InvestigatorDecisionError as exc:
                        available_evidence = [
                            item.message for item in test_result.assertions
                        ]
                        available_evidence.extend(
                            item
                            for observation in test_result.observations[-2:]
                            for item in observation.evidence
                        )
                        available_evidence.extend(
                            item.message for item in test_result.steps[-2:]
                        )
                        investigation.append(
                        InvestigationRecord(
                            decision=InvestigationChoice.CONTINUE,
                            rationale="Follow-up stopped safely because the investigator response could not be validated.",
                            evidence=list(dict.fromkeys(
                                item.strip() for item in available_evidence if item.strip()
                            ))[:5],
                            outcome="Stopped this decision cycle safely because the investigator response could not be validated.",
                        )
                        )
                    else:
                        record = InvestigationRecord(
                            decision=decision.decision,
                            rationale=decision.rationale,
                            evidence=decision.evidence,
                            outcome="Decision recorded.",
                        )
                        investigation.append(record)

                        if decision.decision == InvestigationChoice.INSPECT:
                            page.wait_for_timeout(300)
                            if len(test_result.observations) < 20:
                                follow_up_observation = capture_page_observation(
                                    page,
                                    step_index=max(0, len(planned_test.steps) - 1),
                                    action="inspect",
                                    signals=signals,
                                    secrets=sensitive_values,
                                )
                                test_result.observations.append(follow_up_observation)
                                record.evidence = list(dict.fromkeys(
                                    [*record.evidence, *follow_up_observation.evidence]
                                ))[:5]
                            record.outcome = "Captured one additional bounded page observation."

                        elif decision.decision == InvestigationChoice.RETRY:
                            has_submission = any(
                                step.action in {ActionType.CLICK, ActionType.SUBMIT}
                                for step in planned_test.steps
                            )
                            retry_steps = len(planned_test.steps)
                            if (
                                effective_plan.scope != PlanScope.BROAD
                                or has_submission
                                or retries_queued >= 2
                                or len(effective_plan.tests) >= MAX_PLAN_SCENARIOS
                                or action_count + retry_steps > MAX_BROWSER_ACTIONS
                            ):
                                record.outcome = (
                                    "Retry skipped because this scope does not authorize additional scenarios."
                                    if effective_plan.scope != PlanScope.BROAD
                                    else "Retry skipped to avoid repeating a submission or exceeding the run budget."
                                )
                            else:
                                retry_test = planned_test.model_copy(
                                    update={
                                        "name": f"Retry: {planned_test.name}"[:160],
                                        "scenario_id": f"{planned_test.scenario_id}-retry"[:64],
                                        "description": "One bounded retry of the same non-submitting structured actions.",
                                    }
                                )
                                effective_plan.tests.append(retry_test)
                                effective_plan.scope = PlanScope.BROAD
                                retries_queued += 1
                                record.outcome = "Queued one bounded retry using the same validated actions."

                        elif decision.decision == InvestigationChoice.ALTERNATIVE_PATH:
                            extra_actions = len(decision.actions)
                            if (
                                effective_plan.scope != PlanScope.BROAD
                                or len(effective_plan.tests) >= MAX_PLAN_SCENARIOS
                                or action_count + extra_actions > MAX_BROWSER_ACTIONS
                            ):
                                record.outcome = (
                                    "Alternative scenario skipped because the selected scope does not authorize additional cases."
                                    if effective_plan.scope != PlanScope.BROAD
                                    else "Alternative path skipped because the scenario or action budget is exhausted."
                                )
                            else:
                                alternative_test = planned_test.model_copy(
                                    update={
                                        "name": f"Alternative path: {planned_test.name}"[:160],
                                        "scenario_id": f"{planned_test.scenario_id}-alternate"[:64],
                                        "description": "Bounded alternate structured path selected from observed browser evidence.",
                                        "steps": decision.actions,
                                    }
                                )
                                effective_plan.tests.append(alternative_test)
                                effective_plan.scope = PlanScope.BROAD
                                record.outcome = "Queued the validated alternative path as a separate test scenario."

                        elif decision.decision == InvestigationChoice.NEW_TEST:
                            new_test = decision.new_test
                            if new_test is not None:
                                new_action_count = action_count + len(new_test.steps)
                                if (
                                    effective_plan.scope != PlanScope.BROAD
                                    or len(effective_plan.tests) >= MAX_PLAN_SCENARIOS
                                    or new_action_count > MAX_BROWSER_ACTIONS
                                ):
                                    record.outcome = (
                                        "New scenario skipped because the selected scope does not authorize additional cases."
                                        if effective_plan.scope != PlanScope.BROAD
                                        else "New test skipped because the scenario or action budget is exhausted."
                                    )
                                else:
                                    effective_plan.tests.append(new_test)
                                    effective_plan.scope = PlanScope.BROAD
                                    record.outcome = "Queued one goal-relevant structured test."

                        elif decision.decision == InvestigationChoice.FINISH:
                            pending_selected = any(
                                not selected_ids
                                or pending.scenario_id in selected_ids
                                for pending in effective_plan.tests[test_index + 1 :]
                            )
                            if pending_selected:
                                record.decision = InvestigationChoice.CONTINUE
                                record.rationale = (
                                    "An early finish cannot skip remaining selected scenarios."
                                )
                                record.outcome = (
                                    "Deferred investigator finish; remaining selected scenarios will run."
                                )
                            else:
                                record.outcome = (
                                    "The investigator judged the collected evidence sufficient."
                                )
                                break

                elif len(investigation) < 4:
                    last_planned_test = test_index == len(effective_plan.tests) - 1
                    investigation.append(
                        InvestigationRecord(
                            decision=(
                                InvestigationChoice.FINISH
                                if last_planned_test
                                else InvestigationChoice.CONTINUE
                            ),
                            rationale=(
                                "Every planned assertion passed, so the observed behavior is verified."
                                if last_planned_test
                                else "The current test is verified; continue to the next planned scenario."
                            ),
                            evidence=[
                                assertion.message
                                for assertion in assertion_results[:3]
                            ],
                            outcome=(
                                "Stopped because the planned evidence is sufficient."
                                if last_planned_test
                                else "Continued with the next user-planned scenario."
                            ),
                        )
                    )

            browser.close()

    except AIUserNotApproved:
        raise
    except Exception as exc:
        if not test_results:
            raise BrowserExecutionError(
                f"Could not execute browser test ({type(exc).__name__})."
            ) from exc
        # A later scenario or cleanup failure must not erase evidence already
        # collected. Mark remaining planned cases explicitly as NOT_RUN.
        agent_status = AgentStatus.FAILED
        completed_ids = {item.scenario_id for item in test_results}
        for planned in effective_plan.tests:
            if planned.scenario_id not in completed_ids:
                test_results.append(TestExecutionResult(
                    scenario_id=planned.scenario_id,
                    name=planned.name,
                    status=StepExecutionStatus.FAILED,
                    scenario_status=ScenarioStatus.NOT_RUN,
                    actual_result="Scenario was not run after an internal browser execution error.",
                    uncertainty="Browser execution stopped before this scenario started.",
                    steps=[], assertions=[], observations=[], reproduction_steps=[],
                ))

    # A deadline reached immediately after a scenario must preserve its result
    # and all collected evidence while recording every remaining plan item.
    completed_ids = {item.scenario_id for item in test_results}
    for planned in effective_plan.tests:
        if planned.scenario_id not in completed_ids:
            test_results.append(TestExecutionResult(
                scenario_id=planned.scenario_id,
                name=planned.name,
                status=StepExecutionStatus.FAILED,
                scenario_status=ScenarioStatus.NOT_RUN,
                actual_result="Scenario was not run because browser execution stopped before it started.",
                uncertainty="Browser execution deadline or an internal error stopped the run.",
                steps=[], assertions=[], observations=[], reproduction_steps=[],
            ))

    overall_status = (
        StepExecutionStatus.PASSED
        if test_results
        and all(
            result.status == StepExecutionStatus.PASSED
            for result in test_results
        )
        else StepExecutionStatus.FAILED
    )

    return ExecutionResult(
        status=overall_status,
        agent_status=agent_status,
        duration_ms=max(0, int((time.perf_counter() - wall_started) * 1000)),
        browser_action_count=sum(len(result.steps) for result in test_results),
        tests=test_results,
        run_id=run_id,
        evidence=evidence,
        investigation=investigation,
        effective_plan=effective_plan,
    )
