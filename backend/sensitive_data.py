import re

from .schemas import PrerequisiteType, TestPlan, ActionType


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_ASSIGNED_SECRET = re.compile(
    r"(?i)\b(password|passwd|token|access[_ -]?token|api[_ -]?key)"
    r"(\s*(?:[:=]|\bis\b)\s*(?!(?:a|an|the|wrong|incorrect|invalid|empty|blank|missing|required|not|rejected|accepted|verified|used|provided|submitted|supplied|entered|set|optional|visible|hidden)\b))"
    r"(?:bearer\s+)?([^\s,;]+)"
)
_PERSIAN_ASSIGNED_SECRET = re.compile(
    r"(رمز[\s\u200c]+عبور|گذرواژه|توکن)"
    r"(\s*(?:[:=]|\bاست\b)\s*)"
    r"(?!(?:خالی|تهی|اشتباه|نامعتبر|معتبر|لازم|ضروری|وارد|نمایش|پنهان)(?:\b|\s))"
    r"([^\s،,;]+)"
)


def _redact_assigned_secrets(text: str) -> str:
    assigned_values: set[str] = set()

    def replace(match: re.Match[str]) -> str:
        assigned_values.add(match.group(3))
        return f"{match.group(1)}{match.group(2)}[redacted]"

    redacted = _ASSIGNED_SECRET.sub(replace, text)
    redacted = _PERSIAN_ASSIGNED_SECRET.sub(replace, redacted)
    for value in sorted(assigned_values, key=len, reverse=True):
        redacted = re.sub(re.escape(value), "[redacted]", redacted, flags=re.IGNORECASE)
    return redacted


def sanitize_goal(goal: str) -> str:
    """Keep credentials in setup fields, not in prompts, history, or reports."""
    sanitized = _redact_assigned_secrets(goal)
    return _EMAIL.sub("[test email]", sanitized).strip()


def _align_credential_category(text: str, email_key: str | None, password_key: str | None) -> str:
    """Keep human-readable scenario intent consistent with its structured values."""
    if email_key == "negative_test_email":
        text = re.sub(
            r"(?i)\b(?:(?:authorized|valid|known)(?:\s+account)?\s+)+(?:test\s+)?email\b",
            "negative test email",
            text,
        )
        text = re.sub(
            r"(?i)\b(?:authorized|valid|known)(?:\s+test)?\s+account\s+(?:test\s+)?email\b",
            "negative test email",
            text,
        )
    elif email_key == "valid_test_email":
        text = re.sub(r"(?i)\bnegative\s+(?:test\s+)?email\b", "valid test account email", text)
        text = re.sub(r"(?i)\bvalid\s+test\s+email\b", "valid test account email", text)
    if password_key == "negative_test_password":
        text = re.sub(
            r"(?i)\b(?:do not|never) reuse (?:the )?negative(?: test)? password\b",
            "use a generated negative test password",
            text,
        )
        text = re.sub(
            r"(?i)\b(?:(?:authorized|valid|known)(?:\s+account)?\s+)+(?:test\s+)?password\b",
            "negative test password",
            text,
        )
        text = re.sub(
            r"(?i)\b(?:authorized|valid|known)(?:\s+test)?\s+account\s+(?:test\s+)?password\b",
            "negative test password",
            text,
        )
    elif password_key == "valid_test_password":
        text = re.sub(r"(?i)\bnegative\s+(?:test\s+)?password\b", "valid test account password", text)
    return text


