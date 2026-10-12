from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

import backend.llm_provider as llm_provider
from backend import database
from backend.ai_usage import UsageLedger, UsageLimitExceeded


def test_provider_retries_timeout_once_without_logging_request_data(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls = 0

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            assert kwargs["max_retries"] == 0
            assert kwargs["timeout"] == 60.0
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs: object) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise APITimeoutError(
                    request=httpx.Request("POST", "https://provider.example/v1")
                )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))]
            )

    monkeypatch.setattr(llm_provider, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(llm_provider.time, "sleep", lambda _seconds: None)

    provider = llm_provider.OpenAIProvider(
        api_key="test-secret", model="test-model", base_url="https://provider.example/v1", user_id=1
    )
    response = provider.generate_json("system prompt", "safe test request", {}, "test")

    assert response == '{"ok":true}'
    assert calls == 2
    usage = UsageLedger(database.engine).snapshot(1)
    assert usage.provider_attempts == 2
    assert usage.provider_responses == 1
    assert "test-secret" not in caplog.text
    assert "safe test request" not in caplog.text


def test_provider_reports_timeout_after_bounded_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class AlwaysTimeoutOpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs: object) -> None:
            nonlocal calls
            calls += 1
            raise APITimeoutError(
                request=httpx.Request("POST", "https://provider.example/v1")
            )

    monkeypatch.setattr(llm_provider, "OpenAI", AlwaysTimeoutOpenAI)
    monkeypatch.setattr(llm_provider.time, "sleep", lambda _seconds: None)
    provider = llm_provider.OpenAIProvider(
        api_key="test-secret", model="test-model", base_url="https://provider.example/v1", user_id=1
    )

    with pytest.raises(llm_provider.ProviderRequestError, match="timed out") as error:
        provider.generate_json("system prompt", "safe test request", {}, "test")

    assert calls == 2
    assert error.value.retryable is True


def test_provider_daily_quota_blocks_outbound_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    class FakeOpenAI:
        def __init__(self, **kwargs: object) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs: object) -> SimpleNamespace:
            nonlocal calls
            calls += 1
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))])

    monkeypatch.setattr(llm_provider, "OpenAI", FakeOpenAI)
    monkeypatch.setenv("FLOWCHECK_AI_DAILY_PROVIDER_CALLS", "1")
    provider = llm_provider.OpenAIProvider(
        api_key="test-secret", model="test-model", base_url="https://provider.example/v1", user_id=1
    )
    assert provider.generate_json("system", "user", {}) == '{"ok":true}'
    with pytest.raises(UsageLimitExceeded):
        provider.generate_json("system", "user", {})
    assert calls == 1
    assert UsageLedger(database.engine).snapshot(1).provider_attempts == 1


def test_provider_requires_server_authenticated_user_identity() -> None:
    with pytest.raises(ValueError, match="Authenticated user context"):
        llm_provider.OpenAIProvider(
            api_key="test-secret", model="test-model", base_url="https://provider.example/v1", user_id=0
        )


def test_provider_refuses_user_outside_private_allowlist() -> None:
    from backend.ai_authorization import AIUserNotApproved

    with pytest.raises(AIUserNotApproved):
        llm_provider.OpenAIProvider(
            api_key="test-secret", model="test-model", base_url="https://provider.example/v1", user_id=2
        )
