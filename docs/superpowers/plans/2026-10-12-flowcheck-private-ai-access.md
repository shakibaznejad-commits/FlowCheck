# FlowCheck Private AI Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Work in the current project directory; the repository has no commits and its files are untracked, so a Git worktree would not preserve the current source state.

**Goal:** Enforce owner-approved, quota-limited private AI use while preserving FlowCheck V2 behavior and preparing—but not automatically publishing or deploying—a safe release.

**Architecture:** Use a private numeric user-ID allowlist and a shared backend AI authorization dependency. Put persistent quota reservations and renewable concurrency leases in the existing SQLite database; route every outbound Provider attempt and every Playwright execution through atomic admission. Keep runtime secrets out of source and release artifacts.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, SQLite, OpenAI-compatible Provider client, Playwright, React/Vite, Docker Compose, PowerShell/OpenSSH.

**Spec:** `docs/superpowers/specs/2026-10-12-flowcheck-private-ai-access-design.md`

## Global Constraints

- New registrations receive no AI permission by default.
- Approval uses stable internal FlowCheck user IDs, not unverified email addresses.
- Missing, empty, or malformed allowlist configuration fails closed.
- Provider capacity, outbound attempts, and completed responses are accounted separately.
- Pre-dispatch failures release reservations; attempts that may have reached the Provider are never refunded.
- Per-user execution quota and browser concurrency are separate from Provider-call quota and concurrency.
- Preserve the existing six-scenario and 30-action limits; the complete browser run is limited to 300 seconds.
- Provider timeout remains 60 seconds and retry behavior remains bounded; hidden SDK retries are disabled.
- Provider leases renew every 30 seconds with a 10-minute expiry; browser-execution leases use the same renewal and expiry policy.
- Do not claim a guaranteed monetary spending cap without verified Provider-side enforcement or metering.
- Preserve Smart Test Setup, Broad/Focused planning, History, Saved Suites, Pin, Replay, Compare, user data, and both Northline demos.
- Never print, log, commit, or publish runtime keys, passwords, session cookies, or private data.
- Do not commit or push. Do not modify production until local checks pass and backup/rollback are verified; production identity must be verified through the authenticated owner session.

## Review Focus

- Two requests race for the final daily quota position; only one may reserve it. Test atomic admission with concurrent SQLite sessions.
- A request fails before dispatch versus after dispatch; only the former releases quota. Test reserved, dispatched, and completed-response accounting separately.
- A Provider call exceeds the heartbeat interval, is cancelled, or fails; its lease must remain occupied until the bounded call ends, then release. Test renewal, exception, cancellation, and crash-expiry recovery.
- A Playwright launch fails before browser start versus an expected website failure after start; test reservation release versus started/completed execution counts.
- Unapproved users call every direct AI endpoint or replay a saved suite; test 403 on AI use while preserving owner-scoped non-AI history/suite behavior.

---

### Task 1: Add the persistent atomic usage ledger

**Files:**
- Modify: `backend/database.py`
- Create: `backend/ai_usage.py`
- Test: `tests/test_ai_usage.py`
- Modify: `tests/test_database_migrations.py`

**Interfaces:**
- `UsageLedger(engine, settings)` owns reservation, dispatch, completion, renewal, and release transactions.
- `reserve_provider_attempt(user_id: int) -> ProviderLease`
- `reserve_browser_execution(user_id: int) -> ExecutionLease`
- Lease context managers expose `mark_dispatched()`, `mark_completed_response()`, `mark_started()`, `mark_completed()`, and `renew()` as appropriate.
- Every SQLite admission transaction uses `BEGIN IMMEDIATE`; it checks both persistent daily counts and active unexpired reservations before inserting a lease.

- [x] **Step 1: Write failing database/accounting tests** for separate UTC-day counters, daily quota reservation, pre-dispatch release, non-refundable dispatched attempts, completed responses, execution starts/completions, and safe additive initialization of existing databases.
- [x] **Step 2: Run the focused tests** with `python -m pytest tests/test_ai_usage.py tests/test_database_migrations.py -q`; confirm the new cases fail before implementation.
- [x] **Step 3: Implement the usage tables and atomic ledger** with the approved defaults: 30 Provider attempts/user/day, 10 browser starts/user/day, one concurrent Provider call and one browser execution/user, and two global slots for each resource.
- [x] **Step 4: Add concurrent admission tests** that race more requests than the remaining quota/slots and prove that persisted counts and active leases never exceed configured limits.
- [x] **Step 5: Run the focused usage and migration tests**; verify they pass and that old user/run/suite rows remain intact.

### Task 2: Enforce trusted owner approval on every AI path

**Files:**
- Create: `backend/ai_authorization.py`
- Modify: `backend/auth.py`
- Modify: `backend/main.py`
- Modify: `backend/planner.py`
- Modify: `backend/executor.py`
- Modify: `backend/investigator.py`
- Modify: `backend/analyzer.py`
- Modify: `backend/reporter.py`
- Modify: `.env.example`
- Modify: `.env.production.example`
- Test: `tests/test_ai_authorization.py`
- Test: `tests/test_auth.py`
- Test: `tests/test_suites_and_scenario_history.py`

