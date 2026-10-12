from types import SimpleNamespace

from backend.observations import ObservationSignals, capture_page_observation


class FakeLocator:
    def __init__(self, text: str = "", rows: list[dict] | None = None) -> None:
        self.text = text
        self.rows = rows or []

    def inner_text(self, **kwargs) -> str:
        return self.text

    def evaluate_all(self, script: str):
        return self.rows


class FakePage:
    url = "https://example.com/reset/opaque?token=query-secret"

    def title(self) -> str:
        return "Account sign in"

    def locator(self, selector: str) -> FakeLocator:
        if selector == "body":
            return FakeLocator(
                "Sign in alice@example.test. password=very-secret token=body-secret"
            )
        if selector == "input, textarea, select":
            return FakeLocator(
                rows=[
                    {
                        "label": "Email",
                        "kind": "email",
                        "visible": True,
                        "enabled": True,
                        "required": True,
                    "aria_invalid": True,
                        "valid": False,
                        "validation_message": "Enter a valid email address.",
                    }
                ]
            )
        if selector == "button, input[type=submit], [role=button]":
            return FakeLocator(
                rows=[{"label": "Log in", "visible": True, "enabled": True}]
            )
        raise AssertionError(selector)


def test_observation_collects_relevant_dom_and_redacts_sensitive_values() -> None:
    signals = ObservationSignals(
        alerts=["Session token=dialog-secret"],
        navigation_changes=["https://example.com/redirect?session=nav-secret"],
        console_errors=["request failed password=console-secret"],
        failed_requests=["https://example.com/api?token=request-secret"],
        http_errors=["HTTP 500 https://example.com/api?key=response-secret"],
    )

    observation = capture_page_observation(
        FakePage(),
        step_index=3,
        action="submit",
        signals=signals,
        secrets=["alice@example.test", "very-secret"],
    )

    payload = observation.model_dump_json()
    assert observation.url == "https://example.com/reset/[REDACTED]"
    assert observation.title == "Account sign in"
    assert "Sign in [REDACTED]" in observation.visible_text
    assert "password=[REDACTED]" in observation.visible_text
    assert any("validation_message=\"Enter a valid email address.\"" in item for item in observation.evidence)
    assert observation.controls[0].label == "Email"
    assert observation.controls[0].aria_invalid is True
    assert observation.buttons[0].label == "Log in"
    assert "Login successful" not in payload
    for secret in (
        "query-secret",
        "dialog-secret",
        "nav-secret",
        "console-secret",
        "request-secret",
        "response-secret",
        "alice@example.test",
        "very-secret",
    ):
        assert secret not in payload
    assert any("Visible page text excerpt" in item for item in observation.evidence)


def test_observation_caps_page_signals_and_body_text() -> None:
    page = FakePage()
    page.locator = lambda selector: (
        FakeLocator("A" * 5000)
        if selector == "body"
        else FakeLocator(rows=[])
    )
    signals = ObservationSignals(
        console_errors=[f"console error {index}" for index in range(20)]
    )

    observation = capture_page_observation(
        page,
        step_index=0,
        action="navigate",
        signals=signals,
    )

    assert len(observation.visible_text) <= 600
    assert len(observation.console_errors) <= 5


def test_observation_does_not_emit_empty_evidence_when_page_is_unavailable() -> None:
    class UnavailablePage:
        url = ""

        def title(self) -> str:
            raise RuntimeError("unavailable")

        def locator(self, _selector: str) -> FakeLocator:
            raise RuntimeError("unavailable")

    observation = capture_page_observation(
        UnavailablePage(),
        step_index=0,
        action="navigate",
    )

    assert observation.evidence
    assert any("unavailable" in item.lower() for item in observation.collection_errors)
    assert any("observation collection warning" in item.lower() for item in observation.evidence)
    assert observation.visible_text == ""
