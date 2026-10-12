import ipaddress
import os
import re
import socket
from collections.abc import Callable
from urllib.parse import parse_qsl, urlsplit


class TargetScopeError(ValueError):
    """Raised when a target or browser request leaves the permitted scope."""


Resolver = Callable[..., list[tuple]]


def normalize_origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise TargetScopeError("Only HTTP and HTTPS target URLs are supported.")
    try:
        port = parts.port
    except ValueError as exc:
        raise TargetScopeError("The target URL has an invalid port.") from exc
    host = parts.hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parts.scheme.lower() == "https" else 80
    port_suffix = f":{port}" if port and port != default_port else ""
    return f"{parts.scheme.lower()}://{host}{port_suffix}"


def trusted_origins_from_environment() -> set[str]:
    configured = os.getenv("FLOWCHECK_TRUSTED_TEST_ORIGINS", "")
    origins: set[str] = set()
    for item in configured.split(","):
        candidate = item.strip()
        if candidate:
            origins.add(normalize_origin(candidate))
    return origins


def reject_sensitive_query(url: str) -> None:
    parts = urlsplit(url)
    sensitive_query_keys = re.compile(
        r"(?i)(password|passwd|token|access[_-]?token|auth|session|api[_-]?key|secret)"
    )
    if any(sensitive_query_keys.search(key) for key, _ in parse_qsl(parts.query, keep_blank_values=True)):
        raise TargetScopeError("Sensitive values in target URL query parameters are not allowed.")


def validate_target_url(
    url: str,
    *,
    authorized_origin: str | None = None,
    trusted_origins: set[str] | None = None,
    resolver: Resolver | None = None,
) -> str:
    """Validate one navigation/request and return its normalized origin.

    A trusted origin is an explicit operator-configured exception for isolated
    local fixtures. Other origins must resolve exclusively to public IPs.
    """
    parts = urlsplit(url)
    origin = normalize_origin(url)
    if parts.username is not None or parts.password is not None:
        raise TargetScopeError("Credentials in target URLs are not allowed.")
    reject_sensitive_query(url)

    trusted = (
        trusted_origins
        if trusted_origins is not None
        else trusted_origins_from_environment()
    )
    if authorized_origin is not None:
        authorized = normalize_origin(authorized_origin)
        if origin != authorized and origin not in trusted:
            raise TargetScopeError("Browser request is outside the authorized origin.")

    if origin in trusted:
        return origin

    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise TargetScopeError("Local and private-network targets are not allowed.")

    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        if not literal_ip.is_global:
            raise TargetScopeError("Local and private-network targets are not allowed.")
        return origin

    try:
        resolved = (resolver or socket.getaddrinfo)(
            host,
            parts.port or (443 if parts.scheme.lower() == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except (OSError, ValueError) as exc:
        raise TargetScopeError("The target hostname could not be resolved safely.") from exc

    addresses = {item[4][0].split("%", 1)[0] for item in resolved if item[4]}
    if not addresses:
        raise TargetScopeError("The target hostname did not resolve to an address.")
    for address in addresses:
        try:
            is_public = ipaddress.ip_address(address).is_global
        except ValueError as exc:
            raise TargetScopeError("The target resolved to an invalid IP address.") from exc
        if not is_public:
            raise TargetScopeError("The target resolved to a non-public address.")
    return origin


def redact_url(url: str) -> str:
    """Keep useful origin/path context while removing URL credentials and tokens."""
    try:
        parts = urlsplit(url)
        origin = normalize_origin(url)
    except (ValueError, TargetScopeError):
        return "[REDACTED URL]"
    path = re.sub(
        r"(?i)/(reset|verify|invite|token|session|auth)/[^/?#]+",
        r"/\1/[REDACTED]",
        parts.path,
    )
    return f"{origin}{path}"


_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|access[_ -]?token|api[_ -]?key|authorization|cookie|session)"
    r"(\s*[:=]\s*)(?:bearer\s+)?[^\s,;<>\"]+"
)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def redact_sensitive_text(text: str, secrets: tuple[str, ...] | list[str] = ()) -> str:
    safe_text = text
    safe_text = _URL_IN_TEXT.sub(lambda match: redact_url(match.group(0)), safe_text)
    for secret in sorted((item for item in secrets if len(item) >= 3), key=len, reverse=True):
        safe_text = safe_text.replace(secret, "[REDACTED]")
    return _SENSITIVE_ASSIGNMENT.sub(r"\1\2[REDACTED]", safe_text)
