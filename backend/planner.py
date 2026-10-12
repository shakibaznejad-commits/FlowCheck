import os
import re
import json

from pydantic import ValidationError

from .llm_provider import OpenAIProvider, ProviderRequestError
from .ai_usage import UsageLimitExceeded
from .planner_prompt import PLANNER_SYSTEM_PROMPT
from .sensitive_data import sanitize_plan_credentials
from .schemas import (
    AssertionKind,
    FlowType,
    PlanScope,
    PlannerOutput,
    PrerequisiteType,
    TestPlan,
    ActionType,
    MAX_PLAN_SCENARIOS,
)


class PlannerConfigurationError(Exception):
    """Raised when the planner provider is not configured."""


class PlannerOutputError(Exception):
    """Raised when the provider returns a plan that fails schema validation."""


class PlannerProviderError(Exception):
    """Raised when the external planner provider request fails."""


class UnsupportedFlowError(Exception):
    """Raised when a request is outside the login/signup MVP scope."""


def _goal_requests_empty_field(goal: str, field: str) -> bool:
    goal = goal.casefold()
    if field == "email":
        field_terms = r"email|e-mail|ایمیل"
    else:
        field_terms = r"password|passcode|رمز\s+عبور|گذرواژه"
    if re.search(
        rf"\b(?:empty|blank|missing)\s+(?:{field_terms})\b|"
        rf"\b(?:{field_terms})\s+(?:field\s+)?(?:is\s+)?(?:empty|blank|missing)\b|"
        rf"(?:{field_terms})\s+خالی|خالی\s+(?:بودن\s+)?(?:{field_terms})",
        goal,
    ):
        return True
    return bool(
        re.search(
            r"\b(?:empty|blank|missing)\s+(?:form\s+)?fields?\b|"
            r"\bfields?\s+(?:are\s+)?(?:empty|blank|missing)\b|"
            r"فیلد(?:ها|های)?\s+خالی|خالی\s+بودن\s+فیلد(?:ها|های)?",
            goal,
        )
    )


def _scenario_describes_empty_field(test, field: str) -> bool:
    description = " ".join(
        (test.name, test.goal, test.description, test.expected_result)
    ).casefold()
    if field == "email":
        terms = r"email|e-mail|ایمیل"
    else:
        terms = r"password|passcode|رمز\s+عبور|گذرواژه"
    return bool(
        re.search(
            rf"\bempty\s+(?:{terms})\b|\b(?:{terms})\s+(?:field\s+)?(?:is\s+)?empty\b|"
            rf"\bblank\s+(?:{terms})\b|\b(?:{terms})\s+(?:field\s+)?(?:is\s+)?blank\b|"
            rf"(?:{terms})\s+(?:فیلد\s+)?خالی|خالی\s+(?:بودن\s+)?(?:{terms})",
            description,
        )
    )


