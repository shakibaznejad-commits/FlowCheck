import os
import re
from pathlib import Path

from playwright.sync_api import Page

from .log_safety import safe_log_message
from .schemas import ScreenshotEvidence


RUN_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
FILENAME_PATTERN = re.compile(r"^test-\d{2}-(failure|final)\.png$")


def get_artifacts_root() -> Path:
    configured_root = os.getenv("FLOWCHECK_ARTIFACTS_DIR")
    if configured_root:
        return Path(configured_root).resolve()
    return (Path(__file__).resolve().parent / "artifacts").resolve()


def resolve_evidence_file(run_id: str, filename: str) -> Path | None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        return None
    if not FILENAME_PATTERN.fullmatch(filename):
        return None

    root = get_artifacts_root().resolve()
    candidate = (root / run_id / filename).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def capture_screenshot(
    page: Page,
    *,
    run_id: str,
    filename: str,
    label: str,
    test_index: int,
    step_index: int | None,
    secrets: tuple[str, ...] = (),
) -> ScreenshotEvidence | None:
    if not RUN_ID_PATTERN.fullmatch(run_id) or not FILENAME_PATTERN.fullmatch(filename):
        print("SCREENSHOT CAPTURE FAILED: invalid generated evidence identifier.", flush=True)
        return None

    root = get_artifacts_root().resolve()
    run_directory = root / run_id
    try:
        root.mkdir(parents=True, exist_ok=True)
        if run_directory.exists() and not run_directory.resolve().is_relative_to(root):
            raise PermissionError("Evidence directory escaped the configured root.")
        run_directory.mkdir(parents=True, exist_ok=True)
        if not run_directory.resolve().is_relative_to(root):
            raise PermissionError("Evidence directory escaped the configured root.")
        destination = run_directory / filename
        screenshot_options = {"path": str(destination), "full_page": True}
        try:
            masks = [page.locator("input, textarea")]
            masks.extend(
                page.get_by_text(secret, exact=False)
                for secret in secrets
                if len(secret) >= 3
            )
            screenshot_options["mask"] = masks
            screenshot_options["mask_color"] = "#333333"
        except (AttributeError, Exception):
            # Test doubles and non-standard pages may not expose locators.
            pass
        page.screenshot(**screenshot_options)
        if not destination.is_file():
            raise OSError("Screenshot file was not created.")
    except Exception as exc:
        print(
            "SCREENSHOT CAPTURE FAILED "
            f"{type(exc).__name__}: {safe_log_message(str(exc))}",
            flush=True,
        )
        return None

    return ScreenshotEvidence(
        type="screenshot",
        label=label,
        filename=filename,
        test_index=test_index,
        step_index=step_index,
    )
