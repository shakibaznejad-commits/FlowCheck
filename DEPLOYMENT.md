# FlowCheck Production Deployment

This guide deploys the MVP on one Ubuntu server with Docker Engine and the Docker Compose plugin. It keeps the existing development Compose setup separate. The public web origin is Nginx; FastAPI, SQLite, and screenshots are not published as host ports.

## 1. Prerequisites

- Ubuntu 22.04 or newer with Docker Engine and `docker compose` (Compose plugin).
- A public IPv4 address; ports 80 and 443 available on the server and allowed by the cloud firewall.
- A registered domain that you control when enabling HTTPS.
- An API key and endpoint for an OpenAI-compatible external LLM provider.

Check Docker before continuing:

```bash
docker --version
docker compose version
```

Install Docker Engine and the Compose plugin using Docker's [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/).

## 2. Copy the repository to the server

Use the repository URL supplied for your competition project:

```bash
git clone <FLOWCHECK_REPOSITORY_URL> /opt/flowcheck
cd /opt/flowcheck
```

If Git access is unavailable, copy the project to `/opt/flowcheck` using your approved file transfer method. Keep `.env` out of source control and transfers to untrusted destinations.

## 3. Configure the production environment

Create the private environment file and edit it:

```bash
cp .env.production.example .env
nano .env
```

Set these values in `.env`:

```dotenv
LLM_API_KEY=your-real-provider-key
LLM_BASE_URL=https://your-provider.example/v1
LLM_MODEL=your-provider-model
FLOWCHECK_COOKIE_SECURE=true
YOUR_DOMAIN=YOUR_DOMAIN
```

Replace `YOUR_DOMAIN` with the real hostname, for example `flowcheck.example.com`. Do not commit `.env`. Production sets `FLOWCHECK_COOKIE_SECURE=true`; session cookies remain `HttpOnly` and `SameSite=Lax`.

The frontend uses relative `/api/...` paths. The browser therefore stays on one origin; no backend hostname or LLM key is sent to frontend JavaScript.

## 4. Build and start the HTTP deployment

First start the HTTP service so the ACME HTTP challenge can reach Nginx:

```bash
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml up --build -d
```

The session cookie is already Secure, so sign-in is only usable after HTTPS is enabled in step 8. Do not temporarily turn off the Secure flag on a public server.

## 5. Check status, health, and logs

```bash
docker compose -f docker-compose.prod.yml ps
curl --fail http://localhost/health
docker compose -f docker-compose.prod.yml exec web nginx -t
```

Follow all logs or just backend logs:

```bash
docker compose -f docker-compose.prod.yml logs -f
docker compose -f docker-compose.prod.yml logs -f backend
```

Only the `web` service publishes host ports 80 and 443. The backend listens on port 8000 inside the Compose network and has no host `ports` mapping. Nginx proxies `/api/` to FastAPI, maps `/api/health` and `/health` to FastAPI `/health`, and serves the React SPA with an `index.html` fallback. Evidence continues through the authenticated API; neither SQLite nor the artifacts directory is served as static content.

Nginx forwards `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Real-IP`; Uvicorn proxy-header support is enabled for the private Compose network. Proxy read/send timeouts are 300 seconds for the long planner, browser, analyzer, and reporter requests.

## 6. Domain and firewall

Create a DNS `A` record for `YOUR_DOMAIN` that points to the server's public IPv4 address. Add an `AAAA` record only if IPv6 is configured and reachable. Wait for DNS to resolve before requesting a certificate.

Allow SSH and web traffic in the Ubuntu firewall:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

Also allow inbound TCP 80/443 in the hosting provider's firewall/security group. Do not open port 8000.

## 7. Request a Let's Encrypt certificate

Nginx serves the ACME webroot at `/.well-known/acme-challenge/`. Request a certificate after DNS points to this server and HTTP health responds:

```bash
docker compose -f docker-compose.prod.yml run --rm certbot certonly \
  --webroot --webroot-path /var/www/certbot \
  --email YOUR_EMAIL \
  --agree-tos --no-eff-email --non-interactive \
  -d YOUR_DOMAIN
```

Replace `YOUR_EMAIL` and `YOUR_DOMAIN` in the command. Certbot stores its configuration and certificates in the ignored `certbot/conf/` directory, mounted read-only into Nginx.

## 8. Enable HTTPS

