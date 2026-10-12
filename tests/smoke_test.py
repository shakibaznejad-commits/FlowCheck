import os

from playwright.sync_api import sync_playwright


def main() -> None:
    with sync_playwright() as playwright:
        channel = os.environ.get("PLAYWRIGHT_BROWSER_CHANNEL")
        browser = playwright.chromium.launch(headless=True, channel=channel)
        try:
            page = browser.new_page()
            response = page.goto("https://example.com", wait_until="domcontentloaded")
            assert response is not None and response.ok, "Public webpage did not load successfully"
            assert page.title() == "Example Domain", "Unexpected public webpage title"
            print("Playwright smoke test passed: https://example.com loaded successfully")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