**Interfaces:**
- `approved_user_ids_from_env() -> frozenset[int]` parses `FLOWCHECK_AI_APPROVED_USER_IDS`; invalid entries fail closed.
- `require_ai_access(user: User) -> User` returns the authenticated allowlisted user or raises 403.
- All AI service entry points receive a server-created `user_id`; no request body field can set identity or approval.
- AI route handlers and service entry points receive the authenticated user ID from the dependency; the Provider wrapper is added in Task 4 and consumes that server-created identity.

- [x] **Step 1: Write failing API tests** for unauthenticated 401, signed-in unapproved 403, empty/malformed allowlist deny, approved allowlist access, and new signup denied by default.
- [x] **Step 2: Write route-coverage tests** for `/api/test-plan`, `/api/execute-plan`, `/api/analyze-results`, `/api/generate-report`, and replay through the same backend routes; verify non-AI history, suites, pin, and compare still work for their owners.
- [x] **Step 3: Implement strict user-ID parsing and the shared dependency**; return a generic safe 403 without revealing approved IDs.
- [x] **Step 4: Pass authenticated identity through Planner, Executor/Investigator, Analyzer, Reporter, and replay**; do not accept identity from request bodies or frontend flags.
- [x] **Step 5: Add tests proving Provider construction requires server-supplied user identity** and that Provider keys never appear in response bodies or captured logs.
- [x] **Step 6: Run authorization, auth, suites/history, and full test suites**; preserve owner-scoped non-AI behavior.

### Task 3: Enforce separate browser-execution quota, leases, and deadline

**Files:**
- Modify: `backend/ai_usage.py`
- Modify: `backend/main.py`
- Modify: `backend/executor.py`
- Modify: `backend/schemas.py`
- Modify: `backend/observations.py` only if needed to enforce the run deadline without losing evidence
- Test: `tests/test_ai_usage.py`
- Test: `tests/test_executor.py`
- Test: `tests/test_runs.py`

**Interfaces:**
- `execute_test_plan(..., user_id: int, deadline: float)` consumes one execution reservation per accepted execution request, regardless of selected scenario count.
- Browser lease renewal runs independently of the synchronous Playwright call every 30 seconds; expiry is 10 minutes and the maximum whole run duration is 300 seconds.
- A request that fails validation or browser setup before launch releases the reservation. Once Playwright launch begins, execution-start count is permanent; terminal PASS/FAIL/BLOCKED/INCONCLUSIVE/NOT_RUN results and evidence are preserved.

- [x] **Step 1: Add failing tests** for pre-launch failure release, website-level failure as a completed execution, concurrent execution quota races, lease renewal, cancellation cleanup, expired-lease recovery, and 300-second deadline status/evidence preservation.
- [x] **Step 2: Run focused tests first**; the deadline test failed before implementation and caught missing NOT_RUN rows after an end-of-scenario deadline.
- [x] **Step 3: Add execution admission after request, plan, selected-scenario, and URL validation** and before starting Playwright work.
- [x] **Step 4: Add renewable execution leases and a monotonic 300-second run deadline**; retain completed scenario results and mark remaining work NOT_RUN.
- [x] **Step 5: Run focused executor/history/quota tests** and the full backend suite.

### Task 4: Route every Provider attempt through lease accounting

**Files:**
- Modify: `backend/llm_provider.py`
- Modify: `backend/ai_usage.py`
- Modify: `backend/planner.py`
- Modify: `backend/investigator.py`
- Modify: `backend/analyzer.py`
- Modify: `backend/reporter.py`
- Test: `tests/test_llm_provider.py`
- Test: `tests/test_planner.py`
- Test: `tests/test_investigator.py`
- Test: `tests/test_analyzer.py`
- Test: `tests/test_reporter.py`

**Interfaces:**
- Each provider attempt acquires a fresh `ProviderLease`; retries never reuse quota reservations.
- Provider attempt lease expires after 10 minutes and renews every 30 seconds while the SDK call is active. SDK retries are set to zero so only the bounded application retry loop initiates outbound attempts.
- On SDK response, increment completed-response count before parsing. On timeout/connection loss after dispatch, keep the attempt count and release only the active slot after the call exits.

- [x] **Step 1: Add failing Provider accounting tests** for post-dispatch timeout, completed response counts, daily quota, retries, identity context, and disabled hidden SDK retries.
- [x] **Step 2: Run focused Provider tests** and observe failures before guarded implementation.
- [x] **Step 3: Add one lease per outbound attempt**; existing SDK retry count remains zero, timeout remains 60 seconds, application retry loops remain bounded.
- [x] **Step 4: Pass user identity and accounting through Planner, Investigator, Analyzer, and Reporter** so all repairs/retries are metered.
- [x] **Step 5: Run all Provider and agent-stage tests** and confirm existing fallback behavior and log safety.

### Task 5: Make local tooling and configuration secret-safe