The TLS server template is intentionally inactive until a certificate exists. Back up the HTTP template, activate the HTTPS template, and recreate Nginx:

```bash
cp frontend/nginx/default.conf.template frontend/nginx/default.conf.template.http-backup
cp frontend/nginx/https.conf.template.example frontend/nginx/default.conf.template
docker compose -f docker-compose.prod.yml up -d --force-recreate web
docker compose -f docker-compose.prod.yml exec web nginx -t
```

The HTTPS template redirects normal HTTP requests to HTTPS while keeping the ACME challenge reachable. It enables TLS 1.2/1.3 and HSTS. Keep this in `.env`:

```dotenv
FLOWCHECK_COOKIE_SECURE=true
```

Check the public site at `https://YOUR_DOMAIN/` and the health route at `https://YOUR_DOMAIN/health`. Verify sign-up/login and refresh after HTTPS is live. The cookie is `Secure; HttpOnly; SameSite=Lax` and session values are never readable by frontend JavaScript.

### Certificate renewal

Certbot runs as a one-shot Compose service, so schedule renewal on Ubuntu. This daily job renews only when needed and reloads Nginx afterward:

```bash
sudo tee /etc/cron.d/flowcheck-certbot-renewal >/dev/null <<'EOF'
17 3 * * * root cd /opt/flowcheck && /usr/bin/docker compose -f docker-compose.prod.yml run --rm certbot renew --quiet && /usr/bin/docker compose -f docker-compose.prod.yml exec -T web nginx -s reload
EOF
sudo chmod 644 /etc/cron.d/flowcheck-certbot-renewal
```

Test the renewal path:

```bash
cd /opt/flowcheck
docker compose -f docker-compose.prod.yml run --rm certbot renew --dry-run
```

## 9. Restart, stop, and update

Restart services:

```bash
docker compose -f docker-compose.prod.yml restart
```

Stop and start without removing containers or volumes:

```bash
docker compose -f docker-compose.prod.yml stop
docker compose -f docker-compose.prod.yml start
```

For an incremental production release, use the guarded procedure in section 12. It rebuilds only `backend`, `web`, and `demo-target`; it does not stop other stacks or remove volumes. Back up the application files first. The project currently creates new SQLite tables at startup and does not have a migration framework.

## 10. Persistence and backups

Production uses two named Docker volumes:

- `flowcheck_prod_data` stores `/app/data/flowcheck.db` (users, sessions, and run history).
- `flowcheck_prod_artifacts` stores `/app/artifacts` (Playwright screenshots).

`docker compose -f docker-compose.prod.yml down` removes containers and the network but keeps named volumes. **Never add `-v`** when stopping a deployment that must retain data.

For a consistent backup, stop backend writes, archive both volumes, then start the app again:

```bash
cd /opt/flowcheck
mkdir -p backups
docker compose -f docker-compose.prod.yml stop backend
docker run --rm -v flowcheck_prod_data:/source:ro -v "$PWD/backups:/backup" alpine:3.22 tar -czf /backup/flowcheck-data.tar.gz -C /source .
docker run --rm -v flowcheck_prod_artifacts:/source:ro -v "$PWD/backups:/backup" alpine:3.22 tar -czf /backup/flowcheck-artifacts.tar.gz -C /source .
tar -czf backups/flowcheck-certbot.tar.gz certbot/conf
docker compose -f docker-compose.prod.yml start backend
```

Store backups off-server with restrictive permissions. They contain user data, screenshots, TLS private keys, and may include environment secrets if you separately back up `.env`; protect them accordingly.

### Restore overview

Stop the stack, restore each archive into its matching named volume, restore `certbot/conf/` if HTTPS certificates are needed, then start the stack. Example for the database archive:

```bash
docker compose -f docker-compose.prod.yml down
docker run --rm -v flowcheck_prod_data:/restore -v "$PWD/backups:/backup" alpine:3.22 sh -c 'cd /restore && tar -xzf /backup/flowcheck-data.tar.gz'
```

Repeat with `flowcheck_prod_artifacts` and `flowcheck-artifacts.tar.gz`. Restore the certificate archive to `certbot/conf/`, ensure `.env` is present and protected, then run `docker compose -f docker-compose.prod.yml up -d`. Test restore procedures on a separate host before relying on them.

## 11. Local production smoke test

With Docker running and `.env` configured, local HTTP auth needs an insecure cookie because there is no certificate. Limit this override to local testing only.

