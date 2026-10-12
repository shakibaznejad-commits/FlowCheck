import re


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_ -]?key|access[_ -]?token|authorization|token)(\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b(?:sk|rk|pk|sess)-[A-Za-z0-9_-]{12,}\b"),
)


def safe_log_message(message: str, secrets: tuple[str, ...] | list[str] = ()) -> str:
    """Redact common credential forms from exception messages before logging."""
    safe_message = message
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            safe_message = pattern.sub(r"\1\2[REDACTED]", safe_message)
        else:
            safe_message = pattern.sub("[REDACTED]", safe_message)
    for secret in sorted(set(secrets), key=len, reverse=True):
        if len(secret) >= 3:
            safe_message = safe_message.replace(secret, "[REDACTED]")
    return safe_message


def request_log_secrets(request: object) -> tuple[str, ...]:
    """Return submitted form values and request text that must not reach logs."""
    values: set[str] = set()
    for field in ("goal", "url"):
        value = getattr(request, field, None)
        if value:
            values.add(str(value))
    plan = getattr(request, "plan", None)
    for test in getattr(plan, "tests", ()):
        for step in getattr(test, "steps", ()):
            action = getattr(getattr(step, "action", None), "value", None)
            value = getattr(step, "value", None)
            if action == "fill" and isinstance(value, str) and value:
                values.add(value)
    return tuple(values)
