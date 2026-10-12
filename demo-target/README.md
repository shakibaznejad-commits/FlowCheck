# Northline Supply login QA demo

For a separate local comparison with two independent, visually matched login targets, see [`../DUAL_LOGIN_DEMO_GUIDE.md`](../DUAL_LOGIN_DEMO_GUIDE.md). This existing demo and its `8081` port remain unchanged.

This folder contains an intentionally simulated, non-production login target for FlowCheck investigations. It uses one page and one form for every scenario. Standard credential outcomes model expected behavior; the operator switch can deliberately show an ambiguous failure after valid credentials. The credential values are fictional; the page does not authenticate anyone, send requests, create sessions, or persist credentials. Inputs are compared in browser memory and cleared after each submit.

The local Compose mapping binds port `8081` to loopback only and joins the demo to FlowCheck's existing `flowcheck_default` network. The exact local origin `http://flowcheck-demo-target.test` is allowlisted in the development `.env`; other private targets remain blocked. Production uses its separate internal-only `demo-target` service and does not publish this port.

The ambiguous failure mode is a static UI simulation. It displays a generic error for valid credentials, but it does not return HTTP 503 or simulate a particular backend/root cause. Without server-side evidence, the root cause remains unknown.

## Start locally

From the FlowCheck repository root, with Docker Desktop running, start FlowCheck first so its `flowcheck_default` network exists:

```powershell
docker compose up -d --build backend frontend
```

Then start the demo:

```powershell
docker compose -f demo-target/docker-compose.yml up --build -d
```

Open <http://localhost:8081/> to use the page from the host browser. FlowCheck's backend reaches the same target through <http://flowcheck-demo-target.test/>. The demo publishes host port `8081` on loopback only and adds a DNS alias to the existing FlowCheck network; it does not change FlowCheck's published ports or reverse proxy.

Stop the demo with:

```powershell
docker compose -f demo-target/docker-compose.yml down
```

## Test scenarios

Use the same login form for every attempt. Results below are expected demo behavior, not defects.

| Scenario | Email | Password | Expected result |
| --- | --- | --- | --- |
| Unknown email | `unknown@example.com` | `WrongPass123!` | Remains on the form and shows `Email not found` |
| Known email, wrong password | `demo@example.com` | `WrongPass123!` | Remains on the form and shows `Incorrect password` |
| Valid credentials | `demo@example.com` | `CorrectPass123!` | Shows the mock dashboard with `Welcome, Demo User` |
| Ambiguous valid-login failure | `demo@example.com` | `CorrectPass123!` | Remains on the form and shows `Something went wrong. Please try again later.` |
| Logout | On the mock dashboard | — | Returns to the login form |

The fictional valid credentials are `demo@example.com` / `CorrectPass123!`. No other credential pair grants access. Passwords and email values are never submitted or stored; the mock dashboard is only in-page UI state.

## Operator-controlled ambiguous mode

Append `?demoMode=ambiguous` to the same page URL to enable the generic failure for otherwise valid credentials:

```text
http://localhost:8081/?demoMode=ambiguous
```

Remove the query parameter or open `http://localhost:8081/` to return to the normal mock success mode. The switch is in the URL, so reloading resets the page and selects the mode again. Unknown email and wrong password still show their respective normal validation messages in either mode.

## Smoke test

1. Open <http://localhost:8081/>.
2. Submit `unknown@example.com` / `WrongPass123!`; confirm `Email not found`.
3. Submit `demo@example.com` / `WrongPass123!`; confirm `Incorrect password`.
4. Submit `demo@example.com` / `CorrectPass123!`; confirm `Welcome, Demo User`.
5. Select **Log out**; confirm the login form returns.
6. Open <http://localhost:8081/?demoMode=ambiguous>.
7. Submit `demo@example.com` / `CorrectPass123!`; confirm the generic failure text appears and no dashboard is shown.

## Run FlowCheck against the demo

For a FlowCheck backend running in Docker Desktop on the same Windows machine, use the exact allowlisted local target URL:

```text
http://flowcheck-demo-target.test/
```

For the ambiguous mode, use:

```text
http://flowcheck-demo-target.test/?demoMode=ambiguous
```

For production, the `docker-compose.prod.yml` service attaches the demo to FlowCheck's existing Docker network with the internal hostname `flowcheck-demo-target.test`. The service has no published host port; do not expose port `8081` or change the production reverse proxy. Use this target in FlowCheck:

```text
http://flowcheck-demo-target.test/
```

Append `?demoMode=ambiguous` for the ambiguous outcome. Production trusts only this exact internal origin; other private-network targets remain blocked.

The page only provides controlled browser-visible outcomes. Whether FlowCheck can assert them depends on the current planner/executor/analyzer capabilities; a completed sequence of browser actions alone is not proof that an outcome was verified.
