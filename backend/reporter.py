import json
import os
import re

from pydantic import ValidationError

from .llm_provider import OpenAIProvider
from .log_safety import request_log_secrets, safe_log_message
from .reporter_prompt import REPORTER_SYSTEM_PROMPT
from .scenario_evaluation import evaluate_scenario
from .ai_usage import UsageLimitExceeded
from .ai_authorization import AIUserNotApproved
from .schemas import (
    AnalysisStatus,
    FinalReport,
    GenerateReportRequest,
    ProviderFinalReport,
    ReportNarrativeSource,
    ReportSeverity,
    ScenarioReport,
    ScenarioStatus,
    StepExecutionStatus,
)


class ReporterConfigurationError(Exception):
    """Raised when the report provider is not configured."""


class ReporterOutputError(Exception):
    """Raised when the provider returns invalid or unsupported report data."""


class ReporterProviderError(Exception):
    """Raised when the external report provider request fails."""


def generate_report(request: GenerateReportRequest, user_id: int | None = None) -> FinalReport:
    if request.analysis.analysis_source == ReportNarrativeSource.FALLBACK:
        print("REPORT FALLBACK: analyzer used validated browser evidence.", flush=True)
        return _fallback_report(request)

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        print("REPORT FALLBACK: LLM_API_KEY is not set.", flush=True)
        return _fallback_report(request)

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
        print("REPORT PROVIDER REQUEST START", flush=True)
    except AIUserNotApproved:
        raise
    except Exception as exc:
        print("========== REPORT PROVIDER ERROR ==========", flush=True)
        print(
            f"{type(exc).__name__}: "
            f"{safe_log_message(str(exc), request_log_secrets(request))}",
            flush=True,
        )
        print("===========================================", flush=True)
        return _fallback_report(request)

    user_message = json.dumps(request.model_dump(mode="json"), ensure_ascii=False)
    provider_report: ProviderFinalReport | None = None
    for attempt in range(2):
        try:
            print("REPORT PROVIDER REQUEST START", flush=True)
            content = provider.generate_json(
                system_prompt=(
                    REPORTER_SYSTEM_PROMPT
                    if attempt == 0
                    else REPORTER_SYSTEM_PROMPT
                    + "\nYour previous response failed strict JSON validation. Return one complete JSON object only."
                ),
                user_message=user_message,
                schema=ProviderFinalReport.model_json_schema(),
                schema_name="flowcheck_final_report",
            )
            print(
                f"REPORT RESPONSE RECEIVED bytes={len(content.encode('utf-8'))}",
                flush=True,
            )
            provider_report = ProviderFinalReport.model_validate_json(
                content, strict=True
            )
            break
        except ValidationError as exc:
            print("========== REPORT VALIDATION ERROR ==========", flush=True)
            print(exc.errors(include_input=False), flush=True)
            print("=============================================", flush=True)
            if attempt == 1:
                return _fallback_report(request)
        except UsageLimitExceeded:
            raise
        except AIUserNotApproved:
            raise
        except Exception as exc:
            print("========== REPORT PROVIDER ERROR ==========", flush=True)
            print(
                f"{type(exc).__name__}: "
                f"{safe_log_message(str(exc), request_log_secrets(request))}",
                flush=True,
            )
            print("===========================================", flush=True)
            return _fallback_report(request)

    if provider_report is None:
        return _fallback_report(request)
    try:
        report = FinalReport.model_validate(
            provider_report.model_dump(mode="python"), strict=True
        )
        _validate_report_rules(request, report)
    except (ValidationError, ReporterOutputError) as exc:
        print(
            "REPORT FALLBACK: provider narrative failed validation "
            f"({type(exc).__name__}).",
            flush=True,
        )
        return _fallback_report(request)
    report = report.model_copy(update={
        "agent_status": request.execution.agent_status,
        "scope": request.plan.scope,
        "scenario_results": _scenario_reports(request),
        "narrative_source": ReportNarrativeSource.PROVIDER,
    })
    print(
        "========== VALIDATED REPORT RESPONSE =========="
        f" status={report.status.value} severity={report.severity.value}",
        flush=True,
    )
    print("================================================", flush=True)
    return report


