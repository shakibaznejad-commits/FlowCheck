# FlowCheck Private AI Access and Safe Release Design

**Date:** 2026-10-12  
**Status:** Proposed for review  
**Copyright:** Copyright (c) 2026 Shakiba ZNejad

## Goal

Keep the public MIT source and the hosted FlowCheck service usable while ensuring that only accounts explicitly approved by the owner can consume the owner's 9Router/LLM capacity. Enforce the policy in the backend, account for usage safely under concurrent requests and restarts, preserve existing user data and product flows, rotate the two `v2` runtime keys without exposing them, and update the public-release folder only after local validation.

## Agreed constraints

- New registrations receive no AI permission by default.
- Approval uses stable internal FlowCheck user IDs, not unverified email addresses.
- No public privilege-grant endpoint and no client-controlled role or approval flag.
- Apply authorization to every direct and indirect Provider path: planning, browser execution and Investigator calls, analysis, reporting, and replay.
- Keep non-AI account, history, suite, pin, and compare functions available according to their existing owner-scoped authorization.
- Keep both independent Northline demos and their synthetic credentials/behavior.
- Keep 9Router upstream credentials private in 9Router and FlowCheck runtime keys private in backend configuration.
- Do not disable old keys until both replacement keys have been verified.
- Do not publish, push, or deploy automatically. Production changes require passed local security tests and a verified data/configuration backup and rollback path.
- Do not claim a fixed monetary cap unless billing enforcement or trustworthy metering provides one.

## Current-state findings

- FastAPI protects the main AI routes with session authentication, but the `User` model has no AI authorization state or role; authenticated new users can reach those routes.
- LLM callers are Planner, Investigator, Analyzer, and Reporter. The browser execution endpoint invokes Investigator indirectly. Replay uses the normal planning/execution flow; it has no separate authorization boundary today.
- Plans are already limited to six scenarios and 30 browser actions. Provider requests use a 60-second timeout with one bounded transport retry. No persistent per-user quota or global/per-user concurrency accounting was found.
- The app uses SQLite in a named production data volume. `initialize_database` already applies additive SQLite columns, so a small additive usage schema fits the existing storage model.
- Production currently answers HTTPS with 200 and redirects HTTP with 301. Its 9Router host port is loopback-only; the router also joins the FlowCheck Compose network, the backend can reach its health route, and the public Nginx configuration has no 9Router route. These are read-only observations from 2026-10-12, not a deployment approval.
- Local `test.py` contains a hardcoded credential-like value. It is absent from the current release folder. The local `.env` was not read. The repository has no commits, so local Git history is currently empty.
- The release folder has 89 files and includes the expected source roots, Compose files, README, MIT license, and safe environment templates. The full recursive content scan still needs to run after changes.

## Design options and recommendation

1. **Allowlist unverified email addresses in environment configuration.** Simple to operate, but an email address is not a trusted identity until verified and can change.
2. **Persist an approval flag on each user and expose an owner administration route.** Flexible, but creates a sensitive privilege-management surface and needs stronger owner identity, audit, and operational recovery.
3. **Private allowlist of stable internal user IDs in backend environment configuration.** No public approval route, no schema migration for identity, and explicit owner control. The owner obtains an account ID from the authenticated account response, adds that ID to private server configuration, then restarts only the backend.

**Recommendation: option 3.** `FLOWCHECK_AI_APPROVED_USER_IDS` is parsed as a strict comma-separated list of positive integer user IDs. Missing, empty, or malformed configuration fails closed. The allowlist is never returned by an API or sent to the frontend. There is no self-service approval and no assumption that registration implies consent to spend owner credits.

## Authorization and call accounting

### One authorization boundary

- Add a shared `require_ai_access` dependency to the direct paid-use routes: `/api/test-plan`, `/api/execute-plan`, `/api/analyze-results`, and `/api/generate-report`.
- Return 401 for no valid session and 403 for a signed-in user whose internal ID is not on the private allowlist. Keep error text clear and do not reveal the allowlist.
- Pass a server-created AI principal (the authenticated user ID) into planner, executor/Investigator, analyzer, and reporter services. Never read a user ID or role from request JSON.
- Route every Provider attempt through one guarded provider-call boundary. The boundary rechecks authorization and obtains usage/concurrency admission immediately before the outbound call. This protects retries, indirect Investigator calls, and future callers from accidentally bypassing route-level policy.
- Keep saved-suite reads/writes, history, pin, compare, auth, and health under their current access rules; they do not invoke the Provider. Replay remains protected because every replayed paid-use operation traverses the same gated routes and provider boundary.

### Atomic persistent quotas and concurrency

Use additive SQLite tables in the existing application database:

- `ai_usage_daily`: `(user_id, utc_date)` primary key, outbound-attempt count, and completed-response count.
- `ai_call_leases`: unique lease token, user ID, state (`reserved` or `dispatched`), and expiry timestamp for active Provider attempts.
- `ai_execution_daily`: `(user_id, utc_date)` primary key, started-execution count, and completed-execution count.
- `ai_execution_leases`: unique lease token, user ID, state, and expiry timestamp for active browser executions.

