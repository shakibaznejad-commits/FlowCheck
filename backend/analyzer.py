import json
import os

from pydantic import ValidationError

from .analyzer_prompt import ANALYZER_SYSTEM_PROMPT
from .log_safety import request_log_secrets, safe_log_message
from .llm_provider import OpenAIProvider
from .schemas import (
    AnalysisStatus,
    AnalyzeResultsRequest,
    ReportNarrativeSource,
    ResultAnalysis,
    ScenarioStatus,
    StrictModel,
    StepExecutionStatus,
)
from .scenario_evaluation import evaluate_scenario
from .ai_usage import UsageLimitExceeded
from .ai_authorization import AIUserNotApproved


class AnalyzerConfigurationError(Exception):
    """Raised when the result analyzer provider is not configured."""


class AnalyzerOutputError(Exception):
    """Raised when the provider returns invalid or unsupported analysis."""


class AnalyzerProviderError(Exception):
    """Raised when the external analysis provider request fails."""


class ProviderAnalysis(StrictModel):
    """LLM payload schema; source metadata is assigned by FlowCheck."""

    status: AnalysisStatus
    summary: str
    root_cause: str | None
    evidence: list[str]
    reproduction_steps: list[str]
    recommendation: str


def analyze_results(request: AnalyzeResultsRequest, user_id: int | None = None) -> ResultAnalysis:
    scenario_statuses = _scenario_statuses(request)
    expected_status = _overall_status(scenario_statuses)

    # Incomplete browser evidence is already deterministically inconclusive or
    # blocked. No model call can turn missing verification into a pass.
    if expected_status in {AnalysisStatus.BLOCKED, AnalysisStatus.INCONCLUSIVE}:
        return _deterministic_analysis(request, scenario_statuses)

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("ANALYZER FALLBACK: LLM_API_KEY is not set.", flush=True)
        return _deterministic_analysis(request, scenario_statuses)

    try:
        provider = OpenAIProvider(
            api_key=api_key,
            model=os.getenv("LLM_MODEL", "cx/gpt-6-luna"),
            base_url=os.getenv(
                "LLM_BASE_URL",
                "http://host.docker.internal:20128/v1",
            ),
            user_id=user_id,
        )
    except AIUserNotApproved:
        raise
    except Exception as exc:
        _log_provider_error(exc, request)
        return _deterministic_analysis(request, scenario_statuses)

    user_message = json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            print("ANALYZER PROVIDER REQUEST START", flush=True)
            print(
                "ANALYZER PROVIDER SCHEMA response_format=json_schema strict=True "
                "name=flowcheck_result_analysis "
                f"required={list(ProviderAnalysis.model_json_schema()['required'])}",
                flush=True,
            )
            content = provider.generate_json(
                system_prompt=(
                    ANALYZER_SYSTEM_PROMPT
                    if attempt == 0
                    else ANALYZER_SYSTEM_PROMPT
                    + "\nRepair the previous response: return complete valid JSON matching the schema, "
                    "with at most 10 evidence entries and exact observed messages only."
                ),
                user_message=user_message,
                schema=ProviderAnalysis.model_json_schema(),
                schema_name="flowcheck_result_analysis",
            )
            print(
                f"ANALYZER RESPONSE RECEIVED bytes={len(content.encode('utf-8'))}",
                flush=True,
            )
            provider_analysis = ProviderAnalysis.model_validate_json(content, strict=True)
            analysis = ResultAnalysis(
                **provider_analysis.model_dump(),
                analysis_source=ReportNarrativeSource.PROVIDER,
            )
            _validate_analysis_rules(request, analysis, expected_status)
            print(
                "========== VALIDATED ANALYZER RESPONSE =========="
                f" status={analysis.status.value} evidence={len(analysis.evidence)}",
                flush=True,
            )
            print("=================================================", flush=True)
            return analysis
        except ValidationError as exc:
            last_error = exc
            print("========== ANALYZER VALIDATION ERROR ==========", flush=True)
            print(exc.errors(include_input=False), flush=True)
            print("===============================================", flush=True)
        except AnalyzerOutputError as exc:
            last_error = exc
        except UsageLimitExceeded:
            raise
        except AIUserNotApproved:
            raise
        except Exception as exc:
            _log_provider_error(exc, request)
            # Network failures/timeouts are not repaired by resending the same
            # request; keep the browser-derived result and finish the run.
            print("ANALYZER FALLBACK: provider request failed.", flush=True)
            return _deterministic_analysis(request, scenario_statuses)

    print(
        "ANALYZER FALLBACK: bounded provider repair failed "
        f"({type(last_error).__name__ if last_error else 'unknown error'}).",
        flush=True,
    )
    return _deterministic_analysis(request, scenario_statuses)


def _scenario_statuses(request: AnalyzeResultsRequest) -> list[ScenarioStatus]:
    execution_by_id = {
        test.scenario_id: test for test in request.execution.tests if test.scenario_id
    }
    execution_by_name = {test.name: test for test in request.execution.tests}
    statuses = []
    for planned in request.plan.tests:
        result = execution_by_id.get(planned.scenario_id) or execution_by_name.get(planned.name)
        statuses.append(
            evaluate_scenario(
                planned,
                result.assertions if result else [],
                result.steps if result else [],
                result.observations if result else [],
                missing_prerequisite=bool(
                    result and result.scenario_status == ScenarioStatus.BLOCKED
                    and not result.steps and not result.assertions
                ),
                declared_status=result.scenario_status if result else None,
            )
        )
    return statuses


