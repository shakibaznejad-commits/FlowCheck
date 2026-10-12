# Wednesday Production Readiness Implementation Plan

> **For agentic workers:** This plan is being executed inline in the current task.

**Goal:** Add a production Compose deployment behind Nginx while preserving the development Compose stack and current product behavior.

**Architecture:** Build React with Node in a multi-stage image and serve `dist` from Nginx. Nginx serves one origin and proxies API/health routes to an un-published FastAPI service; existing Chromium execution and separate persistent SQLite/evidence volumes remain. HTTP ACME support is available before an optional HTTPS Nginx template is activated after certificate issuance.

**Tech Stack:** React, Vite, Nginx, FastAPI, Playwright/Chromium, Docker Compose, SQLite, Certbot.

**Spec:** `AGENTS.md` and the user-provided Wednesday production-readiness request.

## Global Constraints

- Preserve the existing development `docker-compose.yml` and Vite dev server.
- Publish ports 80/443 only from the Nginx frontend service.
- Do not expose database or screenshot storage outside authenticated API routes.
- Keep Chromium and Playwright available in the backend image.
- Store secrets only in ignored environment files; committed examples contain placeholders.
- Do not add product features or redesign the interface.

## Review Focus

- `/api/health` must map to FastAPI `/health` without changing other `/api/*` paths; verify via Nginx.
- Docker Nginx `envsubst` must preserve native `$host` and `$uri` variables; restrict substitution and run `nginx -t`.
- Backend must have no published host port; inspect rendered production Compose config.
- HTTPS cookie mode must set `Secure` while retaining `HttpOnly` and `SameSite=Lax`; add an auth test.
- ACME challenge routing must work before certificates exist, while HTTPS config is not loaded until certificates are issued; document both states.

---

### Task 1: Production image and proxy configuration

**Files:**
- Create: `frontend/Dockerfile.prod`
- Create: `nginx/http.conf.template`
- Create: `nginx/https.conf.template.example`
- Create: `nginx/proxy-headers.conf`

- [ ] Build Vite assets in a Node stage and serve them with Nginx in the runtime stage.
- [ ] Add SPA fallback, same-origin API and health proxying, ACME webroot, standard headers, directory listing off, and long proxy timeouts.
- [ ] Add an optional TLS template with redirect, certificate paths, and HSTS.

### Task 2: Production Compose and secrets

**Files:**
- Create: `docker-compose.prod.yml`
- Modify: `.env.example`, `.gitignore`
- Create: `.env.production.example`

- [ ] Publish only Nginx ports; keep backend on its Compose network without `ports`.
- [ ] Use dedicated named DB/evidence volumes, restart policies, and health checks.
- [ ] Provide placeholder-only production environment and ignore local credentials, Certbot material, and backups.

### Task 3: Secure-cookie check and operational docs

**Files:**
- Modify: `tests/test_auth.py`, `README.md`
- Create: `DEPLOYMENT.md`

- [ ] Test secure cookie attributes with `FLOWCHECK_COOKIE_SECURE=true`.
- [ ] Document local production commands, Ubuntu setup, volumes, backup/restore, DNS, Certbot issuance/renewal, and feature freeze.

### Task 4: Verify deployment

- [ ] Run pytest, frontend build, both Compose config checks, production build/start, and public Nginx-path checks.
- [ ] Exercise signup/login/session/workspace and a full LLM/Playwright/report/evidence/history/logout flow if local provider/browser conditions allow.
- [ ] Check browser console and mobile overflow; clearly report any HTTPS/server-only steps not verifiable locally.