def _fallback_report(request: GenerateReportRequest) -> FinalReport:
    """Build a report only from validated plan, analysis, and browser evidence."""
    return FinalReport(
        status=request.analysis.status,
        title=f"{request.plan.flow.value.title()} test results",
        flow=request.plan.flow,
        expected_results=[test.expected_result for test in request.plan.tests],
        actual_result=request.analysis.summary,
        root_cause=request.analysis.root_cause,
        evidence=request.analysis.evidence[:10],
        severity=_severity_for_request(request),
        reproduction_steps=request.analysis.reproduction_steps,
        recommendation=(
            request.analysis.recommendation[:360]
            + " AI analysis or report text was unavailable; scenario outcomes use validated browser results."
        )[:500],
        run_id=request.execution.run_id,
        screenshot_evidence=request.execution.evidence,
        agent_status=request.execution.agent_status,
        scope=request.plan.scope,
        scenario_results=_scenario_reports(request),
        narrative_source=ReportNarrativeSource.FALLBACK,
    )


def _scenario_reports(request: GenerateReportRequest) -> list[ScenarioReport]:
    execution_by_id = {
        item.scenario_id: item for item in request.execution.tests if item.scenario_id
    }
    execution_by_name = {item.name: item for item in request.execution.tests}
    scenarios = []
    for planned in request.plan.tests:
        result = execution_by_id.get(planned.scenario_id) or execution_by_name.get(planned.name)
        status = (
            evaluate_scenario(
                planned,
                result.assertions,
                result.steps,
                result.observations,
                missing_prerequisite=bool(
                    result.scenario_status == ScenarioStatus.BLOCKED
                    and not result.steps
                    and not result.assertions
                ),
                declared_status=result.scenario_status,
            )
            if result
            else ScenarioStatus.NOT_RUN
        )
        evidence: list[str] = []
        if result:
            evidence.extend(item.message for item in result.assertions if item.message.strip())
            evidence.extend(
                item
                for observation in result.observations
                for item in observation.evidence
                if item.strip()
            )
            if not evidence:
                evidence.extend(item.message for item in result.steps if item.message.strip())
        scenarios.append(ScenarioReport(
            scenario_id=planned.scenario_id,
            name=planned.name,
            goal=planned.goal or planned.description,
            expected_result=planned.expected_result,
            status=status,
            actual_result=(result.actual_result if result else "Scenario was not executed.")[:1000],
            evidence=list(dict.fromkeys(evidence))[:20],
            reproduction_steps=(result.reproduction_steps if result else [])[:12],
            uncertainty=result.uncertainty if result else "Scenario was not executed.",
        ))
    return scenarios


