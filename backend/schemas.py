from enum import Enum
import hashlib
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


MAX_PLAN_SCENARIOS = 6
MAX_BROWSER_ACTIONS = 30


class AuthCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254, strict=True)
    password: str = Field(min_length=12, max_length=256, strict=True)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized


class AuthUser(BaseModel):
    id: int
    email: str


class FlowType(str, Enum):
    LOGIN = "login"
    SIGNUP = "signup"


class PlanScope(str, Enum):
    BROAD = "broad"
    FOCUSED = "focused"
    AMBIGUOUS = "ambiguous"


class PrerequisiteType(str, Enum):
    VALID_TEST_EMAIL = "valid_test_email"
    VALID_TEST_PASSWORD = "valid_test_password"
    NEGATIVE_TEST_EMAIL = "negative_test_email"
    NEGATIVE_TEST_PASSWORD = "negative_test_password"
    # Kept for plans saved before credential categories were introduced.
    TEST_EMAIL = "test_email"
    TEST_PASSWORD = "test_password"


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    FILL = "fill"
    CLICK = "click"
    CHECK = "check"
    SUBMIT = "submit"


class StepExecutionStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


class TextExpectation(str, Enum):
    VISIBLE = "visible"
    NOT_VISIBLE = "not_visible"


class AssertionKind(str, Enum):
    TEXT = "text"
    CONTROL_INVALID = "control_invalid"
    STATUS_VISIBLE = "status_visible"
    LOGIN_FORM_VISIBLE = "login_form_visible"
    LOGIN_FORM_HIDDEN = "login_form_hidden"
    LOGIN_REJECTED = "login_rejected"
    LOGIN_AUTHENTICATED = "login_authenticated"


class AnalysisStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class ScenarioStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"
    NOT_RUN = "not_run"


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class InvestigationChoice(str, Enum):
    CONTINUE = "continue"
    INSPECT = "inspect"
    RETRY = "retry"
    ALTERNATIVE_PATH = "alternative_path"
    NEW_TEST = "new_test"
    FINISH = "finish"


class ReportSeverity(str, Enum):
    INFO = "info"
    MEDIUM = "medium"
    HIGH = "high"
    NOT_ASSESSED = "not_assessed"


class ReportNarrativeSource(str, Enum):
    PROVIDER = "provider"
    FALLBACK = "fallback"


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
    )


class TestPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl

    goal: str = Field(
        min_length=1,
        max_length=2000,
        strict=True,
    )

    @field_validator("url")
    @classmethod
    def reject_url_secrets(cls, value: HttpUrl) -> HttpUrl:
        from .url_policy import reject_sensitive_query
        reject_sensitive_query(str(value))
        return value

    @field_validator("goal")
    @classmethod
    def goal_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        from .sensitive_data import sanitize_goal
        return sanitize_goal(value.strip())


class PlannedStep(StrictModel):
    action: ActionType

    target: str = Field(
        min_length=1,
        max_length=300,
    )

    value: str | None = Field(
        max_length=500,
    )


class TextAssertion(StrictModel):
    kind: AssertionKind = AssertionKind.TEXT
    text: str = Field(default="", max_length=200)
    target: str | None = Field(default=None, max_length=120)
    expectation: TextExpectation = TextExpectation.VISIBLE
    timeout_ms: int = Field(ge=250, le=10_000)

    @model_validator(mode="after")
    def validate_assertion_target(self) -> "TextAssertion":
        if self.kind == AssertionKind.TEXT and not self.text.strip():
            raise ValueError("Text assertions require non-empty text.")
        if self.kind == AssertionKind.TEXT and self.target is not None:
            raise ValueError("Text assertions do not accept a target.")
        if self.kind == AssertionKind.CONTROL_INVALID and not (self.target or "").strip():
            raise ValueError("Control validity assertions require a control label.")
        if self.kind == AssertionKind.CONTROL_INVALID and self.expectation != TextExpectation.VISIBLE:
            raise ValueError("Control validity assertions use their structured invalid state.")
        if self.kind == AssertionKind.LOGIN_REJECTED and not (self.target or "").strip():
            raise ValueError("Login rejection assertions require the relevant control label.")
        if self.kind in {
            AssertionKind.STATUS_VISIBLE,
            AssertionKind.LOGIN_FORM_VISIBLE,
            AssertionKind.LOGIN_FORM_HIDDEN,
            AssertionKind.LOGIN_REJECTED,
            AssertionKind.LOGIN_AUTHENTICATED,
        } and self.text.strip():
            raise ValueError(f"{self.kind.value} assertions must not include literal text.")
        if self.kind in {
            AssertionKind.STATUS_VISIBLE,
            AssertionKind.LOGIN_FORM_VISIBLE,
            AssertionKind.LOGIN_FORM_HIDDEN,
            AssertionKind.LOGIN_AUTHENTICATED,
        } and self.target is not None:
            raise ValueError(f"{self.kind.value} assertions do not accept a target.")
        if self.kind in {AssertionKind.LOGIN_REJECTED, AssertionKind.LOGIN_AUTHENTICATED} and self.expectation != TextExpectation.VISIBLE:
            raise ValueError(f"{self.kind.value} assertions use their structured verification state.")
        if self.kind in {
            AssertionKind.STATUS_VISIBLE,
            AssertionKind.LOGIN_FORM_VISIBLE,
            AssertionKind.LOGIN_FORM_HIDDEN,
        } and self.expectation != TextExpectation.VISIBLE:
            raise ValueError(f"{self.kind.value} assertions use their structured visibility state.")
        return self


