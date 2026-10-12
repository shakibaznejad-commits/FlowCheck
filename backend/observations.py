from dataclasses import dataclass, field
from typing import Any

from playwright.sync_api import Page

from .schemas import ButtonObservation, ControlObservation, PageObservation
from .url_policy import redact_sensitive_text, redact_url


@dataclass
class ObservationSignals:
    alerts: list[str] = field(default_factory=list)
    navigation_changes: list[str] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[str] = field(default_factory=list)
    http_errors: list[str] = field(default_factory=list)

    def add(self, field_name: str, value: str, secrets: tuple[str, ...] = ()) -> None:
        items = getattr(self, field_name)
        safe_value = redact_sensitive_text(value, secrets)
        if safe_value and safe_value not in items:
            items.append(safe_value[:400])
            del items[:-5]


def attach_observation_listeners(page: Page) -> ObservationSignals:
    signals = ObservationSignals()

    def on_dialog(dialog: Any) -> None:
        signals.add("alerts", dialog.message)
        try:
            dialog.dismiss()
        except Exception:
            pass

    def on_console(message: Any) -> None:
        if message.type == "error":
            signals.add("console_errors", message.text)

    def on_request_failed(request: Any) -> None:
        failure = request.failure or "request failed"
        signals.add(
            "failed_requests",
            f"{redact_url(request.url)} ({failure})",
        )

    def on_response(response: Any) -> None:
        if response.status >= 400:
            signals.add(
                "http_errors",
                f"HTTP {response.status} {redact_url(response.url)}",
            )

    def on_navigation(frame: Any) -> None:
        try:
            if frame == page.main_frame:
                signals.add("navigation_changes", redact_url(frame.url))
        except Exception:
            return

    for event, callback in (
        ("dialog", on_dialog),
        ("console", on_console),
        ("requestfailed", on_request_failed),
        ("response", on_response),
        ("framenavigated", on_navigation),
    ):
        try:
            page.on(event, callback)
        except Exception:
            continue
    return signals


def _read_rows(
    page: Page,
    selector: str,
    script: str,
    collection_errors: list[str],
) -> list[dict[str, Any]]:
    try:
        rows = page.locator(selector).evaluate_all(script)
        return rows if isinstance(rows, list) else []
    except Exception as exc:
        collection_errors.append(f"{selector} unavailable ({type(exc).__name__}).")
        return []


_CONTROL_SCRIPT = """elements => elements.slice(0, 12).map(element => {
  const labelText = element.labels
    ? Array.from(element.labels).map(label => label.innerText.trim()).filter(Boolean).join(' ')
    : '';
  return {
    label: labelText || element.getAttribute('aria-label') || element.name || element.id || element.tagName.toLowerCase(),
    kind: element.type || element.tagName.toLowerCase(),
    visible: Boolean(element.getClientRects().length),
    enabled: !element.disabled,
    required: Boolean(element.required),
    aria_invalid: element.getAttribute('aria-invalid') === 'true',
    valid: typeof element.checkValidity === 'function' ? element.checkValidity() : null,
    validation_message: element.validationMessage || null
  };
})"""

_BUTTON_SCRIPT = """elements => elements.slice(0, 10).map(element => ({
  label: (element.innerText || element.getAttribute('aria-label') || element.getAttribute('title') || '').trim().slice(0, 120),
  visible: Boolean(element.getClientRects().length),
  enabled: !element.disabled
}))"""


def capture_page_observation(
    page: Page,
    *,
    step_index: int,
    action: str,
    signals: ObservationSignals | None = None,
    secrets: tuple[str, ...] | list[str] = (),
) -> PageObservation:
    signal_data = signals or ObservationSignals()
    collection_errors: list[str] = []
    try:
        raw_url = page.url
        current_url = redact_url(raw_url) if raw_url else ""
    except Exception:
        current_url = ""
    try:
        title = redact_sensitive_text(page.title(), secrets)[:200]
    except Exception as exc:
        collection_errors.append(f"Page title unavailable ({type(exc).__name__}).")
        title = ""
    try:
        body = page.locator("body").inner_text(timeout=2_000)
    except Exception as exc:
        collection_errors.append(f"Page body text unavailable ({type(exc).__name__}).")
        body = ""
    visible_text = redact_sensitive_text(" ".join(body.split()), secrets)[:600]

    controls = []
    for row in _read_rows(
        page, "input, textarea, select", _CONTROL_SCRIPT, collection_errors
    ):
        try:
            controls.append(
                ControlObservation(
                    label=redact_sensitive_text(str(row.get("label", "")), secrets)[:120],
                    kind=str(row.get("kind", "control"))[:32],
                    visible=bool(row.get("visible")),
                    enabled=bool(row.get("enabled")),
                    required=bool(row.get("required")),
                    aria_invalid=row.get("aria_invalid"),
                    valid=row.get("valid"),
                    validation_message=(
                        redact_sensitive_text(str(row["validation_message"]), secrets)[:200]
                        if row.get("validation_message")
                        else None
                    ),
                )
            )
        except Exception:
            continue

    buttons = []
    for row in _read_rows(
        page,
        "button, input[type=submit], [role=button]",
        _BUTTON_SCRIPT,
        collection_errors,
    ):
        try:
            buttons.append(
                ButtonObservation(
                    label=redact_sensitive_text(str(row.get("label", "")), secrets)[:120],
                    visible=bool(row.get("visible")),
                    enabled=bool(row.get("enabled")),
                )
            )
        except Exception:
            continue

    alerts = [redact_sensitive_text(item, secrets)[:400] for item in signal_data.alerts[-5:]]
    navigation = [redact_url(item) for item in signal_data.navigation_changes[-5:]]
    console = [redact_sensitive_text(item, secrets)[:400] for item in signal_data.console_errors[-5:]]
    failed = [redact_sensitive_text(item, secrets)[:400] for item in signal_data.failed_requests[-5:]]
    http_errors = [redact_sensitive_text(item, secrets)[:400] for item in signal_data.http_errors[-5:]]

    evidence = []
    if current_url:
        evidence.append(f"Observed URL: {current_url}")
    if title:
        evidence.append(f"Observed page title: {title}")
    if visible_text:
        evidence.append(f'Visible page text excerpt: "{visible_text}"')
    evidence.extend(
        f'Visible form control: label="{item.label}" kind={item.kind} '
        f"visible={item.visible} enabled={item.enabled} required={item.required} "
        f"aria_invalid={item.aria_invalid} valid={item.valid} "
        f'validation_message="{item.validation_message or ""}"'
        for item in controls[:5]
    )
    evidence.extend(
        f'Visible button: label="{item.label}" visible={item.visible} enabled={item.enabled}'
        for item in buttons[:5]
    )
    evidence.extend(f"Browser alert: {item}" for item in alerts)
    evidence.extend(f"Console error: {item}" for item in console)
    evidence.extend(f"Failed request: {item}" for item in failed)
    evidence.extend(f"HTTP error: {item}" for item in http_errors)
    collection_errors = list(dict.fromkeys(collection_errors))[:8]
    evidence.extend(
        f"Observation collection warning: {item}"
        for item in collection_errors
    )

    return PageObservation(
        step_index=step_index,
        action=action,
        url=current_url,
        title=title,
        visible_text=visible_text,
        controls=controls,
        buttons=buttons,
        alerts=alerts,
        navigation_changes=navigation,
        console_errors=console,
        failed_requests=failed,
        http_errors=http_errors,
        evidence=evidence[:20],
        collection_errors=collection_errors,
    )