def _overall_status(statuses: list[ScenarioStatus]) -> AnalysisStatus:
    if ScenarioStatus.FAIL in statuses:
        return AnalysisStatus.FAILED
    if ScenarioStatus.BLOCKED in statuses:
        return AnalysisStatus.BLOCKED
    if statuses and all(item == ScenarioStatus.PASS for item in statuses):
        return AnalysisStatus.PASSED
    return AnalysisStatus.INCONCLUSIVE


def _deterministic_analysis(
    request: AnalyzeResultsRequest,
    statuses: list[ScenarioStatus],
) -> ResultAnalysis:
    """Summarize only the validated structured browser outcomes and messages."""
    status = _overall_status(statuses)
    counts = {item: statuses.count(item) for item in ScenarioStatus}
    summary = (
        "Browser assertions verified "
        f"{counts[ScenarioStatus.PASS]} scenario(s), failed "
        f"{counts[ScenarioStatus.FAIL]}, blocked {counts[ScenarioStatus.BLOCKED]}, "
        f"inconclusive {counts[ScenarioStatus.INCONCLUSIVE]}, and not run "
        f"{counts[ScenarioStatus.NOT_RUN]}."
    )
    failed_assertions = [
        assertion.message
        for test in request.execution.tests
        for assertion in test.assertions
        if assertion.status == StepExecutionStatus.FAILED
    ]
    other_observations = [
        assertion.message
        for test in request.execution.tests
        for assertion in test.assertions
        if assertion.status != StepExecutionStatus.FAILED
    ] + [
        evidence
        for test in request.execution.tests
        for observation in test.observations
        for evidence in observation.evidence
    ] + [
        step.message
        for test in request.execution.tests
        for step in test.steps
    ]
    evidence = list(dict.fromkeys(failed_assertions + other_observations))[:10]
    reproduction_steps = [
        f"{step.action.value}: {step.target}"
        for test in request.plan.tests
        for step in test.steps
    ][:12] or ["No browser steps were available."]
    recommendation = (
        "Review the failed assertions and their observed browser evidence."
        if status == AnalysisStatus.FAILED
        else "Review the incomplete browser evidence and rerun the unverified scenarios."
        if status in {AnalysisStatus.BLOCKED, AnalysisStatus.INCONCLUSIVE}
        else "Review the recorded successful assertions."
    )
    return ResultAnalysis(
        status=status,
        summary=summary,
        root_cause=None,
        evidence=evidence,
        reproduction_steps=reproduction_steps,
        recommendation=(
            recommendation + " AI analysis was unavailable; scenario outcomes use validated browser results."
        ),
        analysis_source=ReportNarrativeSource.FALLBACK,
    )


def _validate_analysis_rules(
    request: AnalyzeResultsRequest,
    analysis: ResultAnalysis,
    expected_status: AnalysisStatus,
) -> None:
    observed_messages = {
        step.message for test in request.execution.tests for step in test.steps
    }
    assertions = [
        assertion for test in request.execution.tests for assertion in test.assertions
    ]
    observed_messages.update(assertion.message for assertion in assertions)
    observed_messages.update(
        message
        for test in request.execution.tests
        for observation in test.observations
        for message in observation.evidence
    )
    planned_assertions = {
        (
            test.scenario_id, assertion.kind, assertion.text, assertion.target,
            assertion.expectation, assertion.timeout_ms,
        )
        for test in request.plan.tests for assertion in test.assertions
    }
    planned_by_id = {test.scenario_id: test for test in request.plan.tests}
    planned_by_name = {test.name: test for test in request.plan.tests}
    for result in request.execution.tests:
        planned = planned_by_id.get(result.scenario_id)
        if planned is None and not result.scenario_id:
            planned = planned_by_name.get(result.name)
        for assertion in result.assertions:
            identity = (
                planned.scenario_id if planned else result.scenario_id,
                assertion.kind, assertion.text, assertion.target,
                assertion.expectation, assertion.timeout_ms,
            )
            if identity not in planned_assertions:
                _raise_business_rule_error("Executed assertions must match the structured test plan.")
    if any(message not in observed_messages for message in analysis.evidence):
        _raise_business_rule_error("Analysis evidence was not present in browser observations.")
    failed_assertions = [
        assertion.message for assertion in assertions
        if assertion.status == StepExecutionStatus.FAILED
    ]
    if failed_assertions and analysis.status != AnalysisStatus.FAILED:
        _raise_business_rule_error("A failed structured assertion must remain a failed analysis.")
    if any(message not in analysis.evidence for message in failed_assertions):
        _raise_business_rule_error("Analysis evidence must include every failed structured assertion.")
    if analysis.status == AnalysisStatus.PASSED and (
        any(assertion.status != StepExecutionStatus.PASSED for assertion in assertions)
        or any(assertion.message not in analysis.evidence for assertion in assertions)
    ):
        _raise_business_rule_error(
            "A passed analysis must cite every successful assertion and have no failed assertions."
        )
    if analysis.status != expected_status:
        _raise_business_rule_error("Analysis status must match the executed assertion outcomes.")


def _log_provider_error(exc: Exception, request: AnalyzeResultsRequest) -> None:
    print("========== ANALYZER PROVIDER ERROR ==========", flush=True)
    print(
        f"{type(exc).__name__}: "
        f"{safe_log_message(str(exc), request_log_secrets(request))}",
        flush=True,
    )
    print("=============================================", flush=True)


def _raise_business_rule_error(rule: str) -> None:
    print("========== ANALYZER BUSINESS RULE ERROR ==========", flush=True)
    print(rule, flush=True)
    print("================================================", flush=True)
    raise AnalyzerOutputError(rule)