def sanitize_plan_credentials(plan: TestPlan) -> TestPlan:
    """Replace email/password literals with transient setup placeholders."""
    sanitized_tests = []
    for test in plan.tests:
        scenario_text = " ".join((test.name, test.goal, test.description, test.expected_result)).casefold().replace("-", " ")
        is_login = plan.flow.value == "login"
        empty_email = bool(re.search(r"\bempty\s+email\b|\bemail\s+(?:field\s+)?(?:is\s+)?empty\b|ایمیل\s+خالی", scenario_text))
        empty_password = bool(re.search(r"\bempty\s+password\b|\bpassword\s+(?:field\s+)?(?:is\s+)?empty\b|رمز\s+عبور\s+خالی", scenario_text))
        unknown_email = bool(re.search(r"\b(unknown|unregistered|not registered|email not found|invalid email|invalid credentials|incorrect credentials|invalid login|negative test email)\b|ایمیل\s+(?:ناشناخته|ثبت.?نشده)", scenario_text))
        wrong_password = bool(re.search(r"\b(wrong|incorrect|invalid)\s+password\b|\bnegative test password\b|رمز\s+عبور\s+اشتباه", scenario_text))
        empty_email = empty_email or "empty email" in scenario_text
        empty_password = empty_password or "empty password" in scenario_text
        negative_email = bool(re.search(r"\bnegative\s+(?:test\s+)?(?:email|address)\b|\b(?:email|address)\s+(?:is\s+)?negative\b", scenario_text))
        negative_password = bool(re.search(r"\bnegative\s+(?:test\s+)?password\b|\bpassword\b.{0,24}\bnegative\b", scenario_text))
        if is_login:
            email_key = (
                None if empty_email
                else "valid_test_email" if empty_password
                else "negative_test_email" if unknown_email or negative_email
                else "valid_test_email"
            )
            password_key = (
                None if empty_password
                else "negative_test_password" if empty_email or unknown_email or wrong_password or negative_password
                else "valid_test_password"
            )
            prerequisites = set()
        else:
            email_key = "test_email"
            password_key = "test_password"
            prerequisites = set(test.prerequisites)
        steps = []
        for step in test.steps:
            target = step.target.casefold()
            key = None
            if step.action == ActionType.FILL and step.value is not None:
                if any(word in target for word in ("password", "passcode", "رمز")):
                    key = password_key
                elif any(word in target for word in ("email", "e-mail", "username", "user name", "ایمیل")):
                    key = email_key
            if is_login and key is None and step.action == ActionType.FILL and step.target.casefold() in ("email", "e-mail", "password"):
                # Empty-field cases must not receive a hidden non-empty fill.
                continue
            if key and step.value != "{{" + key + "}}":
                step = step.model_copy(update={"value": "{{" + key + "}}"})
            elif step.value:
                step = step.model_copy(update={"value": sanitize_goal(step.value)})
            if key:
                prerequisites.add(PrerequisiteType(key))
            steps.append(step)
        assertions = []
        for assertion in test.assertions:
            safe_text = _redact_assigned_secrets(assertion.text)
            assertion_email_key = email_key or "negative_test_email"
            safe_text = _EMAIL.sub("{{" + assertion_email_key + "}}", safe_text)
            assertions.append(assertion.model_copy(update={"text": safe_text}))
        safe_goal = sanitize_goal(test.goal)
        safe_description = sanitize_goal(test.description)
        safe_expected_result = sanitize_goal(test.expected_result)
        if is_login:
            safe_goal = _align_credential_category(safe_goal, email_key, password_key)
            safe_description = _align_credential_category(safe_description, email_key, password_key)
            safe_expected_result = _align_credential_category(safe_expected_result, email_key, password_key)
            if empty_email and password_key == "valid_test_password":
                align = lambda text: re.sub(
                    r"(?i)(password.{0,40})negative\s+(?:test\s+)?value",
                    r"\1valid test account password",
                    text,
                )
                safe_goal, safe_description, safe_expected_result = map(
                    align, (safe_goal, safe_description, safe_expected_result)
                )
            if empty_email and password_key == "negative_test_password":
                align = lambda text: re.sub(
                    r"(?i)(password.{0,40})negative\s+(?:test\s+)?value",
                    r"\1negative test password",
                    text,
                )
                safe_goal, safe_description, safe_expected_result = map(
                    align, (safe_goal, safe_description, safe_expected_result)
                )
            if empty_password and email_key == "valid_test_email":
                align = lambda text: re.sub(
                    r"(?i)(email.{0,40})negative\s+(?:test\s+)?value",
                    r"\1valid test account email",
                    text,
                )
                safe_goal, safe_description, safe_expected_result = map(
                    align, (safe_goal, safe_description, safe_expected_result)
                )
            if empty_password and email_key == "negative_test_email":
                align = lambda text: re.sub(
                    r"(?i)(email.{0,40})negative\s+(?:test\s+)?value",
                    r"\1negative test email",
                    text,
                )
                safe_goal, safe_description, safe_expected_result = map(
                    align, (safe_goal, safe_description, safe_expected_result)
                )
        sanitized_tests.append(test.model_copy(update={
            "name": sanitize_goal(test.name),
            "goal": safe_goal,
            "description": safe_description,
            "expected_result": safe_expected_result,
            "steps": steps,
            "assertions": assertions,
            "prerequisites": sorted(prerequisites, key=lambda item: item.value),
        }))
    return plan.model_copy(update={
        "summary": sanitize_goal(plan.summary),
        "tests": sanitized_tests,
    })
