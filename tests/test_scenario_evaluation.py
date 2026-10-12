from backend.scenario_evaluation import evaluate_scenario
from backend.sensitive_data import sanitize_goal, sanitize_plan_credentials
from backend.schemas import (
    ActionType,
    AssertionKind,
    AssertionExecutionResult,
    PlannedTest,
    PlannedStep,
    StepExecutionResult,
    StepExecutionStatus,
    TextExpectation,
    ScenarioStatus,
    TestPlan as PlanSchema,
)


def planned_test(assertions=None, prerequisites=None):
    return PlannedTest.model_validate(
        {
            "name": "Invalid login",
            "description": "Check rejection",
            "steps": [{"action": "click", "target": "Log in", "value": None}],
            "expected_result": "Login is rejected",
            "assertions": assertions or [],
            "prerequisites": prerequisites or [],
        },
        strict=False,
    )


def assertion(status):
    return AssertionExecutionResult(
        text="Invalid email or password",
        expectation=TextExpectation.VISIBLE,
        timeout_ms=1000,
        status=status,
        observed_text="Invalid email or password",
        message=f"assertion {status.value}",
    )


def test_expected_assertion_is_scenario_pass():
    result = evaluate_scenario(planned_test([{
        "text": "Invalid email or password",
        "expectation": "visible",
        "timeout_ms": 1000,
    }]), [assertion(StepExecutionStatus.PASSED)], [
        StepExecutionResult(
            action=ActionType.CLICK,
            target="Log in",
            status=StepExecutionStatus.PASSED,
            message="Clicked Log in.",
        )
    ], [])
    assert result == ScenarioStatus.PASS


def test_hidden_login_form_alone_cannot_verify_successful_authentication():
    planned = planned_test([{"kind": "login_form_hidden", "timeout_ms": 500}])
    planned.steps = [
        PlannedStep(action=ActionType.CLICK, target="Log in", value=None),
    ]
    result = evaluate_scenario(
        planned,
        [AssertionExecutionResult(
            kind=AssertionKind.LOGIN_FORM_HIDDEN, timeout_ms=500,
            status=StepExecutionStatus.PASSED,
            observed_text="Email visible=false; Password visible=false",
            message="Login controls are hidden.",
        )],
        [StepExecutionResult(
            action=ActionType.CLICK, target="Log in", status=StepExecutionStatus.PASSED,
            message="Clicked Log in.",
        )],
        [],
    )
    assert result == ScenarioStatus.INCONCLUSIVE


def test_assertion_cannot_pass_when_a_planned_action_was_not_executed():
    result = evaluate_scenario(
        planned_test([{
            "text": "Invalid email or password",
            "expectation": "visible",
            "timeout_ms": 1000,
        }]),
        [assertion(StepExecutionStatus.PASSED)],
        [],
        [],
    )
    assert result == ScenarioStatus.INCONCLUSIVE


def test_failed_assertion_is_website_fail_not_agent_failure():
    result = evaluate_scenario(
        planned_test([{"text": "Invalid email or password", "expectation": "visible", "timeout_ms": 1000}]),
        [assertion(StepExecutionStatus.FAILED)], [], [],
    )
    assert result == ScenarioStatus.FAIL


def test_missing_prerequisite_is_blocked_and_unverified_is_inconclusive():
    assert evaluate_scenario(planned_test(prerequisites=["test_email"]), [], [], [], missing_prerequisite=True) == ScenarioStatus.BLOCKED
    assert evaluate_scenario(planned_test(), [], [], []) == ScenarioStatus.NOT_RUN
    assert evaluate_scenario(
        planned_test(), [], [
            StepExecutionResult(
                action=ActionType.CLICK,
                target="Log in",
                status=StepExecutionStatus.PASSED,
                message="Step completed successfully.",
            )
        ], []
    ) == ScenarioStatus.INCONCLUSIVE


def test_unexecuted_scenario_is_not_run_even_when_no_steps_exist():
    assert evaluate_scenario(planned_test(), [], [], []) == ScenarioStatus.NOT_RUN


def test_preserves_declared_inconclusive_for_started_scenario_without_evidence():
    assert evaluate_scenario(
        planned_test(), [], [], [], declared_status=ScenarioStatus.INCONCLUSIVE
    ) == ScenarioStatus.INCONCLUSIVE


def test_credentials_are_replaced_with_transient_placeholders():
    plan = PlanSchema.model_validate({
        "flow": "login", "summary": "Check invalid login", "tests": [planned_test().model_dump(mode="json")],
    }, strict=False)
    plan.tests[0].steps = [
        PlannedStep(action=ActionType.FILL, target="Email", value="person@example.com"),
        PlannedStep(action=ActionType.FILL, target="Password", value="PrivatePass123!"),
    ]
    sanitized = sanitize_plan_credentials(plan)
    serialized = sanitized.model_dump_json()
    assert "person@example.com" not in serialized
    assert "PrivatePass123!" not in serialized
    assert "{{negative_test_email}}" in serialized
    assert "{{negative_test_password}}" in serialized
    assert "person@example.com" not in sanitize_goal("Login as person@example.com")