PowerShell:

```powershell
$env:FLOWCHECK_COOKIE_SECURE = "false"
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml up --build -d
curl.exe --fail http://localhost/health
```

Then open <http://localhost/> and exercise sign-up, sign-in, the workspace, a test run, report/screenshots, History, refresh, and logout. Verify there is no horizontal page overflow at a mobile viewport. On the server, use `FLOWCHECK_COOKIE_SECURE=true` and the HTTPS origin instead.

## Feature freeze

The MVP feature set is frozen for competition submission. Work after this point is limited to deployment fixes, critical bugs, documentation, and final UI polish.

## 12. Safe incremental release for an existing FlowCheck VPS

Replace `YOUR_SERVER_IP` and `YOUR_DOMAIN` with your own deployment values before running the example commands.

This procedure is for the existing server at `YOUR_SERVER_IP`, project path `/opt/flowcheck`, and public origin `https://YOUR_DOMAIN/`. It preserves the existing `web` HTTPS/Nginx setup, `.env`, Certbot files, Docker volumes, and any separate 9Router containers. It never runs `docker compose down`, `down -v`, or a volume/network prune. Do not continue if the preflight does not match the expected current deployment.

Read-only preflight on 2026-10-09 confirmed public HTTPS `200`, HTTP `301`, valid Nginx syntax, existing certificate files, both named production volumes, and the expected network. The current backend is healthy; the web container is marked unhealthy because its HTTP health probe follows the redirect and fails certificate verification against the loopback probe. The proposed Nginx config/process health probe passed against the live container. The current remote Compose project has only `backend` and `web`; no demo service has been deployed.

### 12.1 Read-only preflight on the VPS

Run these first in an SSH terminal on the Ubuntu VPS. Confirm HTTPS is 200, HTTP redirects to HTTPS, Nginx config passes, the named certificate files exist, and the existing services and volumes are present. The commands do not print certificate contents or `.env` values.

```bash
cd /opt/flowcheck
docker compose -f docker-compose.prod.yml ps
curl -sS -o /dev/null -w 'HTTPS %{http_code}\n' https://YOUR_DOMAIN/
curl -sSI --max-time 10 http://YOUR_DOMAIN/ | head -n 5
docker compose -f docker-compose.prod.yml exec -T web nginx -t
docker compose -f docker-compose.prod.yml exec -T web nginx -T 2>/dev/null | grep -E 'server_name|listen 443|ssl_certificate'
test -s certbot/conf/live/YOUR_DOMAIN/fullchain.pem && echo 'TLS fullchain present'
test -s certbot/conf/live/YOUR_DOMAIN/privkey.pem && echo 'TLS private key present'
docker volume ls --format '{{.Name}}' | grep '^flowcheck_prod_'
docker network ls --format '{{.Name}}' | grep 'flowcheck-prod_flowcheck_network'
```

Expected: HTTPS `200`, HTTP redirects to `https://YOUR_DOMAIN/`, Nginx config succeeds, the backend resolves all three aliases and receives HTTP `200`, and both production volumes remain present. All five FlowCheck services are healthy/running. Verify no host port is published for any demo by inspecting each demo container's `.NetworkSettings.Ports`; each `80/tcp` value should be `null`.

### 12.2 Create a source-only archive in Windows PowerShell

From the FlowCheck repository root, run:

```powershell
$releaseArchive = & .\scripts\prepare-production-archive.ps1
Get-FileHash -Algorithm SHA256 $releaseArchive
scp $releaseArchive root@YOUR_SERVER_IP:/tmp/flowcheck-release.zip
```

The script packages runtime source, production Compose, the deployment guide, and both demo targets; it omits the separate local demo Compose file. It includes `demo-target/dual-login/` and `Dockerfile.dual-login`, but excludes `.env` files, credentials, `.git`, databases, screenshots, artifacts, certificates, backups, local dependencies, and logs. The frontend Nginx templates are included for the image build context, but the VPS extraction command below deliberately skips them so the verified live TLS configuration stays in place. Planner/analyzer/reporter logs contain sanitized validation diagnostics without input values; provider errors redact submitted form values. Do not copy `.env` or backup archives to Windows or `/tmp`.

Previously generated Docker logs may predate this redaction, and this environment cannot inspect the live VPS logs. Do not share old logs until they have been reviewed locally for prior request/plan output.

