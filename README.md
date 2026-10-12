# FlowCheck

FlowCheck is an agentic web QA tool for **login and signup flows**. Give it a website URL and a natural-language testing goal; it proposes scenarios, runs approved browser actions, evaluates observed assertions, and produces an evidence-backed report. The planner, analyzer, and reporter use an external OpenAI-compatible LLM provider. Playwright executes structured actions; generated code is never run.

## What it does

- **Plan:** Turn a broad or focused goal into structured test scenarios and show them in Smart Test Setup before execution.
- **Run:** Execute selected scenarios in isolated Chromium contexts with bounded actions and time.
- **Verify:** Compare explicit assertions with browser-observed page state. PASS, FAIL, BLOCKED, INCONCLUSIVE, and NOT_RUN remain distinct per scenario.
- **Explain:** Analyze validated observations, preserve screenshot references, and separate confirmed facts from hypotheses.
- **Review:** Show a final report, private evidence, run history, saved suites, and replay comparisons for the signed-in owner.

## Stack and architecture

| Layer | Technology |
| --- | --- |
| Web app | React 19, Vite 6 |
| API and schemas | FastAPI, Pydantic |
| Agent stages | Planner, Investigator, Executor, Analyzer, Reporter |
| Browser | Playwright with Chromium |
| Storage and accounts | SQLite, SQLAlchemy, Argon2 password hashing, server-side sessions |
| Packaging | Docker Compose; Nginx for production HTTPS reverse proxy |

```text
React UI -> FastAPI -> Planner -> approved scenarios -> Playwright Executor
                 -> browser observations and assertions -> Analyzer -> Reporter
                 -> owner-scoped history, report, and screenshot evidence
```

The LLM proposes structured test intent and explains validated results. Browser observations and executed assertions determine scenario outcomes; an unavailable or malformed AI analysis must not invent a pass or erase completed browser evidence.

## Local installation with Docker

Prerequisites: Docker Desktop or Docker Engine with Compose, and credentials for an **external** OpenAI-compatible Chat Completions provider. No local LLM is included.

1. Copy the safe configuration template:

   ```sh
   cp .env.example .env
   ```

   In PowerShell, use `Copy-Item .env.example .env`. Set `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` in your private `.env`. The sample base URL points to a host-side provider proxy and is only an example; replace it if your provider uses another endpoint. Keep `FLOWCHECK_COOKIE_SECURE=false` for local HTTP.

2. From the repository root, validate and start the app:

   ```sh
   docker compose config --quiet
   docker compose up --build -d
   docker compose ps
   ```

3. Open <http://localhost:5173/>, create an account, and sign in. Check the API at <http://localhost:8000/health>.

The local Compose stack stores SQLite data and screenshots in separate named volumes. `docker compose down` stops the app and keeps the volumes; `docker compose down -v` deletes them.

## Run the Healthy and Buggy Northline demos

These are independent, synthetic login websites. They do not send or store entered credentials. Start FlowCheck first so its `flowcheck_default` Docker network exists, then run:

```sh
docker compose -f demo-target/docker-compose.yml up --build -d healthy-login buggy-login
```

| Target | Host browser | URL to enter in FlowCheck |
| --- | --- | --- |
| Healthy | <http://localhost:8181/> | `http://flowcheck-healthy.test/` |
| Buggy | <http://localhost:8182/> | `http://flowcheck-buggy.test/` |

The host ports bind to loopback. The backend resolves the exact `.test` aliases on the Docker network; the local `.env.example` allowlists only those demo origins and the older single-form fixture. Other private-network destinations remain blocked. See [the dual-login guide](DUAL_LOGIN_DEMO_GUIDE.md) for the **fictional** test account, scenario matrix, and expected demo defects. The older isolated fixture is available at <http://localhost:8081/> after starting `demo-target` with the same Compose file.

For a broad run, enter a demo target URL and a goal such as:

> Test the complete login functionality. Include successful login, incorrect password, unknown email, empty email, empty password, and logout behavior. Verify the actual authenticated state.

Review the proposed scenarios in Smart Test Setup. Supply the demo's valid reference credentials only in the dedicated fields when a positive scenario requires them; never put passwords in the natural-language goal. Select scenarios, run them, then review independent outcomes, evidence, analysis, and the final report. History and replay create owner-scoped saved runs.

## Checks

The project includes backend tests and a frontend check/build script. Run the backend checks that do not need live demo containers:

```sh
python -m pip install -r backend/requirements.txt -r tests/requirements.txt
python -m pytest tests --ignore=tests/test_dual_login_demo.py
cd frontend
npm ci
npm test
npm run build
```

For the complete browser suite on the host, install Chromium with `python -m playwright install chromium`, start the Healthy and Buggy demo containers as shown above, then run `python -m pytest tests` from the repository root. The public-webpage smoke test is `python tests/smoke_test.py`. The backend Dockerfile installs Chromium in its image; the host Python environment needs its own Playwright browser installation.

Production Compose, Nginx templates, HTTPS setup, backup, and rollback guidance are in [DEPLOYMENT.md](DEPLOYMENT.md). Use a private production `.env` based on `.env.production.example`, supply your own domain and certificates, and validate with `docker compose -f docker-compose.prod.yml config --quiet`. Production deployment is not required for local testing.

## Security and limitations

- Test only sites and accounts you are authorized to use. Repeated login attempts may trigger rate limits or account lockouts.
- The URL policy blocks private/internal destinations unless an **exact** trusted origin is configured for an isolated fixture. Do not add wildcard origins or disable private-network checks.
- Raw reference passwords are supplied only for execution. Plans, reports, history, and LLM prompts must use placeholders or redacted observations. Evidence endpoints and run history require owner authorization.
- FlowCheck currently focuses on login/signup with Chromium. It is not a security scanner, CAPTCHA solver, 2FA tool, performance tester, or multi-browser suite.
- The Northline targets are static simulations. Browser evidence can confirm UI behavior; it cannot establish a server-side root cause without server evidence.
- Screenshots are generated per run and are deliberately **not** committed to this repository. No user screenshots or reports are included in the public source tree.
- Never commit `.env`, API credentials, private certificates, SQLite files, or user artifacts. Rotate any key that has appeared in a local scratch file or public history.
- AI access is disabled for every account unless its stable internal user ID is explicitly listed in the private `FLOWCHECK_AI_APPROVED_USER_IDS` environment setting. New registrations do not receive AI access. Obtain the ID from that user's authenticated `/api/auth/me` response; do not approve an email address or guess an ID. Keep the allowlist in `.env`, never in source or public release files.
- Usage defaults are 30 Provider attempts and 10 browser executions per user per UTC day, one concurrent Provider call and one browser run per user, and two global slots for each. Each outbound retry consumes another Provider attempt; pre-dispatch failures release the reservation, while an attempt that may have reached the Provider is not refunded. Leases renew every 30 seconds and expire after 10 minutes if a process crashes. A browser run has a 300-second deadline; completed scenario results and screenshots are retained.
- FlowCheck quotas limit application use; they are not a guaranteed monetary cap. Configure and monitor real billing limits at the Provider.
