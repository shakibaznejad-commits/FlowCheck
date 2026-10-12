from contextlib import asynccontextmanager
import time
import re
from typing import Annotated
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import get_current_user, router as auth_router
from .ai_authorization import require_ai_access
from .ai_authorization import AIUserNotApproved
from .ai_usage import LeaseLost, UsageLedger, UsageLimitExceeded
from .analyzer import (
    AnalyzerConfigurationError,
    AnalyzerOutputError,
    AnalyzerProviderError,
    analyze_results,
)
from .executor import (
    BrowserExecutionError,
    execute_test_plan,
)
from .evidence import resolve_evidence_file
from .database import Base, SavedTestSuite, TestRun, User, engine, get_db, initialize_database
from .log_safety import safe_log_message
from .planner import (
    PlannerConfigurationError,
    PlannerOutputError,
    PlannerProviderError,
    UnsupportedFlowError,
    create_test_plan,
    scenario_verification_issues,
)
from .reporter import (
    ReporterConfigurationError,
    ReporterOutputError,
    ReporterProviderError,
    generate_report,
)
from .scenario_evaluation import evaluate_scenario
from .sensitive_data import sanitize_plan_credentials
from .schemas import (
    AnalyzeResultsRequest,
    ExecutePlanRequest,
    ExecutionResult,
    FinalReport,
    GenerateReportRequest,
    ResultAnalysis,
    RunDetail,
    RunListItem,
    PinRunRequest,
    SavedTestSuiteRequest,
    SavedTestSuiteResponse,
    AnalysisStatus,
    PlanScope,
    TestPlan,
    TestExecutionResult,
    TestPlanRequest,
)
from .url_policy import TargetScopeError, validate_target_url


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database(engine)
    yield


app = FastAPI(title="FlowCheck", lifespan=lifespan)
app.include_router(auth_router)


def _scenario_counts(run: TestRun) -> dict[str, int]:
    statuses = ("pass", "fail", "blocked", "inconclusive", "not_run")
    counts = {status: 0 for status in statuses}
    plan_tests = (run.plan_json or {}).get("tests", [])
    results = (run.execution_json or {}).get("tests", [])
    by_id = {item.get("scenario_id"): item for item in results if item.get("scenario_id")}
    by_name = {item.get("name"): item for item in results if item.get("name")}
    for planned in plan_tests:
        result = by_id.get(planned.get("scenario_id")) or by_name.get(planned.get("name"))
        status = result.get("scenario_status") if result else "not_run"
        if status not in counts:
            status = "inconclusive" if result else "not_run"
        counts[status] += 1
    return counts