**Files:**
- Modify: `test.py`
- Modify: `.env.example`
- Modify: `.env.production.example`
- Modify: `.gitignore` only if required to keep real secrets/private data excluded
- Modify: `README.md`
- Modify: `DEPLOYMENT.md`
- Test: `tests/test_secret_safety.py`

**Interfaces:**
- `test.py` reads `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` from the process environment and never prints the key.
- Document private `FLOWCHECK_AI_APPROVED_USER_IDS`, Provider attempt/execution quotas, concurrency limits, lease recovery, and the absence of a FlowCheck monetary hard cap.
- Example files contain placeholders only; no real environment file is edited as part of this task.

- [x] **Step 1: Add source-safety checks** for environment-based provider configuration, secret-safe examples, and ignore rules.
- [x] **Step 2: Remove the hardcoded value** from `test.py` and keep provider configuration environment-based.
- [x] **Step 3: Document private allowlist, quota accounting, lease expiry, and lack of a monetary cap.**
- [x] **Step 4: Finish source and release-candidate recursive secret scans**; report filenames/counts only and never print match values.

### Task 6: Rotate and verify the local 9Router key

**Files:**
- Modify private local `.env` only through secure interactive input.
- No source, release, or production files are modified in this task.

- [ ] **Step 1: Obtain the local `v2` key through the active hidden interactive prompt**; do not accept it in chat or write it to a temporary/plaintext file. (Prompt is open; waiting for secure input.)
- [ ] **Step 2: Update only local `.env` in memory-safe code** while preserving unrelated entries and file permissions; retain the previous key until the new key is usable.
- [ ] **Step 3: Make a minimal real 9Router model request and verify the local backend uses it** without printing the key or request authorization header.
- [ ] **Step 4: If the new local key works, retain the old key until the later production gate; if it fails, restore the previous local value from a protected in-memory backup and report the blocker.**

### Task 7: Run full local regression and prepare a scanned public-release candidate

**Files:**
- Modify only files revealed by failing checks in Tasks 1–5.
- Update: `C:/Users/SADAFCOM/OneDrive/Desktop/FlowCheck-GitHub-Release/` only after all local checks and scans pass.
- Test: all `tests/`, frontend existing test/build commands, Compose files, both Northline demo targets.

- [x] **Step 1: Run the complete backend suite** with the installed Edge browser and running local demos.
- [x] **Step 2: Run frontend checks/build** and confirm existing UI error states handle server responses without redesign.
- [x] **Step 3: Validate both Compose files** without printing resolved environment values.
- [x] **Step 4: Run local security integration tests** for approved/unapproved users, direct AI routes, quotas, lease recovery, and owner-scoped evidence/history.
- [x] **Step 5: Run both Northline demo test targets** and a real Broad five-scenario UI→Provider→Chromium→Analyzer→Reporter→History path.
- [ ] **Step 6: Repeat the real local Provider check with the new local `v2` key**; the current runtime key is configured securely, but its 9Router key name/version cannot be verified from the OpenAI-compatible endpoint.
- [x] **Step 7: Refresh the release directory from validated source**, excluding `.env`, databases, evidence, screenshots, certificates, caches, and other private artifacts.
- [x] **Step 8: Recursively scan release filenames and contents**, including supported PDFs/text/configuration; inspect matches without printing secret values; verify MIT license, copyright, safe examples, both demos, source/tests, and deployment files.
- [ ] **Step 9: Report release readiness only if every test, scan, and required-file check passes**; do not commit or push.

### Task 8: Production readiness gate and separately approved rollout

**Files:**
- No production files until Tasks 1–7 pass, production identity is verified, and a data/configuration backup and rollback plan is verified.
- Modify only production `/opt/flowcheck/.env` and the minimum required backend image/container if rollout is later approved.

- [ ] **Step 1: Identify the owner through an authenticated production browser/session** and read the internal ID from that session's `/api/auth/me`; test that exact ID and an unapproved ID locally. Stop if identity cannot be verified safely.
- [ ] **Step 2: Capture production service/image state and back up `.env`, SQLite consistently, and evidence volume** to a restricted VPS-only location; verify archive readability/checksums and rehearse rollback commands without executing rollback.
- [ ] **Step 3: Present the verified owner ID handling, backup, exact service changes, and local test evidence for final production approval**; do not deploy during local implementation without that approval.
- [ ] **Step 4: If approved, securely enter the production `v2` key, update private allowlist/key settings, and recreate only the backend service**; preserve Nginx/TLS, 9Router configuration, user data, and named volumes.
- [ ] **Step 5: Immediately test owner AI access and unapproved-account 403** through authenticated production sessions; rollback backend/config if either result is wrong.
- [ ] **Step 6: Run production HTTPS, auth/session, provider, four-scenario demo, evidence-ownership, history/replay, and 9Router smoke checks**; only after both local and production `v2` keys are verified, use a documented authenticated 9Router path to revoke old keys. If revocation cannot be confirmed safely, leave old keys active and report the blocker.
- [ ] **Step 7: Report local implementation, local tests, deployment, and production verification as four distinct statuses.**