Admission occurs in a short `BEGIN IMMEDIATE` transaction so simultaneous workers cannot overrun a quota or concurrency slot. It removes expired leases, checks the daily user quota including outstanding reservations, checks per-user and global active-call limits, and inserts a lease atomically.

Provider accounting has three explicit states:

1. **Reserved capacity:** admission reserves one daily quota position and one concurrency slot before the outbound call. Request validation, authorization, configuration, or other failures before dispatch release the reservation and do not count as an outbound attempt.
2. **Outbound attempt:** immediately before handing the request to the Provider SDK, atomically transition the lease from `reserved` to `dispatched` and increment the persistent attempt count. From this point onward the attempt is never refunded: a timeout, cancellation, connection loss, malformed response, or process crash may occur after the Provider has incurred cost.
3. **Completed response:** once the SDK returns an HTTP response, increment the completed-response count before parsing or validating its content. A provider error response is still a completed response; a timeout or connection failure is not. Every retry obtains a fresh reservation and is accounted as a separate attempt.

Use the existing 60-second Provider timeout and bounded application retry. Disable hidden SDK retries so each outbound attempt is visible to the accounting boundary. `finally` releases a lease after the Provider call returns. A lease heartbeat renews active leases every 30 seconds; the initial lease is 10 minutes, well beyond the maximum bounded call timeout. If renewal fails, no new attempt begins. On process crash, no legitimate local call remains active; the conservative expiry delays reuse of its slot while preserving any already-dispatched attempt count. A stale `reserved` lease can free its reservation; a stale `dispatched` lease only frees concurrency and never refunds usage.

Browser executions have separate accounting and are not charged as Provider attempts. One complete Agent/browser execution means one accepted execution request that reaches Playwright launch; it may contain multiple selected scenarios. Reserve an execution slot and daily quota atomically after request/plan validation and immediately before browser launch. Validation or setup failure before launch releases the reservation. Once launch begins, increment the started-execution count permanently; an expected website failure is still a completed execution when FlowCheck reaches a terminal scenario result. Increment completed-execution count when the execution result is finalized. If the backend crashes after launch, the execution remains counted as started but incomplete, and its concurrency lease expires conservatively.

Use separate per-user and global browser-execution concurrency limits, independent of Provider concurrency. Renew active execution leases every 30 seconds with a 10-minute expiry. Enforce a whole-browser-run deadline so a stuck execution cannot hold capacity indefinitely. Expired leases recover capacity after crashes without reversing started execution counts.

Initial safe defaults, configurable only in private backend environment settings:

- 30 Provider attempts per approved user per UTC day.
- 1 concurrent Provider attempt per user.
- 2 concurrent Provider attempts globally for this single-instance deployment.
- 10 Playwright execution starts per approved user per UTC day.
- 1 concurrent browser execution per user and 2 browser executions globally.
- 429 for an exhausted daily quota or occupied concurrency capacity; include a safe retry/reset hint without Provider details.

Persistent daily counts survive backend restarts. Leases coordinate multiple Uvicorn workers sharing the same SQLite file. This design assumes one FlowCheck backend deployment and one shared SQLite database; horizontally split databases are out of scope. There is no guaranteed dollar cap: provider retries, model pricing, and 9Router billing are not currently metered by FlowCheck. Use provider-side spend controls if available and report their verified status separately.

### Browser and Provider budgets

- Preserve the existing six-scenario and 30-action limits.
- Add one configured maximum wall-clock duration of 300 seconds for the complete browser execution, enforced with a monotonic deadline. On expiry, keep completed scenario results and evidence and mark remaining work according to existing BLOCKED/NOT_RUN semantics.
- Preserve Provider timeout and bounded retry behavior. Do not add retries beyond existing limits.
- Reject over-limit requests before launching unnecessary browser or Provider work.

## Secret handling, rotation, and release

- Replace the literal in local `test.py` with `LLM_API_KEY`, `LLM_BASE_URL`, and `LLM_MODEL` environment configuration. The key must never be printed, logged, embedded in command arguments, or committed.
- Add only safe placeholders and documented allowlist/quota settings to `.env.example` and `.env.production.example`; keep `.env`, production secrets, databases, evidence, TLS material, and 9Router persistent storage outside the release.
- Install the local and production `v2` keys via hidden interactive input or an approved secret store. Update the local `.env` first, verify a minimal real Provider request, then update the VPS `.env` over SSH without displaying the value and restart only the backend. Keep each old key until that environment's replacement is verified. Revoke old keys only through a documented authenticated 9Router management path; if that cannot be safely verified, leave them active and report the blocker.
- After tests pass, refresh `FlowCheck-GitHub-Release` from safe source/configuration only. Run a recursive filename and content scan, including text and supported document formats, without printing matched secret values. Review every finding and confirm that no runtime data or credential is present. Do not commit or push.
- Preserve both Northline demo targets and verify their files, Compose services, aliases, and existing tests remain present.