def _validate_report_rules(
    request: GenerateReportRequest,
    report: FinalReport,
) -> None:
    assertions = [
        assertion
        for test in request.execution.tests
        for assertion in test.assertions
    ]
    failed_assertions = [
        assertion.message
        for assertion in assertions
        if assertion.status == StepExecutionStatus.FAILED
    ]
    planned_assertions = {
        (
            test.scenario_id,
            assertion.kind,
            assertion.text,
            assertion.target,
            assertion.expectation,
            assertion.timeout_ms,
        )
        for test in request.plan.tests
        for assertion in test.assertions
    }
    planned_by_id = {test.scenario_id: test for test in request.plan.tests}
    planned_by_name = {test.name: test for test in request.plan.tests}
    executed_assertions_match_plan = True
    for result in request.execution.tests:
        planned = planned_by_id.get(result.scenario_id)
        if planned is None and not result.scenario_id:
            planned = planned_by_name.get(result.name)
        for assertion in result.assertions:
            identity = (
                planned.scenario_id if planned else result.scenario_id,
                assertion.kind,
                assertion.text,
                assertion.target,
                assertion.expectation,
                assertion.timeout_ms,
            )
            if identity not in planned_assertions:
                executed_assertions_match_plan = False
    scenario_results = _scenario_reports(request)
    rules = [
        (report.status == request.analysis.status, "Report status must match the validated analysis."),
        (
            not (
                request.execution.status == StepExecutionStatus.FAILED
                and report.status == AnalysisStatus.PASSED
            ),
            "A failed browser execution cannot produce a passed report.",
        ),
        (report.flow == request.plan.flow, "Report flow must match the supplied test plan."),
        (report.run_id == request.execution.run_id, "Report run id must match browser execution."),
        (
            report.screenshot_evidence == request.execution.evidence,
            "Report screenshot evidence must match browser execution references.",
        ),
        (
            bool(assertions)
            or report.status in {AnalysisStatus.BLOCKED, AnalysisStatus.INCONCLUSIVE},
            "A run without an executed text assertion cannot claim a verified outcome.",
        ),
        (
            not failed_assertions or report.status == AnalysisStatus.FAILED,
            "A failed visible-text assertion must remain a failed report.",
        ),
        (
            all(message in report.evidence for message in failed_assertions),
            "Report evidence must include every failed visible-text assertion.",
        ),
        (
            executed_assertions_match_plan,
            "Executed text assertions must match the structured test plan.",
        ),
        (
            report.status != AnalysisStatus.PASSED
            or all(
                assertion.status == StepExecutionStatus.PASSED
                and assertion.message in report.evidence
                for assertion in assertions
            ),
            "A passed report must cite every successful text assertion.",
        ),
        (
            report.status != AnalysisStatus.PASSED
            or all(item.status == ScenarioStatus.PASS for item in scenario_results),
            "A passed report requires every planned scenario to be verified.",
        ),
        (
            report.expected_results
            == [test.expected_result for test in request.plan.tests],
            "Report expected results must match the supplied test plan.",
        ),
        (
            report.actual_result == request.analysis.summary,
            "Report actual result must match the validated analysis summary.",
        ),
        (
            report.root_cause == request.analysis.root_cause,
            "Report root cause must match the validated analysis.",
        ),
        (
            all(item in request.analysis.evidence for item in report.evidence),
            "Report evidence must be present in the validated analysis.",
        ),
        (
            report.reproduction_steps == request.analysis.reproduction_steps,
            "Report reproduction steps must match the validated analysis.",
        ),
        (
            report.recommendation == request.analysis.recommendation,
            "Report recommendation must match the validated analysis.",
        ),
        (
            report.severity == _severity_for_request(request),
            "Report severity must match the validated browser evidence.",
        ),
    ]
    for is_valid, rule in rules:
        if not is_valid:
            print("========== REPORT BUSINESS RULE ERROR ==========", flush=True)
            print(rule, flush=True)
            print("=================================================", flush=True)
            raise ReporterOutputError(rule)


def _severity_for(status: AnalysisStatus) -> ReportSeverity:
    return {
        AnalysisStatus.PASSED: ReportSeverity.INFO,
        AnalysisStatus.BLOCKED: ReportSeverity.NOT_ASSESSED,
        AnalysisStatus.FAILED: ReportSeverity.HIGH,
        AnalysisStatus.INCONCLUSIVE: ReportSeverity.NOT_ASSESSED,
    }[status]


def _severity_for_request(request: GenerateReportRequest) -> ReportSeverity:
    if request.analysis.status != AnalysisStatus.FAILED:
        return _severity_for(request.analysis.status)

    rejection_intent = re.compile(
        r"\b(reject|rejection|invalid|wrong|unknown|unregistered|empty|negative)\b",
        re.IGNORECASE,
    )
    success_signal = re.compile(
        r"\b(success|successful|welcome|authenticated|signed in|logged in|dashboard)\b",
        re.IGNORECASE,
    )
    execution_by_id = {
        item.scenario_id: item for item in request.execution.tests if item.scenario_id
    }
    execution_by_name = {item.name: item for item in request.execution.tests}
    for planned in request.plan.tests:
        if not rejection_intent.search(
            " ".join((planned.name, planned.goal, planned.expected_result, planned.description))
        ):
            continue
        result = execution_by_id.get(planned.scenario_id) or execution_by_name.get(planned.name)
        if result is None:
            continue
        for assertion in result.assertions:
            if assertion.status != StepExecutionStatus.FAILED:
                continue
            if (
                assertion.kind.value == "login_rejected"
                and "authenticated signal=true" in assertion.message.casefold()
            ):
                return ReportSeverity.HIGH
            if (
                assertion.kind.value == "text"
                and assertion.expectation.value == "not_visible"
                and assertion.text
                and success_signal.search(assertion.text)
                and assertion.text.casefold() in assertion.observed_text.casefold()
            ):
                return ReportSeverity.HIGH
    return ReportSeverity.MEDIUM
