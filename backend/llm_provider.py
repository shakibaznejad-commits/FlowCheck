import logging
import time

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    OpenAIError,
)

from . import database
from .ai_authorization import ensure_ai_user_approved
from .ai_usage import UsageLedger


logger = logging.getLogger(__name__)


class ProviderRequestError(Exception):
    """Raised when the configured LLM provider cannot return a response."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class OpenAIProvider:
    """Small adapter for providers that expose OpenAI-compatible chat completions."""

    def __init__(self, api_key: str, model: str, base_url: str, user_id: int) -> None:
        if user_id <= 0:
            raise ValueError("Authenticated user context is required for Provider access.")
        ensure_ai_user_approved(user_id)
        self.model = model
        self.user_id = user_id
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=60.0,
        )

    def generate_json(
        self,
        system_prompt: str,
        user_message: str,
        schema: dict,
        schema_name: str = "flowcheck_test_plan",
    ) -> str:
        for attempt in range(2):
            try:
                with UsageLedger(database.engine).reserve_provider_attempt(self.user_id) as lease:
                    lease.mark_dispatched()
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        response_format={
                            "type": "json_schema",
                            "json_schema": {
                                "name": schema_name,
                                "strict": True,
                                "schema": schema,
                            },
                        },
                    )
                    lease.mark_completed_response()
            except APITimeoutError as exc:
                if attempt == 0:
                    logger.warning("LLM provider timeout; retrying once")
                    time.sleep(0.5)
                    continue
                raise ProviderRequestError(
                    "LLM provider request timed out after one retry.", retryable=True
                ) from exc
            except APIConnectionError as exc:
                if attempt == 0:
                    logger.warning("LLM provider connection failed; retrying once")
                    time.sleep(0.5)
                    continue
                raise ProviderRequestError(
                    "LLM provider connection failed after one retry.", retryable=True
                ) from exc
            except APIStatusError as exc:
                retryable = exc.status_code in {408, 409, 429} or exc.status_code >= 500
                if retryable and attempt == 0:
                    logger.warning(
                        "LLM provider returned a transient HTTP status; retrying once"
                    )
                    time.sleep(0.5)
                    continue
                raise ProviderRequestError(
                    f"LLM provider returned HTTP {exc.status_code}.",
                    retryable=retryable,
                ) from exc
            except OpenAIError as exc:
                raise ProviderRequestError(
                    "LLM provider rejected the request."
                ) from exc

            content = response.choices[0].message.content if response.choices else None
            if content:
                return content
            if attempt == 0:
                logger.warning("LLM provider returned an empty response; retrying once")
                time.sleep(0.5)

        raise ProviderRequestError(
            "LLM provider returned an empty response after one retry.", retryable=True
        )