def _verification_issues(
    plan: TestPlan,
    scope: PlanScope,
    goal: str | None = None,
) -> list[str]:
    issues: list[str] = []
    for index, test in enumerate(plan.tests, start=1):
        issues.extend(
            f"scenario {index} {issue}"
            for issue in scenario_verification_issues(test, plan.flow)
        )

        description = " ".join(
            (test.name, test.goal, test.description, test.expected_result)
        ).casefold()
        empty_fields = {
            "email": bool(
                re.search(r"\bempty\s+email\b|\bemail\s+(?:field\s+)?(?:is\s+)?empty\b", description)
                or re.search(r"ایمیل\s+خالی", description)
            ),
            "password": bool(
                re.search(r"\bempty\s+password\b|\bpassword\s+(?:field\s+)?(?:is\s+)?empty\b", description)
                or re.search(r"رمز\s+عبور\s+خالی", description)
            ),
        }
        for field, is_empty in empty_fields.items():
            if not is_empty:
                continue
            prerequisite = (
                PrerequisiteType.VALID_TEST_EMAIL
                if field == "email"
                else PrerequisiteType.VALID_TEST_PASSWORD
            )
            legacy_prerequisite = (
                PrerequisiteType.TEST_EMAIL
                if field == "email"
                else PrerequisiteType.TEST_PASSWORD
            )
            if prerequisite in test.prerequisites or legacy_prerequisite in test.prerequisites:
                issues.append(f"scenario {index} requires {field} even though its goal leaves it empty")
            for step in test.steps:
                if step.action == ActionType.FILL and field in step.target.casefold() and (step.value or "").strip():
                    issues.append(f"scenario {index} fills {field} even though its goal leaves it empty")

    if plan.flow == FlowType.LOGIN and scope == PlanScope.BROAD:
        scenario_text = " ".join(
            " ".join((test.name, test.goal, test.description, test.expected_result))
            for test in plan.tests
        ).casefold()
        has_unknown_email = bool(
            re.search(r"\b(unknown|unregistered|not registered|email not found)\b", scenario_text)
            or re.search(r"ایمیل\s+(?:ناشناخته|ثبت.?نشده)", scenario_text)
        )
        has_known_wrong_password = bool(
            re.search(
                r"\b(known|registered|existing)\b.{0,60}\b(wrong|incorrect|invalid)\s+password\b|"
                r"\b(wrong|incorrect|invalid)\s+password\b.{0,60}\b(known|registered|existing)\b",
                scenario_text,
            )
            or re.search(r"رمز\s+عبور\s+(?:اشتباه|نادرست)|گذرواژه\s+(?:اشتباه|نادرست)", scenario_text)
        )
        successful_candidates = [
            test for test in plan.tests
            if re.search(
                r"\b(successful\s+login|valid\s+credentials|successful\s+authentication|login\s+succeeds)\b|"
                r"ورود\s+موفق|اطلاعات\s+(?:ورود\s+)?صحیح",
                " ".join((test.name, test.goal, test.description, test.expected_result)).casefold(),
            )
        ]
        has_successful_login = any(
            any(assertion.kind == AssertionKind.LOGIN_FORM_HIDDEN for assertion in test.assertions)
            and PrerequisiteType.VALID_TEST_EMAIL in test.prerequisites
            and PrerequisiteType.VALID_TEST_PASSWORD in test.prerequisites
            for test in successful_candidates
        )
        if not has_unknown_email:
            issues.append("broad login coverage is missing an independent unregistered-email scenario")
        if not has_known_wrong_password:
            issues.append("broad login coverage is missing a known-email/wrong-password scenario")
        if not has_successful_login:
            issues.append(
                "broad login coverage is missing a successful-login scenario with valid test-account prerequisites and a verified transition"
            )

    if plan.flow == FlowType.LOGIN and goal:
        for field in ("email", "password"):
            if _goal_requests_empty_field(goal, field) and not any(
                _scenario_describes_empty_field(test, field)
                for test in plan.tests
            ):
                issues.append(
                    f"requested login coverage is missing an independent empty-{field} scenario"
                )

    return issues


