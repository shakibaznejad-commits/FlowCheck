# FlowCheck dual login demo

This is a local-only comparison fixture for demonstrating evidence-based login testing. It adds two independent Nginx containers that serve the same Northline Workspace page and use the same synthetic account. They do not call an authentication service, persist credentials, or make external requests. The existing `demo-target` on port 8081 remains unchanged.

## Architecture and files

- `demo-target/dual-login/index.html`, `styles.css`, and `app.js`: shared interface and browser-only synthetic login flow.
- `demo-target/dual-login/healthy.js`: behavior configuration for the correctly behaving site.
- `demo-target/dual-login/buggy.js`: behavior configuration for three intentional, observable defects.
- `demo-target/dual-login/nginx.conf`: serves `/workspace` through the same static application entry point.
- `demo-target/Dockerfile.dual-login`: builds either behavior into an independent Nginx container.
- `demo-target/docker-compose.yml`: adds `healthy-login` and `buggy-login` to the existing local FlowCheck Docker network.

The pages share their markup and styles; only their behavior configuration differs. The FlowCheck planner, executor, analyzer, reporter, and historical data are not modified. SSRF protections remain enabled. Local development allowlists only the exact internal origins `http://flowcheck-healthy.test` and `http://flowcheck-buggy.test`, alongside the pre-existing exact demo origin. The backend still rejects other private-network targets.

## Start locally

In PowerShell at the repository root, start FlowCheck first. This preserves named volumes; do not use `down -v`:

```powershell
docker compose up -d --build backend frontend
```

Start the two targets in a second PowerShell window:

```powershell
docker compose -f demo-target/docker-compose.yml up --build -d healthy-login buggy-login
```

Use these URLs in a host browser:

- Healthy: <http://localhost:8181/>
- Buggy: <http://localhost:8182/>

Use these exact URLs as the target in FlowCheck. Docker DNS aliases resolve them from the backend container:

- Healthy: `http://flowcheck-healthy.test/`
- Buggy: `http://flowcheck-buggy.test/`

The host ports bind to loopback only. The demo services join the local `flowcheck_default` network and are not exposed through production configuration or public proxy ports.

Synthetic credentials (use only in these local fixtures):

- Email: `demo@example.com`
- Password: `CorrectPass123!`
- Deliberately wrong password: `WrongPass123!`

Credentials are compared in browser memory, cleared after submission, and never written to storage or logs. A successful sign-in stores only a synthetic session flag in tab-scoped `sessionStorage`.

## Expected behavior matrix

| Scenario | Healthy | Buggy |
| --- | --- | --- |
| Valid credentials | PASS: workspace is authenticated | PASS: workspace is authenticated |
| Known account with `WrongPass123!` | PASS: rejected | FAIL: authenticated workspace opens |
| Unknown email | PASS: rejected as unknown | PASS: rejected as unknown |
| Empty email, nonempty password | PASS: required validation | FAIL: no required validation; generic error, no authentication |
| Valid email, empty password | PASS: required validation | PASS: required validation |
| Logout then open `/workspace` | PASS: protected area denied | FAIL: prior session still grants access |

The empty-email defect is an input-validation defect only. It is not an authentication bypass. The other defects are reported only when the relevant browser assertion is actually executed and supported by observed page state.

## Reusable FlowCheck prompts

Use the same prompt wording for either target. Do not include the words “healthy” or “buggy” in the prompt.

**A — Broad login coverage**

> Test the complete login functionality of this website. Include successful login, incorrect password, unknown email, empty email, empty password, and logout behavior. Verify the actual authenticated state, not just whether a button was clicked.

**B — Negative authentication**

> Verify that an existing account cannot log in with an incorrect password. Check the visible result and whether authenticated account content becomes accessible.

**C — Empty-field validation**

> Check whether the email and password fields enforce required validation when submitted empty. Verify both HTML/accessibility validity and visible feedback.

**D — Session logout**

> Log in using the authorized demo account, log out, then attempt to revisit the protected workspace. Verify that authenticated access is no longer possible.

For successful-login and logout tests, enter the authorized synthetic credentials through FlowCheck's credential fields. Avoid credentials in prompts. Run each target as its own test run so history does not conflate the two distinct origins.

## Browser checks and evidence

Automated direct-browser regression tests are in `tests/test_dual_login_demo.py`; they interact with the running sites using Playwright Chromium and cover both service liveness, valid login, invalid password, unknown email, empty fields, protected-route access, and logout/session behavior. URL-policy regression coverage is in `tests/test_url_policy.py`.

Run them from the repository root after Docker reports both targets healthy:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp .pytest-dual-login tests/test_url_policy.py tests/test_dual_login_demo.py -q
```

If the matching Playwright Chromium build is not installed on the host, set `FLOWCHECK_TEST_BROWSER_EXECUTABLE` to an installed Chromium-compatible browser path (for example Microsoft Edge) before running the tests. FlowCheck's own executor still runs Chromium inside the backend container.

FlowCheck-generated screenshots and reports remain in FlowCheck's existing owner-scoped artifact/data storage. The demo itself does not create evidence files. Direct Playwright screenshots are not persisted by the regression suite.

### Actual verification record

Verified locally on 2026-10-10:

- Both Compose configurations passed `docker compose config --quiet`.
- `healthy-login` and `buggy-login` reached Docker `healthy`; their host ports bind only to `127.0.0.1:8181` and `127.0.0.1:8182`.
- The backend container resolved both exact internal hostnames (`flowcheck-healthy.test` and `flowcheck-buggy.test`) on `flowcheck_default`; its trusted-origin environment contains only those two new origins plus the pre-existing `http://flowcheck-demo-target.test`.
- Direct Playwright regression suite: **28 passed** against both targets using the installed Microsoft Edge executable (the host Playwright-matched Chromium binary was absent). Three layout widths, 1440, 768, and 375 pixels, had no horizontal overflow.
- Additional direct Playwright checks inside the backend container used its actual `/usr/bin/chromium`: the healthy target rejected `WrongPass123!`; the buggy target authenticated with it; after buggy logout, `/workspace` remained accessible.
- Visual inspection showed matching login surfaces on both targets.
- Real FlowCheck broad-prompt attempt on each target: **NOT TESTED past planning**. Both attempts returned HTTP 502 before Smart Test Setup or browser execution. Backend validation logs show the Planner returned a multi-scenario plan marked `focused`; Pydantic rejected it with the exact rule `focused scope requires exactly one scenario`. This is a Planner output/schema classification failure, not a target-site or browser-observation failure. No scenario result, FlowCheck report, or FlowCheck screenshot was generated for either target. The FlowCheck agent was left unchanged as required.

Therefore this demo's defects are verified through direct Playwright evidence, but **FlowCheck has not yet demonstrated discovery of them**. No FlowCheck report IDs or screenshot paths exist for these two attempted runs. Future successful FlowCheck screenshots/reports are stored in the existing owner-scoped artifact/data storage, not in the demo containers.

## Limits and cleanup

These are static synthetic fixtures, not real identity systems. They have one fake account, no server-side sessions, no real users, and no production use. Browser-only behavior allows deterministic QA checks but does not model server security or backend authorization.

Stop only these two demo services without removing any volumes:

```powershell
docker compose -f demo-target/docker-compose.yml stop healthy-login buggy-login
```

The original `demo-target` stays untouched. Never use `docker compose down -v` or Docker volume deletion for this local demo.
