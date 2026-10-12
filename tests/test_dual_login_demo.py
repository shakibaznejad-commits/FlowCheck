"""Real browser regression checks for the isolated dual-login fixtures.

These targets only simulate a fictional account in page/session memory. The
tests never connect to or submit data to an external authentication service.
"""

from collections.abc import Iterator
import os
from urllib.request import urlopen

import pytest
from playwright.sync_api import Browser, Page, sync_playwright


HEALTHY_URL = os.getenv("FLOWCHECK_HEALTHY_DEMO_URL", "http://127.0.0.1:8181")
BUGGY_URL = os.getenv("FLOWCHECK_BUGGY_DEMO_URL", "http://127.0.0.1:8182")
TARGETS = [("healthy", HEALTHY_URL), ("buggy", BUGGY_URL)]
VALID_EMAIL = "demo@example.com"
VALID_PASSWORD = "CorrectPass123!"
WRONG_PASSWORD = "WrongPass123!"


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        launch_options: dict[str, object] = {"headless": True}
        browser_executable = os.getenv("FLOWCHECK_TEST_BROWSER_EXECUTABLE")
        if browser_executable:
            launch_options["executable_path"] = browser_executable
        instance = playwright.chromium.launch(**launch_options)
        yield instance
        instance.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context()
    current_page = context.new_page()
    yield current_page
    context.close()


def sign_in(page: Page, url: str, email: str, password: str) -> None:
    page.goto(url, wait_until="domcontentloaded")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()


@pytest.mark.parametrize(("name", "url"), TARGETS)
def test_each_service_is_live_and_has_the_same_login_surface(name: str, url: str) -> None:
    del name
    with urlopen(url, timeout=5) as response:
        assert response.status == 200
        html = response.read().decode("utf-8")
    assert "Northline Workspace" in html
    assert 'id="email"' in html
    assert 'id="password"' in html


@pytest.mark.parametrize("viewport", [(1440, 900), (768, 1024), (375, 812)])
def test_login_page_fits_desktop_tablet_and_mobile(viewport: tuple[int, int], page: Page) -> None:
    page.set_viewport_size({"width": viewport[0], "height": viewport[1]})
    page.goto(HEALTHY_URL, wait_until="domcontentloaded")

    assert page.get_by_role("heading", name="Sign in to your workspace").is_visible()
    assert page.get_by_label("Email").is_visible()
    assert page.get_by_label("Password").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize(("name", "url"), TARGETS)
def test_valid_synthetic_account_logs_in(name: str, url: str, page: Page) -> None:
    del name
    sign_in(page, url, VALID_EMAIL, VALID_PASSWORD)

    assert page.get_by_role("heading", name="Welcome, Demo User").is_visible()
    assert page.get_by_role("button", name="Log out").is_visible()
    assert page.url.endswith("/workspace")


def test_wrong_password_is_rejected_by_healthy_target(page: Page) -> None:
    sign_in(page, HEALTHY_URL, VALID_EMAIL, WRONG_PASSWORD)

    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0
    assert page.get_by_role("status").get_by_text("That password does not match this account.").is_visible()
    assert page.get_by_label("Email").is_visible()


def test_wrong_password_is_deliberately_accepted_by_buggy_target(page: Page) -> None:
    sign_in(page, BUGGY_URL, VALID_EMAIL, WRONG_PASSWORD)

    assert page.get_by_role("heading", name="Welcome, Demo User").is_visible()
    assert page.get_by_role("button", name="Log out").is_visible()
    assert page.url.endswith("/workspace")


@pytest.mark.parametrize(("name", "url"), TARGETS)
def test_unknown_email_is_rejected_by_both_targets(name: str, url: str, page: Page) -> None:
    sign_in(page, url, "unknown@example.com", WRONG_PASSWORD)

    assert page.get_by_role("status").get_by_text("No account found for that email.").is_visible()
    assert page.get_by_label("Email").is_visible()
    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0


def test_empty_email_validation_differs_without_authenticating_buggy_target(page: Page) -> None:
    page.goto(HEALTHY_URL)
    healthy_email = page.get_by_label("Email")
    healthy_email.fill("")
    page.get_by_label("Password").fill(WRONG_PASSWORD)
    assert healthy_email.get_attribute("type") == "email"
    assert healthy_email.get_attribute("required") is not None
    page.get_by_role("button", name="Sign in").click()
    assert healthy_email.evaluate("element => element.validity.valueMissing")
    assert page.get_by_label("Email").is_visible()
    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0

    page.goto(BUGGY_URL)
    buggy_email = page.get_by_label("Email")
    buggy_email.fill("")
    page.get_by_label("Password").fill(WRONG_PASSWORD)
    assert buggy_email.get_attribute("type") == "email"
    assert buggy_email.get_attribute("required") is None
    assert buggy_email.evaluate("element => element.validity.valid")
    page.get_by_role("button", name="Sign in").click()
    assert page.get_by_role("status").get_by_text(
        "Invalid sign-in details. Check your email and password."
    ).is_visible()
    assert page.get_by_label("Email").is_visible()
    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0


@pytest.mark.parametrize(("name", "url"), TARGETS)
def test_empty_password_is_rejected_by_both_targets(name: str, url: str, page: Page) -> None:
    del name
    page.goto(url)
    page.get_by_label("Email").fill(VALID_EMAIL)
    password = page.get_by_label("Password")
    assert password.get_attribute("required") is not None
    page.get_by_role("button", name="Sign in").click()

    assert password.evaluate("element => element.validity.valueMissing")
    assert page.get_by_label("Email").is_visible()
    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0


@pytest.mark.parametrize(("name", "url"), TARGETS)
def test_fresh_session_cannot_open_protected_workspace(name: str, url: str, page: Page) -> None:
    del name
    page.goto(f"{url}/workspace")

    assert page.get_by_label("Email").is_visible()
    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0
    assert not page.url.endswith("/workspace")


def test_logout_invalidates_healthy_session_but_buggy_session_survives(page: Page) -> None:
    sign_in(page, HEALTHY_URL, VALID_EMAIL, VALID_PASSWORD)
    page.get_by_role("button", name="Log out").click()
    assert page.get_by_label("Email").is_visible()
    page.goto(f"{HEALTHY_URL}/workspace")
    assert page.get_by_label("Email").is_visible()
    assert page.get_by_role("heading", name="Welcome, Demo User").count() == 0

    sign_in(page, BUGGY_URL, VALID_EMAIL, VALID_PASSWORD)
    page.get_by_role("button", name="Log out").click()
    assert page.get_by_label("Email").is_visible()
    page.goto(f"{BUGGY_URL}/workspace")
    assert page.get_by_role("heading", name="Welcome, Demo User").is_visible()
    assert page.get_by_role("button", name="Log out").is_visible()