def scenario_verification_issues(test, flow: FlowType) -> list[str]:
    """Require each executable case to verify an outcome relevant to its intent."""
    issues: list[str] = []
    if not test.assertions:
        issues.append("has no executable assertion")
        return issues

    description = " ".join(
        (test.name, test.goal, test.description, test.expected_result)
    ).casefold()
    empty_email = bool(
        re.search(r"\bempty\s+email\b|\bemail\s+(?:field\s+)?(?:is\s+)?empty\b", description)
        or re.search(r"ایمیل\s+خالی", description)
    )
    empty_password = bool(
        re.search(r"\bempty\s+password\b|\bpassword\s+(?:field\s+)?(?:is\s+)?empty\b", description)
        or re.search(r"رمز\s+عبور\s+خالی|گذرواژه\s+خالی", description)
    )
    if flow == FlowType.LOGIN:
        legacy_rejection_check = (
            any(item.kind == AssertionKind.LOGIN_FORM_VISIBLE for item in test.assertions)
            and any(item.kind == AssertionKind.STATUS_VISIBLE for item in test.assertions)
        )
        email_verified = any(
            assertion.kind in {AssertionKind.CONTROL_INVALID, AssertionKind.LOGIN_REJECTED}
            and "email" in (assertion.target or "").casefold()
            for assertion in test.assertions
        )
        password_verified = any(
            assertion.kind in {AssertionKind.CONTROL_INVALID, AssertionKind.LOGIN_REJECTED}
            and any(word in (assertion.target or "").casefold() for word in ("password", "passcode"))
            for assertion in test.assertions
        )
        if empty_email and not (email_verified or legacy_rejection_check):
            issues.append("needs a control_invalid assertion for Email")
        if empty_password and not (password_verified or legacy_rejection_check):
            issues.append("needs a control_invalid assertion for Password")

        logout_case = bool(
            re.search(r"\b(log\s*out|logout|sign\s*out|signout|signed\s*out)\b", description)
        )
        positive_case = not logout_case and bool(
            re.search(
                r"\b(successful\s+login|valid\s+credentials|successful\s+authentication|login\s+succeeds)\b|"
                r"ورود\s+موفق|اطلاعات\s+(?:ورود\s+)?صحیح",
                description,
            )
        )
        if positive_case and not any(
            assertion.kind == AssertionKind.LOGIN_FORM_HIDDEN
            for assertion in test.assertions
        ):
            issues.append("needs a login_form_hidden assertion to verify successful authentication")
    return issues


def _normalize_alternative_empty_field_checks(plan: TestPlan) -> TestPlan:
    """Keep an OR-style rejection check from becoming an unintended AND check."""
    tests = []
    changed = False
    for test in plan.tests:
        expected = test.expected_result.casefold()
        has_alternative_outcome = bool(
            re.search(r"\b(?:or|either)\b|یا", expected)
        )
        rejection_targets = {
            (item.target or "").casefold()
            for item in test.assertions
            if item.kind == AssertionKind.LOGIN_REJECTED
        }
        if has_alternative_outcome and rejection_targets:
            assertions = [
                item for item in test.assertions
                if not (
                    item.kind == AssertionKind.CONTROL_INVALID
                    and (item.target or "").casefold() in rejection_targets
                )
            ]
            if len(assertions) != len(test.assertions):
                changed = True
                tests.append(test.model_copy(update={"assertions": assertions}))
                continue
        tests.append(test)
    return plan.model_copy(update={"tests": tests}) if changed else plan


def detect_scope(goal: str) -> PlanScope:
    """Classify how specifically the user described the requested checks."""
    focused = re.search(
        r"(?<!not )\b(?:only|just)\s+(?:test|check|verify|inspect|exercise|run)\b|"
        r"\b(?:test|check|verify|inspect|exercise|run)\s+only\b|"
        r"\b(?:single|one|exactly\s+(?:one|a\s+single))\s+(?:[a-z-]+\s+){0,3}(?:case|scenario)\b|"
        r"(?<!نه )(?:فقط|تنها|صرفاً|صرفا|همین\s+حالت)",
        goal,
        re.IGNORECASE,
    )
    if focused:
        return PlanScope.FOCUSED

    broad = re.search(
        r"\b(functionality|comprehensive|overall|entire|complete|full|whole|"
        r"all\s+(?:login|signup|sign[ -]?in|sign[ -]?up)\s+(?:flow|functionality|scenarios|cases)|"
        r"all\s+(?:relevant\s+)?(?:scenarios|cases)|any\s+problems)\b|"
        r"همه(?:ی|ٔ)?\s+(?:حالت|موارد|جریان|سناریوها)|کامل|عملکرد|"
        r"بخش\s+(?:لاگین|ورود|ثبت.?نام)",
        goal,
        re.IGNORECASE,
    )
    if broad:
        return PlanScope.BROAD

    concrete_case = re.search(
        r"\b(empty\s+(?:email|password)|invalid\s+(?:email|password|credentials)|"
        r"wrong\s+password|unknown\s+email|valid\s+credentials)\b|"
        r"ایمیل\s+خالی|رمز\s+عبور\s+خالی|رمز\s+اشتباه|ایمیل\s+ناشناخته|"
        r"اطلاعات\s+ورود\s+نامعتبر",
        goal,
        re.IGNORECASE,
    )
    if concrete_case:
        return PlanScope.FOCUSED

    return PlanScope.AMBIGUOUS


