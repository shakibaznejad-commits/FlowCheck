from .schemas import (
    AssertionExecutionResult,
    PlannedTest,
    ScenarioStatus,
    StepExecutionResult,
    StepExecutionStatus,
)


def evaluate_scenario(
    planned: PlannedTest,
    assertions: list[AssertionExecutionResult],
    steps: list[StepExecutionResult],
    observations: list[object],
    *,
    missing_prerequisite: bool = False,
    declared_status: ScenarioStatus | None = None,
) -> ScenarioStatus:
    """Derive website outcome from structured, executed browser evidence only."""
    if missing_prerequisite:
        return ScenarioStatus.BLOCKED
    if any(item.status == StepExecutionStatus.FAILED for item in assertions):
        return ScenarioStatus.FAIL
    if any(item.status == StepExecutionStatus.FAILED for item in steps):
        return ScenarioStatus.BLOCKED
    if not steps and not assertions:
        if declared_status in {ScenarioStatus.BLOCKED, ScenarioStatus.INCONCLUSIVE}:
            return declared_status
        return ScenarioStatus.NOT_RUN
    planned_actions = [(step.action, step.target) for step in planned.steps]
    executed_actions = [(step.action, step.target) for step in steps]
    actions_complete = len(planned_actions) == len(executed_actions) and all(
        expected == observed
        for expected, observed in zip(planned_actions, executed_actions)
    )
    weak_login_transition_only = (
        bool(planned.assertions)
        and all(item.kind.value == "login_form_hidden" for item in planned.assertions)
        and len(assertions) == len(planned.assertions)
        and all(item.status == StepExecutionStatus.PASSED for item in assertions)
    )
    if actions_complete and weak_login_transition_only:
        return ScenarioStatus.INCONCLUSIVE
    if (
        actions_complete
        and planned.assertions
        and len(assertions) == len(planned.assertions)
        and all(item.status == StepExecutionStatus.PASSED for item in assertions)
    ):
        return ScenarioStatus.PASS
    if declared_status == ScenarioStatus.BLOCKED:
        return ScenarioStatus.BLOCKED
    return ScenarioStatus.INCONCLUSIVE