@app.post(
    "/api/analyze-results",
    response_model=ResultAnalysis,
)
def analyze_execution(
    request: AnalyzeResultsRequest,
    _user: Annotated[User, Depends(require_ai_access)],
    db: Annotated[Session, Depends(get_db)],
) -> ResultAnalysis:
    step_count = sum(
        len(test.steps)
        for test in request.execution.tests
    )
    print("ANALYZER REQUEST START", flush=True)
    print(
        "ANALYZER INPUT "
        f"goal_present={bool(request.goal.strip())} "
        f"tests={len(request.plan.tests)} "
        f"execution_status={request.execution.status.value} "
        f"steps={step_count}",
        flush=True,
    )

    try:
        analysis = analyze_results(request, user_id=_user.id)
        _persist_agent_stage(
            db, _user.id, request.execution.run_id,
            analysis=analysis, agent_status=request.execution.agent_status.value,
        )
        return analysis
    except UsageLimitExceeded as exc:
        _persist_agent_stage(db, _user.id, request.execution.run_id, agent_status="skipped")
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AIUserNotApproved as exc:
        raise HTTPException(status_code=403, detail="AI access is not enabled for this account.") from exc
    except AnalyzerConfigurationError as exc:
        _persist_agent_stage(
            db, _user.id, request.execution.run_id,
            agent_status=request.execution.agent_status.value,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except AnalyzerOutputError as exc:
        _persist_agent_stage(
            db, _user.id, request.execution.run_id,
            agent_status=request.execution.agent_status.value,
        )
        _log_analyzer_http_error(exc)
        raise HTTPException(
            status_code=502,
            detail="Analyzer returned an invalid structured analysis.",
        ) from exc
    except AnalyzerProviderError as exc:
        _persist_agent_stage(
            db, _user.id, request.execution.run_id,
            agent_status=request.execution.agent_status.value,
        )
        _log_analyzer_http_error(exc)
        raise HTTPException(
            status_code=502,
            detail="The LLM result analysis request failed.",
        ) from exc


def _log_analyzer_http_error(exc: Exception) -> None:
    print("ANALYZER HTTP ERROR", flush=True)
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        print(
            f"{type(current).__name__}: {safe_log_message(str(current))}",
            flush=True,
        )
        current = current.__cause__


def _persist_agent_stage(
    db: Session,
    user_id: int,
    run_id: str,
    *,
    analysis: ResultAnalysis | None = None,
    agent_status: str,
) -> None:
    run = db.scalar(select(TestRun).where(TestRun.id == run_id, TestRun.user_id == user_id))
    if run is None:
        return
    run.agent_status = agent_status
    if analysis is not None:
        run.analysis_json = analysis.model_dump(mode="json")
        run.status = analysis.status.value
    db.commit()


@app.post("/api/generate-report", response_model=FinalReport)
def create_report(
    request: GenerateReportRequest,
    _user: Annotated[User, Depends(require_ai_access)],
    db: Annotated[Session, Depends(get_db)],
) -> FinalReport:
    step_count = sum(len(test.steps) for test in request.execution.tests)
    print("REPORT REQUEST START", flush=True)
    print(
        "REPORT INPUT "
        f"goal_present={bool(request.goal.strip())} "
        f"tests={len(request.plan.tests)} "
        f"execution_status={request.execution.status.value} "
        f"analysis_status={request.analysis.status.value} "
        f"steps={step_count}",
        flush=True,
    )

    try:
        report = generate_report(request, user_id=_user.id)
        completed_at = int(time.time())
        saved_run = db.scalar(
            select(TestRun).where(
                TestRun.id == request.execution.run_id,
                TestRun.user_id == _user.id,
            )
        )
        if saved_run is None:
            saved_run = TestRun(
                id=request.execution.run_id,
                user_id=_user.id,
                target_url=str(request.url),
                goal=request.goal,
                flow=request.plan.flow.value,
                status=report.status.value,
                created_at=completed_at,
                completed_at=completed_at,
                plan_json=request.plan.model_dump(mode="json"),
                execution_json=request.execution.model_dump(mode="json"),
                analysis_json=request.analysis.model_dump(mode="json"),
                report_json=report.model_dump(mode="json"),
                evidence_run_id=report.run_id,
                target_domain=(urlsplit(str(request.url)).hostname or "").lower(),
                scope=request.plan.scope.value,
                agent_status="completed",
            )
            db.add(saved_run)
        else:
            saved_run.status = report.status.value
            saved_run.completed_at = completed_at
            saved_run.plan_json = request.plan.model_dump(mode="json")
            saved_run.execution_json = request.execution.model_dump(mode="json")
            saved_run.analysis_json = request.analysis.model_dump(mode="json")
            saved_run.report_json = report.model_dump(mode="json")
            saved_run.agent_status = report.agent_status.value
        db.commit()
        return report
    except UsageLimitExceeded as exc:
        _persist_agent_stage(db, _user.id, request.execution.run_id, agent_status="skipped")
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AIUserNotApproved as exc:
        raise HTTPException(status_code=403, detail="AI access is not enabled for this account.") from exc
    except ReporterConfigurationError as exc:
        _persist_agent_stage(
            db, _user.id, request.execution.run_id,
            agent_status=request.execution.agent_status.value,
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReporterOutputError as exc:
        _persist_agent_stage(
            db, _user.id, request.execution.run_id,
            agent_status=request.execution.agent_status.value,
        )
        _log_report_http_error(exc)
        raise HTTPException(
            status_code=502,
            detail="Reporter returned an invalid structured report.",
        ) from exc
    except ReporterProviderError as exc:
        _persist_agent_stage(db, _user.id, request.execution.run_id, agent_status="failed")
        _log_report_http_error(exc)
        raise HTTPException(
            status_code=502,
            detail="The LLM final report request failed.",
        ) from exc


@app.get("/api/runs", response_model=list[RunListItem])
def list_runs(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str = "",
    feature: str = "",
    since: int | None = None,
    until: int | None = None,
) -> list[RunListItem]:
    query = select(TestRun).where(TestRun.user_id == _user.id)
    if feature in {"login", "signup"}:
        query = query.where(TestRun.flow == feature)
    if since is not None:
        query = query.where(TestRun.created_at >= since)
    if until is not None:
        query = query.where(TestRun.created_at <= until)
    runs = db.scalars(query.order_by(TestRun.created_at.desc(), TestRun.id.desc())).all()
    if q.strip():
        normalized_query = q.strip().lower()
        runs = [
            run for run in runs
            if normalized_query in (
                run.target_domain or urlsplit(run.target_url).hostname or ""
            ).lower()
        ]
    return [
        RunListItem.model_validate({
            "id": run.id,
            "target_url": run.target_url,
            "goal": run.goal,
            "flow": run.flow,
            "status": run.status,
            "agent_status": run.agent_status,
            "target_domain": run.target_domain or (urlsplit(run.target_url).hostname or "").lower(),
            "scope": run.scope,
            "pinned": run.pinned,
            "scenario_counts": _scenario_counts(run),
            "confirmed_issues": (
                [run.analysis_json.get("summary", "")]
                if run.status == AnalysisStatus.FAILED.value
                and run.analysis_json
                and run.analysis_json.get("summary")
                else []
            ),
            "created_at": run.created_at,
            "completed_at": run.completed_at,
        }, strict=False)
        for run in runs
    ]


@app.get("/api/runs/{run_id}", response_model=RunDetail)
def get_run(
    run_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RunDetail:
    run = db.scalar(
        select(TestRun).where(TestRun.id == run_id, TestRun.user_id == _user.id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    return RunDetail.model_validate({
        "id": run.id,
        "target_url": run.target_url,
        "goal": run.goal,
        "flow": run.flow,
        "status": run.status,
        "agent_status": run.agent_status,
        "target_domain": run.target_domain or (urlsplit(run.target_url).hostname or "").lower(),
        "scope": run.scope,
        "pinned": run.pinned,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "plan": run.plan_json,
        "execution": run.execution_json,
        "analysis": run.analysis_json or None,
        "report": run.report_json or None,
        "evidence_run_id": run.evidence_run_id,
    }, strict=False)


@app.patch("/api/runs/{run_id}/pin")
def set_run_pin(
    run_id: str,
    request: PinRunRequest,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    run = db.scalar(
        select(TestRun).where(TestRun.id == run_id, TestRun.user_id == _user.id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    run.pinned = request.pinned
    db.commit()
    return {"id": run.id, "pinned": run.pinned}


@app.get("/api/runs/{run_id}/compare/{other_run_id}")
def compare_runs(
    run_id: str,
    other_run_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    first = db.scalar(select(TestRun).where(TestRun.id == run_id, TestRun.user_id == _user.id))
    second = db.scalar(select(TestRun).where(TestRun.id == other_run_id, TestRun.user_id == _user.id))
    if first is None or second is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    first_domain = (first.target_domain or urlsplit(first.target_url).hostname or "").lower()
    second_domain = (second.target_domain or urlsplit(second.target_url).hostname or "").lower()
    first_parts, second_parts = urlsplit(first.target_url), urlsplit(second.target_url)
    same_environment = (
        first_parts.scheme.lower(), first_parts.netloc.lower()
    ) == (
        second_parts.scheme.lower(), second_parts.netloc.lower()
    ) and first_parts.path.rstrip("/") == second_parts.path.rstrip("/")
    if not same_environment or first_domain != second_domain or first.flow != second.flow:
        return {"comparable": False, "reason": "Target website or tested feature differs.", "scenarios": []}
    try:
        first_plan = TestPlan.model_validate(first.plan_json, strict=False)
        second_plan = TestPlan.model_validate(second.plan_json, strict=False)
    except ValidationError:
        return {"comparable": False, "reason": "A legacy plan lacks structured scenario data.", "scenarios": []}
    first_tests = {item.scenario_id: item for item in first_plan.tests}
    second_tests = {item.scenario_id: item for item in second_plan.tests}
    shared_ids = sorted(first_tests.keys() & second_tests.keys())
    if not shared_ids or any(
        first_tests[item].expected_result != second_tests[item].expected_result
        or first_tests[item].prerequisites != second_tests[item].prerequisites
        for item in shared_ids
    ):
        return {"comparable": False, "reason": "No scenarios have matching stable IDs and expectations.", "scenarios": []}

    def result_map(run: TestRun, plan: TestPlan) -> dict[str, str]:
        execution = run.execution_json or {}
        by_id = {item.get("scenario_id"): item for item in execution.get("tests", []) if item.get("scenario_id")}
        by_name = {item.get("name"): item for item in execution.get("tests", [])}
        outcomes: dict[str, str] = {}
        for planned in plan.tests:
            raw_result = by_id.get(planned.scenario_id) or by_name.get(planned.name)
            if not raw_result:
                outcomes[planned.scenario_id] = "not_run"
            else:
                explicit_status = raw_result.get("scenario_status")
                if explicit_status in {"blocked", "inconclusive", "not_run"}:
                    outcomes[planned.scenario_id] = explicit_status
                    continue
                try:
                    executed = TestExecutionResult.model_validate(raw_result, strict=False)
                    outcome = evaluate_scenario(
                        planned, executed.assertions, executed.steps, executed.observations,
                        declared_status=executed.scenario_status,
                    )
                    outcomes[planned.scenario_id] = outcome.value
                except ValidationError:
                    outcomes[planned.scenario_id] = "inconclusive"
        return outcomes

    before, after = result_map(first, first_plan), result_map(second, second_plan)
    scenarios = []
    for scenario_id in shared_ids:
        old, new = before.get(scenario_id, "not_run"), after.get(scenario_id, "not_run")
        change = "unchanged" if old == new else "changed"
        if old in {"not_run", "blocked", "inconclusive"} or new in {"not_run", "blocked", "inconclusive"}:
            change = "insufficient_data"
        elif old == "fail" and new == "pass":
            change = "resolved"
        elif old == "pass" and new == "fail":
            change = "new_failure"
        scenarios.append({
            "scenario_id": scenario_id,
            "name": first_tests[scenario_id].name,
            "expected_result": first_tests[scenario_id].expected_result,
            "before": old,
            "after": new,
            "change": change,
        })
    removed_ids = sorted(first_tests.keys() - second_tests.keys())
    added_ids = sorted(second_tests.keys() - first_tests.keys())
    scenarios.extend({
        "scenario_id": scenario_id,
        "name": first_tests[scenario_id].name,
        "expected_result": first_tests[scenario_id].expected_result,
        "before": before.get(scenario_id, "not_run"),
        "after": "not_planned",
        "change": "removed",
    } for scenario_id in removed_ids)
    scenarios.extend({
        "scenario_id": scenario_id,
        "name": second_tests[scenario_id].name,
        "expected_result": second_tests[scenario_id].expected_result,
        "before": "not_planned",
        "after": after.get(scenario_id, "not_run"),
        "change": "added",
    } for scenario_id in added_ids)
    return {
        "comparable": True,
        "domain": first_domain,
        "feature": first.flow,
        "coverage": {
            "before": len(first_tests),
            "after": len(second_tests),
            "added": added_ids,
            "removed": removed_ids,
        },
        "scenarios": scenarios,
    }


@app.post("/api/suites", response_model=SavedTestSuiteResponse, status_code=201)
def save_suite(
    request: SavedTestSuiteRequest,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SavedTestSuiteResponse:
    try:
        plan = sanitize_plan_credentials(TestPlan.model_validate(request.plan, strict=False))
        validate_target_url(str(request.url))
    except (ValidationError, TargetScopeError) as exc:
        raise HTTPException(status_code=422, detail="The saved test suite is invalid.") from exc
    if request.source_run_id:
        source = db.scalar(select(TestRun.id).where(
            TestRun.id == request.source_run_id, TestRun.user_id == _user.id
        ))
        if source is None:
            raise HTTPException(status_code=404, detail="Source run not found.")
    suite = SavedTestSuite(
        id=uuid4().hex, user_id=_user.id, name=request.name.strip(),
        target_url=str(request.url), goal=request.goal.strip(),
        plan_json=plan.model_dump(mode="json"), source_run_id=request.source_run_id,
        created_at=int(time.time()),
    )
    db.add(suite)
    db.commit()
    return SavedTestSuiteResponse.model_validate({
        "id": suite.id, "name": suite.name, "url": suite.target_url,
        "goal": suite.goal, "plan": suite.plan_json,
        "source_run_id": suite.source_run_id, "created_at": suite.created_at,
    }, strict=False)


@app.get("/api/suites", response_model=list[SavedTestSuiteResponse])
def list_suites(
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[SavedTestSuiteResponse]:
    suites = db.scalars(select(SavedTestSuite).where(
        SavedTestSuite.user_id == _user.id
    ).order_by(SavedTestSuite.created_at.desc())).all()
    return [SavedTestSuiteResponse.model_validate({
        "id": item.id, "name": item.name, "url": item.target_url,
        "goal": item.goal, "plan": item.plan_json,
        "source_run_id": item.source_run_id, "created_at": item.created_at,
    }, strict=False) for item in suites]


@app.get("/api/suites/{suite_id}", response_model=SavedTestSuiteResponse)
def get_suite(
    suite_id: str,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SavedTestSuiteResponse:
    suite = db.scalar(select(SavedTestSuite).where(
        SavedTestSuite.id == suite_id, SavedTestSuite.user_id == _user.id
    ))
    if suite is None:
        raise HTTPException(status_code=404, detail="Test suite not found.")
    return SavedTestSuiteResponse.model_validate({
        "id": suite.id, "name": suite.name, "url": suite.target_url,
        "goal": suite.goal, "plan": suite.plan_json,
        "source_run_id": suite.source_run_id, "created_at": suite.created_at,
    }, strict=False)
def _log_report_http_error(exc: Exception) -> None:
    print("REPORT HTTP ERROR", flush=True)
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        print(
            f"{type(current).__name__}: {safe_log_message(str(current))}",
            flush=True,
        )
        current = current.__cause__


@app.get("/api/evidence/{run_id}/{filename}", response_class=FileResponse)
def serve_evidence(
    run_id: str,
    filename: str,
    _user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FileResponse:
    owned_evidence = db.scalar(
        select(TestRun.id).where(
            TestRun.evidence_run_id == run_id,
            TestRun.user_id == _user.id,
        )
    )
    if owned_evidence is None:
        raise HTTPException(status_code=404, detail="Evidence image not found.")

    evidence_file = resolve_evidence_file(run_id, filename)
    if evidence_file is None:
        raise HTTPException(status_code=404, detail="Evidence image not found.")
    return FileResponse(
        path=evidence_file,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store"},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
    }


@app.post(
    "/api/test-plan",
    response_model=TestPlan,
)
def create_plan(
    request: TestPlanRequest,
    _user: Annotated[User, Depends(require_ai_access)],
) -> TestPlan:

    try:
        validate_target_url(str(request.url))
        return create_test_plan(
            str(request.url),
            request.goal,
            user_id=_user.id,
        )

    except UsageLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except AIUserNotApproved as exc:
        raise HTTPException(status_code=403, detail="AI access is not enabled for this account.") from exc
    except PlannerConfigurationError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        ) from exc

    except UnsupportedFlowError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    except TargetScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except PlannerOutputError as exc:
        raise HTTPException(
            status_code=502,
              detail=(
                "The planner returned an invalid structured plan after bounded correction attempts. "
                "Clarify the requested scenarios and try again."
            ),
        ) from exc

    except PlannerProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "The planner provider did not return a usable response. "
                "Please try again shortly."
            ),
        ) from exc


@app.post(
    "/api/execute-plan",
    response_model=ExecutionResult,
)
def execute_plan(
    request: ExecutePlanRequest,
    _user: Annotated[User, Depends(require_ai_access)],
    db: Annotated[Session, Depends(get_db)],
) -> ExecutionResult:

    try:
        plan = TestPlan.model_validate(
            request.plan,
            strict=False,
        )
        plan = sanitize_plan_credentials(plan)

    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="The supplied test plan is invalid.",
        ) from exc

    if request.selected_scenario_ids and not set(request.selected_scenario_ids).issubset(
        {test.scenario_id for test in plan.tests}
    ):
        raise HTTPException(status_code=422, detail="Selected scenarios are not part of this plan.")

    selected_tests = [
        test for test in plan.tests
        if not request.selected_scenario_ids or test.scenario_id in request.selected_scenario_ids
    ]
    verification_issues = [
        f"{test.name}: {issue}"
        for test in selected_tests
        for issue in scenario_verification_issues(test, plan.flow)
    ]
    if verification_issues:
        raise HTTPException(
            status_code=422,
            detail=(
                "Selected scenarios need supported verification before execution: "
                + "; ".join(verification_issues)
            ),
        )
    required_categories = {
        item.value
        for test in selected_tests
        for item in test.prerequisites
    }
    required_categories.update(
        category
        for test in selected_tests
        for category in re.findall(
            r"\{\{(valid_test_email|valid_test_password|negative_test_email|negative_test_password)\}\}",
            " ".join(step.value or "" for step in test.steps),
        )
    )
    requires_reference = bool(required_categories & {"valid_test_email", "valid_test_password"})
    missing_reference = sorted(
        required_categories.intersection({"valid_test_email", "valid_test_password"})
        - set(request.test_data)
    )
    if missing_reference:
        raise HTTPException(status_code=422, detail="Supply the required valid test account credentials.")
    if requires_reference and not request.authorized_test_account:
        raise HTTPException(status_code=422, detail="Confirm you are authorized to use this test account.")
    wrong_password_attempts = sum(
        "valid_test_email" in {item.value for item in test.prerequisites}
        and "negative_test_password" in {item.value for item in test.prerequisites}
        for test in selected_tests
    )
    if wrong_password_attempts > 1:
        raise HTTPException(status_code=422, detail="Only one wrong-password attempt per valid account is allowed per run.")

    try:
        validate_target_url(str(request.url))
        if request.source_run_id:
            source = db.scalar(select(TestRun.id).where(
                TestRun.id == request.source_run_id, TestRun.user_id == _user.id
            ))
            if source is None:
                raise HTTPException(status_code=404, detail="Source run not found.")
        usage = UsageLedger(engine)
        with usage.reserve_browser_execution(_user.id) as execution_lease:
            execution_lease.mark_started()
            execution = execute_test_plan(
                url=str(request.url),
                plan=plan,
                goal=request.goal,
                test_data=request.test_data,
                selected_scenario_ids=request.selected_scenario_ids,
                user_id=_user.id,
            )
            execution_lease.mark_completed()
        if execution.effective_plan is not None:
            execution = execution.model_copy(update={
                "effective_plan": sanitize_plan_credentials(execution.effective_plan)
            })
        created_at = int(time.time())
        db.add(TestRun(
            id=execution.run_id,
            user_id=_user.id,
            target_url=str(request.url),
            goal=request.goal or plan.summary,
            flow=plan.flow.value,
            status=AnalysisStatus.INCONCLUSIVE.value,
            created_at=created_at,
            completed_at=created_at,
            plan_json=plan.model_dump(mode="json"),
            execution_json=execution.model_dump(mode="json"),
            analysis_json={},
            report_json={},
            evidence_run_id=execution.run_id,
            agent_status=execution.agent_status.value,
            target_domain=(urlsplit(str(request.url)).hostname or "").lower(),
            scope=plan.scope.value,
            source_run_id=request.source_run_id,
        ))
        db.commit()
        return execution

    except BrowserExecutionError as exc:
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    except TargetScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except UsageLimitExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    except LeaseLost as exc:
        raise HTTPException(status_code=503, detail="The active execution reservation was lost.") from exc

    except AIUserNotApproved as exc:
        raise HTTPException(status_code=403, detail="AI access is not enabled for this account.") from exc