### 12.3 Back up and update only the application services

Run on the VPS only after the preflight succeeds. The backup contains application source and Compose/Nginx configuration for rollback, while excluding `.env`, databases, screenshots, artifacts, certificates, dependencies, and logs. Restrictive permissions protect the backup directory.

```bash
set -euo pipefail
cd /opt/flowcheck
umask 077
stamp=$(date -u +%Y%m%d-%H%M%S)
backup="/opt/flowcheck/backups/release-$stamp"
stage="/tmp/flowcheck-release-stage-$stamp"
mkdir -p "$backup" "$stage"
docker image tag flowcheck-prod-backend "flowcheck-rollback-backend:$stamp"
docker image tag flowcheck-prod-web "flowcheck-rollback-web:$stamp"
docker image tag flowcheck-prod-demo-target "flowcheck-rollback-demo-target:$stamp"
tar -czf "$backup/source-config.tar.gz" \
  --exclude='.env*' --exclude='*/.env*' --exclude='*.log' --exclude='*.png' \
  --exclude='.git' --exclude='*/.git' --exclude='*/__pycache__' \
  --exclude='*/node_modules' --exclude='*/.venv' --exclude='*/venv' \
  --exclude='*/dist' --exclude='*.db' --exclude='*.sqlite*' --exclude='backend/data' \
  --exclude='backend/artifacts' --exclude='backups' --exclude='certbot' \
  docker-compose.prod.yml backend frontend demo-target DEPLOYMENT.md
chmod 600 "$backup/source-config.tar.gz"
printf 'Rollback backup: %s\n' "$backup/source-config.tar.gz"
sha256sum /tmp/flowcheck-release.zip
python3 -m zipfile -e /tmp/flowcheck-release.zip "$stage"
cp "$stage/docker-compose.prod.yml" docker-compose.prod.yml
cp -a "$stage/backend/." backend/
cp "$stage/frontend/Dockerfile.prod" "$stage/frontend/package.json" \
  "$stage/frontend/package-lock.json" "$stage/frontend/index.html" \
  "$stage/frontend/vite.config.js" "$stage/frontend/.dockerignore" frontend/
cp -a "$stage/frontend/src" frontend/
cp -a "$stage/demo-target/." demo-target/
cp "$stage/DEPLOYMENT.md" DEPLOYMENT.md
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml build backend web demo-target healthy-login buggy-login
docker compose -f docker-compose.prod.yml up -d backend web demo-target healthy-login buggy-login
docker compose -f docker-compose.prod.yml ps backend web demo-target healthy-login buggy-login
```

The production Compose stack attaches the old demo and both new independent login demos to the existing `flowcheck_network`. Their aliases are `flowcheck-demo-target.test`, `flowcheck-healthy.test`, and `flowcheck-buggy.test`; none has a host `ports` mapping. The backend trusts only those three exact HTTP origins. SSRF rejection for other private/local targets remains enabled. Existing `.env` values, including the configured 9Router endpoint and credentials, are read in place and are not copied or edited. The existing 9router service, database/evidence volumes, Nginx template mount, and TLS certificate mounts are not rebuilt or removed. The commands target only the named FlowCheck services.

### 12.4 Post-deployment checks

Run on the VPS:

```bash
cd /opt/flowcheck
curl -sS -o /dev/null -w 'HTTPS %{http_code}\n' https://YOUR_DOMAIN/
curl -sSI --max-time 10 http://YOUR_DOMAIN/ | head -n 5
docker compose -f docker-compose.prod.yml exec -T web nginx -t
docker compose -f docker-compose.prod.yml exec -T backend python -c "import os,socket,urllib.request; hosts=('flowcheck-demo-target.test','flowcheck-healthy.test','flowcheck-buggy.test'); expected=','.join('http://'+h for h in hosts); assert os.environ.get('FLOWCHECK_TRUSTED_TEST_ORIGINS') == expected; [(print(h,socket.gethostbyname(h),urllib.request.urlopen('http://'+h,timeout=5).status)) for h in hosts]"
docker compose -f docker-compose.prod.yml ps backend web demo-target healthy-login buggy-login
docker volume ls --format '{{.Name}}' | grep '^flowcheck_prod_'
```

Expected: HTTPS `200`, HTTP redirects to `https://YOUR_DOMAIN/`, Nginx config succeeds, the backend resolves all three aliases and receives HTTP `200`, and both production volumes remain present. All five FlowCheck services are healthy/running. Verify no host port is published for any demo by inspecting each demo container's `.NetworkSettings.Ports`; each `80/tcp` value should be `null`.