class PlannedTest(StrictModel):
    scenario_id: str = Field(default="", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    name: str = Field(
        min_length=1,
        max_length=160,
    )

    goal: str = Field(default="", max_length=500)
    prerequisites: list[PrerequisiteType] = Field(default_factory=list, max_length=5)

    description: str = Field(
        min_length=1,
        max_length=500,
    )

    steps: list[PlannedStep] = Field(
        min_length=1,
        max_length=20,
    )

    expected_result: str = Field(
        min_length=1,
        max_length=500,
    )

    assertions: list[TextAssertion] = Field(max_length=2)

    @model_validator(mode="after")
    def assertions_follow_submission(self) -> "PlannedTest":
        if self.assertions and self.steps[-1].action not in {
            ActionType.CLICK,
            ActionType.SUBMIT,
        }:
            raise ValueError("Text assertions require a final click or submit step.")
        return self

    @model_validator(mode="after")
    def assign_legacy_scenario_id(self) -> "PlannedTest":
        if not self.scenario_id:
            canonical = "\n".join(
                (
                    " ".join(self.name.casefold().split()),
                    " ".join(self.description.casefold().split()),
                    " ".join(self.expected_result.casefold().split()),
                )
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
            self.scenario_id = f"scenario-{digest}"
        return self


class GeneratedPlannedTest(StrictModel):
    """Provider-facing version: every output field is required and no ID is LLM-authored."""
    name: str = Field(min_length=1, max_length=160)
    goal: str = Field(max_length=500)
    prerequisites: list[PrerequisiteType] = Field(max_length=5)
    description: str = Field(min_length=1, max_length=500)
    steps: list[PlannedStep] = Field(min_length=1, max_length=20)
    expected_result: str = Field(min_length=1, max_length=500)
    assertions: list[TextAssertion] = Field(max_length=2)


class PlannerOutput(StrictModel):
    flow: FlowType
    summary: str = Field(min_length=1, max_length=500)
    tests: list[GeneratedPlannedTest] = Field(min_length=1, max_length=6)

    @model_validator(mode="before")
    @classmethod
    def mark_omitted_assertions_for_repair(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("tests"), list):
            value = dict(value)
            value["tests"] = [
                {**test, "assertions": []}
                if isinstance(test, dict) and "assertions" not in test
                else test
                for test in value["tests"]
            ]
        return value


class InvestigatorOutput(StrictModel):
    decision: InvestigationChoice
    rationale: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(min_length=1, max_length=5)
    actions: list[PlannedStep] = Field(max_length=3)
    new_test: GeneratedPlannedTest | None


class AgentDecision(StrictModel):
    decision: InvestigationChoice
    rationale: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(min_length=1, max_length=5)
    actions: list[PlannedStep] = Field(max_length=3)
    new_test: PlannedTest | None

    @model_validator(mode="after")
    def validate_decision_payload(self) -> "AgentDecision":
        if self.decision == InvestigationChoice.ALTERNATIVE_PATH:
            if not self.actions or self.new_test is not None:
                raise ValueError("alternative_path requires structured actions only.")
            if self.actions[-1].action not in {ActionType.CLICK, ActionType.SUBMIT}:
                raise ValueError("alternative_path must end with a click or submit action.")
        elif self.decision == InvestigationChoice.NEW_TEST:
            if self.new_test is None or self.actions:
                raise ValueError("new_test requires one structured test only.")
        elif self.actions or self.new_test is not None:
            raise ValueError("This decision cannot include actions or a new test.")
        return self


class InvestigationRecord(StrictModel):
    decision: InvestigationChoice
    rationale: str = Field(min_length=1, max_length=600)
    evidence: list[str] = Field(max_length=5)
    outcome: str = Field(min_length=1, max_length=300)


class TestPlan(StrictModel):
    flow: FlowType
    scope: PlanScope = PlanScope.FOCUSED

    summary: str = Field(
        min_length=1,
        max_length=500,
    )

    tests: list[PlannedTest] = Field(
        min_length=1,
        max_length=MAX_PLAN_SCENARIOS,
    )

    @model_validator(mode="before")
    @classmethod
    def default_legacy_assertions(cls, value: object) -> object:
        if isinstance(value, dict) and isinstance(value.get("tests"), list):
            value = dict(value)
            value["tests"] = [
                {**test, "assertions": []}
                if isinstance(test, dict) and "assertions" not in test
                else test
                for test in value["tests"]
            ]
        return value

    @model_validator(mode="after")
    def cap_total_actions(self) -> "TestPlan":
        if self.scope == PlanScope.BROAD and len(self.tests) < 2:
            raise ValueError("broad scope requires multiple scenarios")
        if self.scope == PlanScope.FOCUSED and len(self.tests) != 1:
            raise ValueError("focused scope requires exactly one scenario")
        scenario_ids = [test.scenario_id for test in self.tests]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenario IDs must be unique within a plan")
        if sum(len(test.steps) for test in self.tests) > MAX_BROWSER_ACTIONS:
            raise ValueError(
                f"A test plan cannot contain more than {MAX_BROWSER_ACTIONS} browser actions."
            )
        return self


class ControlObservation(StrictModel):
    label: str = Field(max_length=120)
    kind: str = Field(max_length=32)
    visible: bool
    enabled: bool
    required: bool = False
    aria_invalid: bool | None = None
    valid: bool | None = None
    validation_message: str | None = Field(max_length=200)


class ButtonObservation(StrictModel):
    label: str = Field(max_length=120)
    visible: bool
    enabled: bool


class PageObservation(StrictModel):
    step_index: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=32)
    url: str = Field(max_length=2048)
    title: str = Field(max_length=200)
    visible_text: str = Field(max_length=600)
    controls: list[ControlObservation] = Field(max_length=12)
    buttons: list[ButtonObservation] = Field(max_length=10)
    alerts: list[str] = Field(max_length=5)
    navigation_changes: list[str] = Field(max_length=5)
    console_errors: list[str] = Field(max_length=5)
    failed_requests: list[str] = Field(max_length=5)
    http_errors: list[str] = Field(max_length=5)
    evidence: list[str] = Field(max_length=20)
    collection_errors: list[str] = Field(default_factory=list, max_length=8)


class ExecutePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    plan: dict
    goal: str | None = Field(default=None, max_length=2000, strict=True)
    source_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    test_data: dict[str, str] = Field(default_factory=dict)
    selected_scenario_ids: list[str] = Field(default_factory=list, max_length=6)
    authorized_test_account: bool = False

    @field_validator("url")
    @classmethod
    def reject_url_secrets(cls, value: HttpUrl) -> HttpUrl:
        from .url_policy import reject_sensitive_query
        reject_sensitive_query(str(value))
        return value

    @field_validator("goal")
    @classmethod
    def sanitize_optional_goal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from .sensitive_data import sanitize_goal
        return sanitize_goal(value)

    @field_validator("test_data")
    @classmethod
    def validate_test_data(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {
            "test_email", "test_password", "valid_test_email", "valid_test_password",
            "negative_test_email", "negative_test_password",
        }
        if set(value) - allowed:
            raise ValueError("Unsupported test data category.")
        if any(not item or len(item) > 256 for item in value.values()):
            raise ValueError("Test values must be non-empty and at most 256 characters.")
        for positive, negative in (
            ("valid_test_email", "negative_test_email"),
            ("valid_test_password", "negative_test_password"),
        ):
            if positive not in value or negative not in value:
                continue
            positive_value = value[positive].strip()
            negative_value = value[negative].strip()
            if positive.endswith("email"):
                same_value = positive_value.casefold() == negative_value.casefold()
            else:
                same_value = positive_value == negative_value
            if same_value:
                raise ValueError(f"{positive} and {negative} must differ.")
        return value

    @field_validator("selected_scenario_ids")
    @classmethod
    def validate_scenario_selection(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", item) for item in value):
            raise ValueError("Scenario IDs are invalid.")
        if len(value) != len(set(value)):
            raise ValueError("Scenario IDs must be unique.")
        return value


class SavedTestSuiteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160, strict=True)
    url: HttpUrl
    goal: str = Field(min_length=1, max_length=2000, strict=True)
    plan: dict
    source_run_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("url")
    @classmethod
    def reject_url_secrets(cls, value: HttpUrl) -> HttpUrl:
        from .url_policy import reject_sensitive_query
        reject_sensitive_query(str(value))
        return value

    @field_validator("name")
    @classmethod
    def sanitize_suite_name(cls, value: str) -> str:
        from .sensitive_data import sanitize_goal
        return sanitize_goal(value.strip())

    @field_validator("goal")
    @classmethod
    def sanitize_suite_goal(cls, value: str) -> str:
        from .sensitive_data import sanitize_goal
        return sanitize_goal(value.strip())


class SavedTestSuiteResponse(StrictModel):
    id: str
    name: str
    url: HttpUrl
    goal: str
    plan: TestPlan
    source_run_id: str | None
    created_at: int


class StepExecutionResult(StrictModel):
    action: ActionType
    target: str
    status: StepExecutionStatus
    message: str


class AssertionExecutionResult(StrictModel):
    kind: AssertionKind = AssertionKind.TEXT
    text: str = ""
    target: str | None = None
    expectation: TextExpectation = TextExpectation.VISIBLE
    timeout_ms: int
    status: StepExecutionStatus
    observed_text: str = Field(max_length=500)
    message: str


class TestExecutionResult(StrictModel):
    scenario_id: str = Field(default="", max_length=64)
    name: str
    status: StepExecutionStatus
    scenario_status: ScenarioStatus = ScenarioStatus.INCONCLUSIVE
    actual_result: str = Field(default="", max_length=1000)
    uncertainty: str | None = Field(default=None, max_length=500)
    reproduction_steps: list[str] = Field(default_factory=list, max_length=12)
    steps: list[StepExecutionResult]
    assertions: list[AssertionExecutionResult] = Field(default_factory=list)
    observations: list[PageObservation] = Field(default_factory=list, max_length=20)


class ScreenshotEvidence(StrictModel):
    type: str = Field(pattern=r"^screenshot$")
    label: str = Field(min_length=1, max_length=160)
    filename: str = Field(pattern=r"^test-\d{2}-(failure|final)\.png$")
    test_index: int = Field(ge=0)
    step_index: int | None = Field(ge=0)


class ExecutionResult(StrictModel):
    status: StepExecutionStatus
    agent_status: AgentStatus = AgentStatus.COMPLETED
    duration_ms: int = Field(default=0, ge=0)
    browser_action_count: int = Field(default=0, ge=0, le=MAX_BROWSER_ACTIONS)
    tests: list[TestExecutionResult]
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    evidence: list["ScreenshotEvidence"] = Field(max_length=12)
    investigation: list[InvestigationRecord] = Field(default_factory=list, max_length=4)
    effective_plan: TestPlan | None = None


class AnalyzeResultsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=2000, strict=True)
    plan: TestPlan
    execution: ExecutionResult

    @field_validator("plan", mode="before")
    @classmethod
    def validate_plan(cls, value: object) -> TestPlan:
        from .sensitive_data import sanitize_plan_credentials
        return sanitize_plan_credentials(TestPlan.model_validate(value, strict=False))

    @field_validator("execution", mode="before")
    @classmethod
    def validate_execution(cls, value: object) -> ExecutionResult:
        execution = ExecutionResult.model_validate(value, strict=False)
        if execution.effective_plan is not None:
            from .sensitive_data import sanitize_plan_credentials
            execution = execution.model_copy(update={
                "effective_plan": sanitize_plan_credentials(execution.effective_plan)
            })
        return execution

    @field_validator("goal")
    @classmethod
    def goal_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        from .sensitive_data import sanitize_goal
        return sanitize_goal(value.strip())


class ResultAnalysis(StrictModel):
    status: AnalysisStatus
    summary: str = Field(min_length=1, max_length=500)
    root_cause: str | None
    evidence: list[str] = Field(max_length=10)
    reproduction_steps: list[str] = Field(min_length=1, max_length=12)
    recommendation: str = Field(min_length=1, max_length=500)
    analysis_source: ReportNarrativeSource = ReportNarrativeSource.PROVIDER


class GenerateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl
    goal: str = Field(min_length=1, max_length=2000, strict=True)
    plan: TestPlan
    execution: ExecutionResult
    analysis: ResultAnalysis

    @field_validator("url")
    @classmethod
    def reject_url_secrets(cls, value: HttpUrl) -> HttpUrl:
        from .url_policy import reject_sensitive_query
        reject_sensitive_query(str(value))
        return value

    @field_validator("plan", mode="before")
    @classmethod
    def validate_plan(cls, value: object) -> TestPlan:
        from .sensitive_data import sanitize_plan_credentials
        return sanitize_plan_credentials(TestPlan.model_validate(value, strict=False))

    @field_validator("execution", mode="before")
    @classmethod
    def validate_execution(cls, value: object) -> ExecutionResult:
        execution = ExecutionResult.model_validate(value, strict=False)
        if execution.effective_plan is not None:
            from .sensitive_data import sanitize_plan_credentials
            execution = execution.model_copy(update={
                "effective_plan": sanitize_plan_credentials(execution.effective_plan)
            })
        return execution

    @field_validator("analysis", mode="before")
    @classmethod
    def validate_analysis(cls, value: object) -> ResultAnalysis:
        return ResultAnalysis.model_validate(value, strict=False)

    @field_validator("goal")
    @classmethod
    def goal_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("goal must not be blank")
        from .sensitive_data import sanitize_goal
        return sanitize_goal(value.strip())


class ProviderFinalReport(StrictModel):
    status: AnalysisStatus
    title: str = Field(min_length=1, max_length=160)
    flow: FlowType
    expected_results: list[str] = Field(min_length=1, max_length=6)
    actual_result: str = Field(min_length=1, max_length=500)
    root_cause: str | None
    evidence: list[str] = Field(max_length=10)
    severity: ReportSeverity
    reproduction_steps: list[str] = Field(min_length=1, max_length=12)
    recommendation: str = Field(min_length=1, max_length=500)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    screenshot_evidence: list[ScreenshotEvidence] = Field(max_length=12)


class FinalReport(StrictModel):
    status: AnalysisStatus
    title: str = Field(min_length=1, max_length=160)
    flow: FlowType
    expected_results: list[str] = Field(min_length=1, max_length=6)
    actual_result: str = Field(min_length=1, max_length=500)
    root_cause: str | None
    evidence: list[str] = Field(max_length=10)
    severity: ReportSeverity
    reproduction_steps: list[str] = Field(min_length=1, max_length=12)
    recommendation: str = Field(min_length=1, max_length=500)
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    screenshot_evidence: list[ScreenshotEvidence] = Field(max_length=12)
    agent_status: AgentStatus = AgentStatus.COMPLETED
    scope: PlanScope = PlanScope.FOCUSED
    scenario_results: list["ScenarioReport"] = Field(default_factory=list, max_length=6)
    narrative_source: ReportNarrativeSource = ReportNarrativeSource.PROVIDER


class ScenarioReport(StrictModel):
    scenario_id: str
    name: str
    goal: str
    expected_result: str
    status: ScenarioStatus
    actual_result: str
    evidence: list[str] = Field(max_length=20)
    reproduction_steps: list[str] = Field(max_length=12)
    uncertainty: str | None


FinalReport.model_rebuild()


class RunListItem(StrictModel):
    id: str
    target_url: HttpUrl
    goal: str
    flow: FlowType
    status: AnalysisStatus
    agent_status: AgentStatus = AgentStatus.COMPLETED
    target_domain: str = ""
    scope: PlanScope = PlanScope.FOCUSED
    pinned: bool = False
    scenario_counts: dict[str, int] = Field(default_factory=dict)
    confirmed_issues: list[str] = Field(default_factory=list, max_length=2)
    created_at: int
    completed_at: int


class PinRunRequest(StrictModel):
    pinned: bool


class RunDetail(RunListItem):
    plan: TestPlan
    execution: ExecutionResult
    analysis: ResultAnalysis | None
    report: FinalReport | None
    evidence_run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