def _scope_count_issue(scope: PlanScope, scenario_count: int) -> str | None:
    if scope == PlanScope.BROAD and not 2 <= scenario_count <= MAX_PLAN_SCENARIOS:
        return (
            f"broad scope requires 2 to {MAX_PLAN_SCENARIOS} independent scenarios; "
            f"the response contained {scenario_count}"
        )
    if scope == PlanScope.FOCUSED and scenario_count != 1:
        return (
            "focused scope requires exactly one scenario; "
            f"the response contained {scenario_count}"
        )
    if scope == PlanScope.AMBIGUOUS and scenario_count != 1:
        return (
            "ambiguous scope allows at most one candidate scenario; "
            f"the response contained {scenario_count}"
        )
    return None


def _structured_repair_message(
    url: str,
    scope: PlanScope,
    goal: str,
    issue: str,
) -> str:
    return (
        f"Target URL: {url}\nRequested scope: {scope.value}\nTesting goal: {goal}\n"
        f"The previous response did not satisfy this plan constraint: {issue}.\n"
        "Regenerate the complete plan from the user's goal. Preserve every requested "
        "scenario category. Do not remove scenarios or change the requested scope just "
        "to satisfy the schema. Return only valid JSON that matches the required schema."
    )


def _requested_flow(goal: str) -> FlowType:
    signup = re.search(
        r"\b(sign[\s-]?up|register(?:ed|ing|s|ation)?|create\s+(?:an?\s+)?account|new\s+account)\b|"
        r"ثبت\s*نام|نام\s*نویسی|ساخت\s+(?:یک\s+)?حساب(?:\s+کاربری)?",
        goal,
        re.IGNORECASE,
    )

    login = re.search(
        r"\b(log[\s-]?in|logging\s+in|sign[\s-]?in|authenticate|authentication|existing\s+account\s+access)\b|"
        r"ورود|لاگین",
        goal,
        re.IGNORECASE,
    )

    if bool(login) == bool(signup):
        raise UnsupportedFlowError(
            "Only single-flow login and signup plans are supported. "
            "Describe one login or signup goal."
        )

    return FlowType.LOGIN if login else FlowType.SIGNUP


