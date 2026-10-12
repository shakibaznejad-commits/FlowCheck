import socket

import pytest

from backend.url_policy import (
    TargetScopeError,
    redact_url,
    trusted_origins_from_environment,
    validate_target_url,
)


def public_resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


def private_resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443))]


def test_allows_public_target_with_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.url_policy.socket.getaddrinfo", public_resolver)

    origin = validate_target_url("https://example.com/login")

    assert origin == "https://example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/health",
        "http://[::1]:8080/",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_rejects_loopback_and_reserved_targets(url: str) -> None:
    with pytest.raises(TargetScopeError):
        validate_target_url(url)


def test_rejects_public_hostname_that_resolves_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.url_policy.socket.getaddrinfo", private_resolver)

    with pytest.raises(TargetScopeError, match="non-public address"):
        validate_target_url("https://example.com/login")


def test_allows_only_explicitly_trusted_private_origin() -> None:
    origin = validate_target_url(
        "http://host.docker.internal:8081/",
        trusted_origins={"http://host.docker.internal:8081"},
    )

    assert origin == "http://host.docker.internal:8081"


def test_local_demo_allowlist_is_exact_and_keeps_private_targets_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "FLOWCHECK_TRUSTED_TEST_ORIGINS",
        "http://flowcheck-demo-target.test,http://flowcheck-healthy.test,"
        "http://flowcheck-buggy.test",
    )

    trusted = trusted_origins_from_environment()

    assert trusted == {
        "http://flowcheck-demo-target.test",
        "http://flowcheck-healthy.test",
        "http://flowcheck-buggy.test",
    }
    assert validate_target_url(
        "http://flowcheck-healthy.test/",
        trusted_origins=trusted,
    ) == "http://flowcheck-healthy.test"
    with pytest.raises(TargetScopeError, match="outside the authorized origin"):
        validate_target_url(
            "http://flowcheck-healthy.test:8181/",
            authorized_origin="http://flowcheck-healthy.test",
            trusted_origins=trusted,
            resolver=public_resolver,
        )
    with pytest.raises(TargetScopeError, match="non-public address"):
        validate_target_url(
            "https://example.com/",
            trusted_origins=trusted,
            resolver=private_resolver,
        )


def test_rejects_redirect_outside_authorized_origin() -> None:
    with pytest.raises(TargetScopeError, match="outside the authorized origin"):
        validate_target_url(
            "https://evil.example/path",
            authorized_origin="https://example.com",
            trusted_origins=set(),
            resolver=public_resolver,
        )


def test_rejects_secrets_in_target_query_parameters() -> None:
    with pytest.raises(TargetScopeError, match="query parameters"):
        validate_target_url(
            "https://example.com/login?access_token=private",
            resolver=public_resolver,
        )


def test_redacts_url_credentials_and_query_values() -> None:
    safe = redact_url("https://alice:secret@example.com/reset/opaque?token=abc&next=/home#fragment")

    assert safe == "https://example.com/reset/[REDACTED]"
    assert "alice" not in safe
    assert "secret" not in safe
    assert "abc" not in safe