Then use a browser at `https://YOUR_DOMAIN/`:

1. Create a throwaway FlowCheck account, sign in, refresh the page, and confirm the session remains active.
2. Run the independent scenario matrix against `http://flowcheck-healthy.test/` and `http://flowcheck-buggy.test/`, using only fictional credentials from `DUAL_LOGIN_DEMO_GUIDE.md`. Verify fixture differences from observed browser evidence; the guide's expected outcomes are not proof that FlowCheck detected them.
3. Confirm each final report cites observed browser text and has its screenshot/history entry. The first three are expected outcomes; the ambiguous mode should report a failure/inconclusive root cause based on its browser evidence and must not invent a server, database, or HTTP 503 cause.
4. Create a second throwaway account. While signed in as that account, try the first account's run-history and screenshot URLs; both must be inaccessible (404/unauthorized). Repeat the owner check from the first account.
5. Complete one real planner → Playwright → analyzer → reporter run to verify the existing 9Router provider connectivity. Do not inspect or change its configuration as part of this procedure.

Use dummy demo credentials only. Do not paste real website credentials into the demo or a FlowCheck run. Avoid terminal commands that print `.env`, provider configuration, request bodies, cookies, or full application logs.

### 12.5 Roll back application code and Compose configuration

If a post-deployment check fails, run on the VPS. This restores the source and Compose/Nginx configuration snapshot, retags and runs the previous backend/web images, and leaves `.env`, Docker volumes, screenshots, database, certificates, and other containers untouched:

```bash
set -euo pipefail
cd /opt/flowcheck
ls -1dt /opt/flowcheck/backups/release-*/source-config.tar.gz | head -n 5
```

Choose the backup created immediately before the failed release and substitute its timestamp below:

```bash
stamp=YYYYMMDD-HHMMSS
backup="/opt/flowcheck/backups/release-$stamp/source-config.tar.gz"
tar -xzf "$backup" -C /opt/flowcheck
docker image tag "flowcheck-rollback-backend:$stamp" flowcheck-prod-backend
docker image tag "flowcheck-rollback-web:$stamp" flowcheck-prod-web
docker image tag "flowcheck-rollback-demo-target:$stamp" flowcheck-prod-demo-target
demo_id=$(docker ps -aq --filter label=com.docker.compose.project=flowcheck-prod --filter label=com.docker.compose.service=demo-target)
if [ -n "$demo_id" ]; then docker rm -f "$demo_id"; fi
for service in healthy-login buggy-login; do
  demo_id=$(docker ps -aq --filter label=com.docker.compose.project=flowcheck-prod --filter "label=com.docker.compose.service=$service")
  if [ -n "$demo_id" ]; then docker rm -f "$demo_id"; fi
done
docker compose -f docker-compose.prod.yml config --quiet
docker compose -f docker-compose.prod.yml up -d --no-build backend web demo-target
docker compose -f docker-compose.prod.yml ps backend web demo-target
```

The pre-update images are tagged locally on the VPS and restored before Compose recreates those services. The rollback removes only the release's demo container by its exact Compose project/service labels; it does not use orphan cleanup or touch other stacks. Do not run `docker compose down`, `docker volume rm`, `docker volume prune`, or `docker system prune` during deployment or rollback. The data and screenshot volumes are named and are not part of the backup/extraction commands.
# Private AI access and quotas

Before enabling AI access for a production account, identify the owner through an authenticated production session (`GET /api/auth/me`) and configure only that stable numeric user ID in the private `.env` value `FLOWCHECK_AI_APPROVED_USER_IDS`. Empty or malformed configuration denies AI use, and new registrations remain denied. Do not store the approved ID in the public release.

Defaults are 30 outbound Provider attempts per user per UTC day, 10 browser executions per user per UTC day, one concurrent Provider call and one concurrent browser run per user, and two global slots for each. Provider retries each count as attempts. Reservations that fail before dispatch are released; a dispatched request is never refunded because it may have incurred an upstream charge. Provider and browser leases renew every 30 seconds and expire after 10 minutes following a process crash. Browser execution stops at 300 seconds and preserves completed scenario results and evidence. These limits are not a monetary hard cap; enforce billing limits with the Provider.
