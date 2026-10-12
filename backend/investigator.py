import json
import os

from pydantic import ValidationError

from .llm_provider import OpenAIProvider
from .schemas import (
    AgentDecision,
    InvestigationChoice,
    InvestigatorOutput,
    PlannedTest,
    TestExecutionResult,
)
from .ai_usage import UsageLimitExceeded
from .ai_authorization import AIUserNotApproved


INVESTIGATOR_SYSTEM_PROMPT = """
You are FlowCheck's bounded QA investigator. Decide the single most useful
next step from real browser observations. Never invent evidence. Return a
strict JSON object with decision, rationale, evidence, actions, and new_test.
Always include every key. Use this exact baseline shape:
{"decision":"continue","rationale":"...","evidence":["exact observed evidence"],"actions":[],"new_test":null}
Each evidence item must exactly copy an observed execution message or
observation evidence string. Use only these decisions: continue, inspect,
retry, alternative_path, new_test, finish. `alternative_path` may contain at
most three structured actions using navigate/fill/click/check/submit and plain
UI labels, with `new_test: null`; it must end with click or submit when the test
has assertions. `new_test` uses `actions: []` and may add one focused
structured scenario with name, goal, prerequisites, description, steps,
expected_result, and assertions. Prerequisites may only be valid_test_email,
valid_test_password, negative_test_email, and negative_test_password. Use the
matching structured placeholders for form values; never include credentials.
Treat negative values as locally generated/supplied test data and never ask for
or infer the user's valid password. For signup, legacy test_email and
test_password categories remain valid. All other decisions use
`actions: []` and `new_test: null`.
Do not produce selectors, code, shell commands, or arbitrary browser scripts.
Use dummy values only. Stop when evidence is sufficient. Rationale must state
why the decision helps the user's goal and cite supplied observations. Keep
rationale at or below 600 characters.
Use one to five exact evidence items, preferring only the most relevant ones.
""".strip()


class InvestigatorDecisionError(Exception):
    """Raised when the investigator returns an invalid or unsupported choice."""


class InvestigatorConfigurationError(InvestigatorDecisionError):
    """Raised when the investigator provider is not configured."""


def decide_next_action(
    *,
    goal: str,
    test: TestExecutionResult,
    remaining_tests: int,
    remaining_actions: int,
    user_id: int | None = None,
) -> AgentDecision:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise InvestigatorConfigurationError("LLM_API_KEY is not set.")

    evidence = [
        message
        for step in test.steps
        for message in [step.message]
    ]
    evidence.extend(
        assertion.message for assertion in test.assertions
    )
    evidence.extend(
        item
        for observation in test.observations
        for item in observation.evidence
    )
    evidence = list(dict.fromkeys(evidence))[-30:]

    safe_test = {
        "name": test.name,
        "status": test.status.value,
        "steps": [
            {
                "action": step.action.value,
                "target": step.target,
                "status": step.status.value,
                "message": step.message,
            }
            for step in test.steps
        ],
        "assertions": [assertion.model_dump(mode="json") for assertion in test.assertions],
        "observations": [item.model_dump(mode="json") for item in test.observations[-6:]],
        "remaining_test_slots": remaining_tests,
        "remaining_action_budget": remaining_actions,
    }

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
        content = provider.generate_json(
            system_prompt=INVESTIGATOR_SYSTEM_PROMPT,
            user_message=json.dumps(
                {"goal": goal, "execution": safe_test, "available_evidence": evidence},
                ensure_ascii=False,
            ),
            schema=InvestigatorOutput.model_json_schema(),
            schema_name="flowcheck_investigation_decision",
        )
    except UsageLimitExceeded:
        raise
    except AIUserNotApproved:
        raise
    except Exception as exc:
        raise InvestigatorDecisionError("Investigator provider request failed.") from exc

    try:
        generated = InvestigatorOutput.model_validate_json(content, strict=True)
        payload = generated.model_dump(mode="json")
        if generated.new_test is not None:
            payload["new_test"] = PlannedTest.model_validate(
                generated.new_test.model_dump(mode="json"), strict=False
            ).model_dump(mode="json")
        decision = AgentDecision.model_validate(payload, strict=False)
    except ValidationError as exc:
        issues = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_input=False)
        )
        raise InvestigatorDecisionError(
            f"Investigator response does not match the decision schema ({issues})."
        ) from exc

    if any(item not in evidence for item in decision.evidence):
        raise InvestigatorDecisionError(
            "Investigator cited evidence that was not observed by Playwright."
        )
    if decision.decision == InvestigationChoice.NEW_TEST:
        if remaining_tests < 1:
            raise InvestigatorDecisionError("Investigator exceeded the scenario limit.")
        if decision.new_test is not None and len(decision.new_test.steps) > remaining_actions:
            raise InvestigatorDecisionError("Investigator exceeded the action limit.")
    if len(decision.actions) > remaining_actions:
        raise InvestigatorDecisionError("Investigator exceeded the action limit.")
    return decision