def create_test_plan(url: str, goal: str, user_id: int | None = None) -> TestPlan:
    requested_flow = _requested_flow(goal)
    scope = detect_scope(goal)

    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise PlannerConfigurationError("LLM_API_KEY is not set.")

    model = os.getenv("LLM_MODEL", "cx/gpt-6-luna")

    base_url = os.getenv(
        "LLM_BASE_URL",
        "http://host.docker.internal:20128/v1",
    )

    try:
        provider = OpenAIProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
            user_id=user_id,
        )
        schema = PlannerOutput.model_json_schema()
        user_message = (
            f"Target URL: {url}\n"
            f"Requested scope: {scope.value}\n"
            f"Testing goal: {goal}"
        )
        plan: TestPlan | None = None
        for repair_attempt in range(3):
            content = provider.generate_json(
                system_prompt=PLANNER_SYSTEM_PROMPT,
                user_message=user_message,
                schema=schema,
                schema_name="flowcheck_test_plan",
            )
            print(
                f"PLANNER RESPONSE RECEIVED bytes={len(content.encode('utf-8'))}",
                flush=True,
            )
            try:
                generated = PlannerOutput.model_validate_json(content, strict=True)
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
                print("\n========== VALIDATION ERROR ==========", flush=True)
                if isinstance(exc, ValidationError):
                    print(exc.errors(include_input=False), flush=True)
                else:
                    print(f"{type(exc).__name__}: {exc}", flush=True)
                print("======================================\n", flush=True)
                if repair_attempt >= 2:
                    raise PlannerOutputError(
                        "Planner response remained invalid after two structured repairs."
                    ) from exc
                validation_issues = []
                if isinstance(exc, ValidationError):
                    validation_issues = [
                        {
                            "field": ".".join(str(part) for part in item.get("loc", ())),
                            "issue": item.get("msg", "invalid value"),
                        }
                        for item in exc.errors(include_input=False)
                    ]
                user_message = _structured_repair_message(
                    url,
                    scope,
                    goal,
                    "the previous response did not match the required schema; "
                    f"correct these validation issues: {json.dumps(validation_issues, ensure_ascii=False)}",
                )
                continue

            scope_issue = _scope_count_issue(scope, len(generated.tests))
            if scope_issue:
                if repair_attempt:
                    print(
                        "PLANNER REPAIR EXHAUSTED reason=scope_count "
                        f"scope={scope.value} scenarios={len(generated.tests)}",
                        flush=True,
                    )
                    raise PlannerOutputError(
                        "Planner returned a scenario count inconsistent with the requested scope after one repair."
                    )
                user_message = _structured_repair_message(
                    url, scope, goal, scope_issue
                )
                continue

            plan_payload = generated.model_dump(mode="json")
            plan_payload["scope"] = scope.value
            try:
                plan = TestPlan.model_validate_json(
                    json.dumps(plan_payload, ensure_ascii=False),
                    strict=True,
                )
            except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
                print("\n========== PLAN CONSTRAINT ERROR ==========", flush=True)
                if isinstance(exc, ValidationError):
                    print(exc.errors(include_input=False), flush=True)
                else:
                    print(f"{type(exc).__name__}: {exc}", flush=True)
                print("===========================================\n", flush=True)
                if repair_attempt:
                    raise PlannerOutputError(
                        "Planner response still violates a plan constraint after one repair."
                    ) from exc
                user_message = _structured_repair_message(
                    url,
                    scope,
                    goal,
                    "the response violated a scenario ID or action-budget constraint",
                )
                continue

            plan = _normalize_alternative_empty_field_checks(plan)
            issues = _verification_issues(plan, scope, goal=goal)
            if not issues:
                break
            if repair_attempt:
                print(
                    "PLANNER REPAIR EXHAUSTED reason=verification "
                    f"issues={json.dumps(issues, ensure_ascii=False)}",
                    flush=True,
                )
                raise PlannerOutputError(
                    "Planner returned an unverifiable or inconsistent scenario after one repair."
                )
            safe_plan = sanitize_plan_credentials(plan).model_dump(
                mode="json", exclude_none=False
            )
            user_message = (
                f"Target URL: {url}\nRequested scope: {scope.value}\n"
                f"Testing goal: {goal}\n"
                "Repair required without changing the requested scope or adding "
                "unrelated scenarios. Add any explicitly requested scenario categories "
                "that are missing, and give every scenario supported, goal-relevant "
                "assertions. Do not invent page copy. If a goal says a field is empty, "
                "omit its fill action and do not list it as a prerequisite. Issues: "
                f"{'; '.join(issues)}\n"
                "Prior validated plan (repair only missing verification):\n"
                f"{json.dumps(safe_plan, ensure_ascii=False)}"
                )

    except ProviderRequestError as exc:
        print(
            "PLANNER PROVIDER ERROR "
            f"type={type(exc).__name__} retryable={getattr(exc, 'retryable', False)}",
            flush=True,
        )
        raise PlannerProviderError("The planner provider request failed.") from exc

    assert plan is not None

    if plan.flow != requested_flow:
        print(
            f"FLOW MISMATCH: expected={requested_flow}, got={plan.flow}",
            flush=True,
        )

        raise PlannerOutputError(
            "Planner response selected a flow that does not match the goal."
        )

    print(
        f"PLANNER VALIDATED flow={plan.flow.value} tests={len(plan.tests)}",
        flush=True,
    )

    return sanitize_plan_credentials(plan)