## Production networking and rollout

- Preserve the observed 9Router loopback host binding, backend network reachability, existing HTTPS/Nginx configuration, certificates, and all named data/evidence volumes. Do not add a public 9Router route or host port.
- Before production changes, record current image IDs and Compose state; make a restricted server-side backup of `.env`, SQLite using a consistent SQLite backup method, and evidence volume; verify backup readability and checksums; record rollback commands and image tags. Keep the backup on the VPS with restrictive permissions.
- Complete automated tests, frontend build, local Compose validation, and local provider/access checks before production.
- Only then update the private allowlist/key/runtime settings and rebuild/recreate the backend service. Recreate frontend only if its code actually changed. Do not run `down -v`, prune, or replace proxy/certificate configuration.
- Verify public HTTPS/redirect, signup/login/session, owner-approved and denied account behavior, direct API denial, AI provider connectivity, four demo scenarios, private evidence/history ownership, and 9Router availability. If a critical smoke test fails, restore the previous backend image/configuration without deleting volumes.
- Production remains **not secured/deployed** until those live checks have actually passed.

## Verification plan

Automated tests must prove:

1. Unauthenticated AI calls return 401; authenticated IDs absent from the allowlist return 403.
2. New signup does not gain AI access; an owner-approved internal ID can use the intended AI routes.
3. Planner, execution/Investigator, Analyzer, Reporter, retries, and replay cannot bypass the shared guard.
4. Non-AI owner-scoped history, suites, pin, and compare retain their current behavior; one user cannot access another user's data/evidence.
5. Concurrent Provider admission never exceeds configured daily attempt counts or global/per-user lease limits; pre-dispatch failures release reservations, dispatched failures are never refunded, returned HTTP responses count as completed responses, and expired leases recover capacity safely after failure/restart.
6. Concurrent browser admission never exceeds the separate daily execution quota or browser concurrency limits; pre-launch failures release reservations, launched executions remain counted even when they fail, and completed runs are recorded independently.
7. Existing scenario/action limits, full-run deadline behavior, all five scenario statuses, Smart Test Setup, Broad/Focused planning, History, Saved Suites, Pin, Replay, Compare, and both Northline demos continue to work.
8. No key appears in API responses, logs, the frontend build, source examples, or the refreshed release folder; `test.py` uses environment configuration.
9. Run the complete backend suite, frontend tests/build, Compose validation, isolated local security integration tests, and—when both v2 keys are securely available—a real local Provider request. Production tests are reported separately.

## Production identity gate

Before any production rollout, identify the owner using a trusted authenticated session on the actual production app, then read the owner's internal numeric user ID from that authenticated `/api/auth/me` response. Do not infer it from local SQLite, email, registration order, logs, or a client-supplied identifier. Verify that the response belongs to the owner by completing the normal authenticated sign-in in the owner-controlled browser/session. Add only this verified ID to the private `FLOWCHECK_AI_APPROVED_USER_IDS` value prepared for the VPS. Before rollout, test the same allowlist policy locally with that exact ID and a separate unapproved account. Immediately after the backend update, make the first production authorization checks through authenticated sessions: the owner must retain AI access and the unapproved account must receive 403. Roll back the backend/configuration if either check fails. Do not expose either session cookie or allowlist value. If the owner's identity cannot be established through that trusted process, stop the rollout.

## Non-goals

- No public owner/admin endpoint, new authentication provider, email verification system, or client-side authorization trust.
- No database replacement, Redis, queue, or distributed service rewrite.
- No LLM or browser behavior redesign, new test flows, or changes to the Northline demo behavior.
- No hard monetary cap claimed without provider-enforced billing controls.
- No GitHub commit/push and no production deployment before local checks and verified backup/rollback readiness.

## Review checklist

- [x] The approval identity is a stable internal user ID; no email trust assumption remains.
- [x] New users fail closed and no public privilege grant route is proposed.
- [x] All current direct and indirect Provider paths are named and share a guarded call boundary.
- [x] Usage increments and concurrency leases are designed to be atomic, persistent, and recoverable after restarts.
- [x] Provider capacity reservation, outbound attempts, and completed responses are accounted separately; browser execution has its own per-user daily quota and independent concurrency leases.
- [x] Active Provider/browser leases renew while work is in progress, have conservative bounded expiries, and recover capacity after process crashes without refunding potentially billable attempts or launched executions.
- [x] Production approval is gated on trusted identification of the owner user ID and live authorization checks for both approved and unapproved accounts.
- [x] Limits distinguish request/call controls from monetary billing guarantees.
- [x] Existing V2 flows, user data, both demos, TLS, and 9Router access are explicitly preserved.
- [x] Key values are only installed through hidden input/secret store and never printed.
- [x] Production and public release remain gated by local verification, scanning, backup, and rollback checks.
