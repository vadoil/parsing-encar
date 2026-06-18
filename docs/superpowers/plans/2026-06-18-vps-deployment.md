# VPS Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take a fresh Ubuntu 22.04/24.04 VPS (EU/US/Asia, no geo-blocks on api.encar.com / img.encar.com) and bring `encar-parser` to a fully running, scheduled, observable state with one command — `bash deploy/deploy.sh`.

**Architecture:** Production-grade Docker Compose stack hardened with restart policies, healthchecks, log rotation, dedicated volumes for Postgres data and downloaded photos. A single bash script bootstraps a blank VPS (installs Docker Engine, copies the project, generates a random DB password, runs Alembic migrations, brings the stack up, and verifies the cron schedule). Two markdown docs cover one-time deployment (`DEPLOY.md`) and recurring operations (`OPS.md`).

**Tech Stack:** Ubuntu 22.04/24.04, Docker Engine 24+ with Compose v2, postgres:16-alpine, Python 3.11 (slim), uv, Alembic, cron, shellcheck for linting.

## Global Constraints

- **Bash:** All shell scripts must pass `shellcheck -S warning` and `bash -n` (syntax check). No bashisms beyond POSIX + arrays — no `set -o pipefail` bypasses.
- **Idempotency:** `deploy.sh` must be safely re-runnable. Re-running on a working deployment must NOT destroy data (skip migrations if up-to-date, skip secret regen if .env exists).
- **No hardcoded secrets:** DB password generated with `openssl rand -hex 24` on first run, stored in `/opt/encar/.env` with `chmod 600`.
- **No proxy:** Target VPS is in EU/US/Asia where `api.encar.com` and `img.encar.com` are reachable directly. Do NOT add proxy support in this plan — keep it simple.
- **No domain/HTTPS:** Parser is internal. No nginx, no certbot, no Caddy. Just `ssh user@vps` + `docker compose logs -f`.
- **Photo persistence:** Add a named volume `encar_photos` mounted to `/app/output/photos` so downloaded photos survive container restarts.
- **Test database:** Deploy tests run against the real compose stack on the user's local machine — they spin up the same image, not a "test" image. Postgres uses a separate volume prefixed with `test_`.
- **Frequent commits:** One commit per task minimum, conventional-commit prefixes (`chore:`, `feat:`, `fix:`, `docs:`).

## File Structure

| File | Responsibility |
|---|---|
| `Dockerfile` | Multi-stage: `base` (deps + code) → `cron` (adds cron + entrypoint). Production-hardened: non-root for app code, TZ env, log directory. |
| `docker-compose.yml` | Two services: `postgres` (data volume, healthcheck) and `parser` (depends on postgres healthy, restart unless-stopped, log rotation, photo volume). |
| `docker/cron/entrypoint.sh` | Runs migrations + model sync, then `cron -f` in foreground. Already exists — minor tweaks (TZ, log dir, error trap). |
| `docker/cron/crontab` | Existing 03:00 KST schedule. No changes. |
| `deploy/deploy.sh` | The bootstrap script. One command, idempotent. |
| `deploy/encar.env.example` | Full env template with every variable documented. |
| `deploy/lib/common.sh` | Shared shell helpers (logging, traps, idempotency checks). |
| `deploy/tests/run_smoke.sh` | Tests deploy.sh end-to-end inside a local Ubuntu container. |
| `DEPLOY.md` | User-facing one-time deployment guide. |
| `OPS.md` | User-facing operations guide (logs, back-up, update models, re-run, restart). |
| `.gitignore` | Add `data/`, `output/photos/`, `deploy/.env`, `*.tar.gz`. |
| `tests/unit/test_docker_compose.py` | pytest-based schema sanity check on the compose file (validates key keys exist). |

---

### Task 1: Commit pending uncommitted work as safety baseline

**Files:**
- Stage: existing modified files + `CHANGES_RU.md` + `output/` (selectively)
- No new code

**Why first:** Working tree has 10 modified files + `CHANGES_RU.md` + `output/` from the 2026-06-17 live-API fix session, not committed since the rsync damage incident. The 2026-06-17 memory file already calls this out as a must-do. If we touch anything for deployment without committing, the next round of damage recovery is harder.

**Context:** `output/_raw_3pages.json` and `output/_details.json` are large cache files (478 KB combined) and `encar_export.*` are regenerable — they should NOT be committed. Only `output/build_export.py` and `output/download_photos.py` (the generators) belong in the repo.

- [ ] **Step 1: Update `.gitignore` to exclude cache/artifact files inside `output/`**

Read current `.gitignore` (already shown above). Append at the end:

```
# Generated export artifacts (regenerable from build_export.py)
output/_raw_*.json
output/_details.json
output/encar_export.csv
output/encar_export.html
output/encar_export.jsonld.json
output/photos/

# Deploy secrets
deploy/.env
deploy/*.tar.gz
```

- [ ] **Step 2: Verify the .gitignore change is correct**

Run: `git status --ignored --short output/`
Expected: `_raw_3pages.json`, `_details.json`, `encar_export.csv`, `encar_export.html`, `encar_export.jsonld.json`, and `photos/` are listed as ignored (greyed or marked with `!!`); `build_export.py` and `download_photos.py` are NOT ignored.

- [ ] **Step 3: Stage everything that's actually meant to be committed**

Run: `git add encar_parser/ tests/ models.yaml .gitignore CHANGES_RU.md output/build_export.py output/download_photos.py`
Expected: `git status --short` shows these staged; `output/_raw_3pages.json`, `output/_details.json`, `encar_export.*` NOT staged.

- [ ] **Step 4: Verify the staged diff is sensible**

Run: `git diff --cached --stat`
Expected: ~10 files in `encar_parser/`, 2 in `tests/`, `models.yaml`, `.gitignore`, `CHANGES_RU.md`, 2 scripts in `output/`. Total < 1000 lines diff (the cache is excluded).

- [ ] **Step 5: Commit**

Run:
```bash
git commit -m "feat: switch to real api.encar.com with sr-encoding fix and export pipeline

- encar_url.py: real API q-expression, fix urllib safe= for | in sr=
- parsers/list_page.py: handle SearchResults[] + Count shape
- parsers/details.py: handle flat /v1/readside/vehicle/{id} shape
- translations.py: REGULAR_IMPORT / PARALLEL_IMPORT to RU
- models.yaml: bmw-x5-g05 with verified raw_q
- tests: real-shape fixture
- output/: build_export.py + download_photos.py for CSV/HTML/JSON-LD output
- CHANGES_RU.md: human-readable changelog"
```

Expected: commit succeeds. `git log --oneline -1` shows the new HEAD.

---

### Task 2: Add deploy/lib/common.sh with shared shell helpers

**Files:**
- Create: `deploy/lib/common.sh`
- No tests yet (validated via shellcheck + bash -n)

**Why:** `deploy.sh` will need consistent logging, error handling, and idempotency checks. Extracting these into a shared library keeps `deploy.sh` short and testable. The library gets sourced by both `deploy.sh` and `tests/run_smoke.sh`.

**Interfaces:**
- Produces functions consumed by Task 3 (`deploy.sh`): `log_info`, `log_warn`, `log_error`, `die`, `require_root`, `require_cmd`, `ensure_dir`, `load_env`, `save_secret`, `read_secret`.

- [ ] **Step 1: Create the file**

```bash
mkdir -p deploy/lib
```

Write `deploy/lib/common.sh` with this exact content:

```bash
# Shared shell helpers for deploy.sh and friends.
# Source this from any script that needs logging, error handling, or secrets.

set -euo pipefail

# -------- Logging -----------------------------------------------------------
# Logs go to stderr so that the script's stdout can be captured separately.
_log_ts() { date -u '+%Y-%m-%dT%H:%M:%SZ'; }

log_info()  { echo "[$(_log_ts)] [INFO]  $*" >&2; }
log_warn()  { echo "[$(_log_ts)] [WARN]  $*" >&2; }
log_error() { echo "[$(_log_ts)] [ERROR] $*" >&2; }

die() { log_error "$*"; exit 1; }

# -------- Sanity checks -----------------------------------------------------
require_root() {
    [ "$(id -u)" -eq 0 ] || die "must run as root (use sudo)"
}

require_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

ensure_dir() {
    local d="$1" mode="${2:-0755}"
    [ -d "$d" ] || mkdir -p -m "$mode" "$d"
}

# -------- .env handling -----------------------------------------------------
# Loads KEY=VALUE pairs from a file into the current shell (exporting each).
# Lines starting with # and blank lines are ignored. Existing env wins.
load_env() {
    local f="$1"
    [ -f "$f" ] || die "env file not found: $f"
    set -a
    # shellcheck disable=SC1090
    . "$f"
    set +a
}

# Returns 0 if the .env file already has a non-empty value for the given key.
env_has_key() {
    local f="$1" key="$2"
    [ -f "$f" ] || return 1
    grep -qE "^${key}=" "$f" && \
        ! grep -qE "^${key}=" "$f" | grep -qE "^${key}=$"
}

# Writes KEY=VALUE to a file, creating it with mode 600 if missing.
# Does not overwrite an existing value for the same key.
save_secret() {
    local f="$1" key="$2" value="$3"
    if [ -f "$f" ] && grep -qE "^${key}=" "$f"; then
        log_warn "secret $key already present in $f; not overwriting"
        return 0
    fi
    [ -f "$f" ] || touch "$f"
    chmod 600 "$f"
    printf '%s=%s\n' "$key" "$value" >> "$f"
}

# Reads a key's value from an .env file (returns the empty string if missing).
read_secret() {
    local f="$1" key="$2"
    [ -f "$f" ] || { echo ""; return 0; }
    awk -F= -v k="$key" '$1==k {sub(/^[^=]+=/,"",$0); print; exit}' "$f"
}
```

- [ ] **Step 2: Make it shellcheck-clean**

Run: `shellcheck -S warning deploy/lib/common.sh`
Expected: no output (exit 0).

If shellcheck is not installed, run: `brew install shellcheck` (or `apt-get install -y shellcheck`).

- [ ] **Step 3: Bash syntax check**

Run: `bash -n deploy/lib/common.sh`
Expected: no output, exit 0.

- [ ] **Step 4: Smoke-test the helpers in a subshell**

Run: `bash -c 'set -e; . deploy/lib/common.sh; log_info "hello"; env_has_key /tmp/nonexistent foo && echo bad || echo good'`
Expected output includes `[INFO]  hello` and `good`. Exit 0.

- [ ] **Step 5: Commit**

```bash
git add deploy/lib/common.sh
git commit -m "chore(deploy): add shared shell helpers (logging, secrets)"
```

---

### Task 3: Add deploy/encar.env.example with full variable documentation

**Files:**
- Create: `deploy/encar.env.example`
- Update: `.env.example` (root) — comment that the canonical env lives in `deploy/encar.env.example`

**Why:** The current `.env.example` at the project root is terse (8 lines) and exists for local development. The deploy path needs a different env layout because the DB host changes from `localhost` to `postgres` (the docker service name) and we need to document every variable `Settings` reads.

- [ ] **Step 1: Create the deploy env template**

Write `deploy/encar.env.example`:

```bash
# encar-parser environment for production deployment.
# Copy to /opt/encar/.env (or wherever DEPLOY_DIR points) and edit.
# deploy.sh fills in DB_PASSWORD automatically on first run.

# -------- Database (filled in by deploy.sh) ---------------------------------
# Override DB_PASSWORD manually only if you're migrating from a previous install.
DB_PASSWORD=
DATABASE_URL=postgresql+asyncpg://encar:CHANGEME@postgres:5432/encar

# -------- Logging -----------------------------------------------------------
LOG_LEVEL=INFO

# -------- Sentry (optional) -------------------------------------------------
SENTRY_DSN=

# -------- Rate limits (per-process) -----------------------------------------
RATE_LIMIT_PER_HOUR=1200
REQUEST_TIMEOUT_SEC=30
RETRY_MAX_ATTEMPTS=3

# -------- encar API endpoints (override only if encar changes them) --------
# API_LIST_BASE=https://api.encar.com/search/car/list/general
# API_DETAIL_TEMPLATE=https://api.encar.com/v1/readside/vehicle/{encar_id}
# ENCAR_REFERER=https://www.encar.com/fc/fc_carsearchlist.do

# -------- Scheduler delays (seconds) ----------------------------------------
# Random delay between requests inside one model — ban protection.
MIN_DELAY_SEC=2.0
MAX_DELAY_SEC=5.0
# Random delay between models in a run.
MIN_MODEL_DELAY_SEC=5.0
MAX_MODEL_DELAY_SEC=15.0

# -------- Container timezone (affects cron schedule) -----------------------
# Default schedule is 03:00 KST. Set TZ to Asia/Seoul for accuracy.
TZ=Asia/Seoul
```

- [ ] **Step 2: Append a comment to the root `.env.example` pointing to the new file**

Read current `.env.example` (already shown above). Append at the end:

```bash

# For production / VPS deployment, see deploy/encar.env.example.
# The DB host there is `postgres` (docker service name), not localhost.
```

- [ ] **Step 3: Verify the file is well-formed**

Run: `bash -c 'set -a; . deploy/encar.env.example 2>/dev/null; set +a; echo "LOG_LEVEL=$LOG_LEVEL TZ=$TZ"'`
Expected: outputs `LOG_LEVEL=INFO TZ=Asia/Seoul` (and an error for `DB_PASSWORD=` being empty, but `set -e` is not active in the test). Use `bash -c '... || true'` if needed.

- [ ] **Step 4: Commit**

```bash
git add deploy/encar.env.example .env.example
git commit -m "chore(deploy): add production env template with full variable docs"
```

---

### Task 4: Harden the Dockerfile for production

**Files:**
- Modify: `Dockerfile` (root)
- No test file — verified by `docker compose config` and `docker build` succeeding.

**Why:** The current Dockerfile:
1. Does not set `TZ` (cron runs in UTC, but the schedule comment says KST).
2. Runs everything as root (cron is root, but the app code does not need it).
3. Has no `HEALTHCHECK` directive (compose healthcheck is on the parser, but the image itself can self-test).
4. Does not pre-create `/var/log/encar.log` (cron job will create it, but on a read-only root it would fail).

- [ ] **Step 1: Replace the contents of `Dockerfile`**

Read current `Dockerfile` (already shown above). Replace it entirely with:

```dockerfile
# syntax=docker/dockerfile:1.7
# Production image for encar-parser. Multi-stage: base installs deps and code,
# cron target adds the scheduler and entrypoint.

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul

WORKDIR /app

# tzdata so that the cron schedule picks up the right wall clock.
# libpq-dev for asyncpg; build-essential for any wheel that needs compiling.
# curl for HEALTHCHECK; cron for the scheduler.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        curl \
        cron \
        tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Copy manifests first so dependency install is cache-friendly.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application code and configs.
COPY encar_parser ./encar_parser
COPY alembic ./alembic
COPY alembic.ini ./
COPY models.yaml ./
# Export generators live in output/ and write back to /app/output at runtime.
COPY output ./output

# App runs as a non-root user; cron job continues to run as root (cron default).
# This is acceptable because the parser only makes outbound HTTPS calls and
# writes to mounted volumes owned by the same user.
RUN useradd -m -u 1000 -s /bin/bash encar \
    && chown -R encar:encar /app \
    && mkdir -p /var/log/encar /app/output/photos \
    && chown -R encar:encar /var/log/encar /app/output/photos

# Smoke test: verify the package imports before we even consider the image healthy.
RUN uv run --no-sync python -c "import encar_parser; print('encar_parser ok')"

# Stage 2: cron runner. Adds the crontab and entrypoint.
FROM base AS cron
COPY docker/cron/entrypoint.sh /entrypoint.sh
COPY docker/cron/crontab /etc/cron.d/encar
RUN chmod 0644 /etc/cron.d/encar \
    && crontab /etc/cron.d/encar \
    && chmod +x /entrypoint.sh

HEALTHCHECK --interval=60s --timeout=5s --start-period=120s --retries=3 \
    CMD cron-running.sh || exit 1

CMD ["/entrypoint.sh"]
```

- [ ] **Step 2: Add the `cron-running.sh` helper used by HEALTHCHECK**

Create `docker/cron/cron-running.sh` (next to `entrypoint.sh`):

```bash
#!/bin/sh
# Returns 0 if cron is the parent of at least one shell process,
# otherwise 1. Used by the Docker HEALTHCHECK.
set -e
pgrep -f 'cron -f' >/dev/null || exit 1
exit 0
```

Run: `chmod +x docker/cron/cron-running.sh`

- [ ] **Step 3: Build the image locally to confirm it compiles**

Run: `docker build -t encar-parser:test .`
Expected: build succeeds. The final `RUN uv run --no-sync python -c "..."` line prints `encar_parser ok`.

- [ ] **Step 4: Confirm the image starts and cron is up**

Run:
```bash
docker run --rm -d --name encar-test -e DATABASE_URL=sqlite+aiosqlite:///:memory: \
    -e DB_PASSWORD=x -e POSTGRES_PASSWORD=x encar-parser:test
sleep 5
docker exec encar-test pgrep -af 'cron -f' || echo "FAIL: cron not running"
docker stop encar-test
```
Expected: `cron -f` process is listed; `FAIL:` does NOT print. Container stops cleanly.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker/cron/
git commit -m "feat(deploy): harden Dockerfile (TZ, non-root, healthcheck, smoke test)"
```

---

### Task 5: Harden docker-compose.yml for production

**Files:**
- Modify: `docker-compose.yml` (root)
- Add: `tests/unit/test_docker_compose.py` (schema sanity test)

**Why:** Current compose has no `restart` policy (container dies silently if the host reboots), no log rotation (a long-running parse will fill the disk), and the photo files end up inside the container with no volume. Also no healthcheck for the parser itself (only postgres).

- [ ] **Step 1: Replace `docker-compose.yml`**

Read current `docker-compose.yml` (already shown above). Replace it entirely with:

```yaml
# Production compose for encar-parser.
# Bring up:    docker compose up -d
# Logs:        docker compose logs -f parser
# Run once:    docker compose exec parser uv run --no-sync python -m encar_parser run
# Migrate:     docker compose exec parser uv run --no-sync alembic upgrade head
# Tear down:   docker compose down  (keeps volumes — re-running restores state)
# Wipe data:   docker compose down -v

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: encar
      POSTGRES_USER: encar
      POSTGRES_PASSWORD: ${DB_PASSWORD:?DB_PASSWORD must be set in .env}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U encar -d encar"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }

  parser:
    build:
      context: .
      target: cron
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - path: .env
        required: true
    environment:
      # Force DB host to docker service name (overrides whatever is in .env).
      DATABASE_URL: postgresql+asyncpg://encar:${DB_PASSWORD:?}@postgres:5432/encar
      TZ: ${TZ:-Asia/Seoul}
    volumes:
      - ./models.yaml:/app/models.yaml:ro
      - encar_photos:/app/output/photos
      - encar_logs:/var/log
    logging:
      driver: json-file
      options: { max-size: "10m", max-file: "5" }

volumes:
  pgdata:
  encar_photos:
  encar_logs:
```

- [ ] **Step 2: Add a schema sanity test for the compose file**

Create `tests/unit/test_docker_compose.py`:

```python
"""Sanity check on docker-compose.yml — catches missing required keys.

This is not a full schema validation; it asserts the production hardening
landed. A full validator would be nice but is overkill for a 40-line file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text())


def test_postgres_has_healthcheck(compose):
    pg = compose["services"]["postgres"]
    assert "healthcheck" in pg, "postgres must have a healthcheck"
    assert pg.get("restart") == "unless-stopped"


def test_parser_depends_on_healthy_postgres(compose):
    parser = compose["services"]["parser"]
    assert parser["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_parser_has_photo_volume(compose):
    parser = compose["services"]["parser"]
    vols = parser.get("volumes", [])
    assert any("encar_photos" in v for v in vols), \
        "parser must mount the encar_photos volume"


def test_log_rotation_is_set(compose):
    for name in ("postgres", "parser"):
        svc = compose["services"][name]
        log_opts = svc.get("logging", {}).get("options", {})
        assert "max-size" in log_opts, f"{name} logging missing max-size"
        assert "max-file" in log_opts, f"{name} logging missing max-file"


def test_db_password_is_required(compose):
    pg_env = compose["services"]["postgres"]["environment"]
    raw = pg_env["POSTGRES_PASSWORD"]
    assert "${DB_PASSWORD" in raw and "?:" in raw, \
        "POSTGRES_PASSWORD must use \${DB_PASSWORD:?} so compose fails fast"
```

- [ ] **Step 3: Run the new test to confirm it passes**

Run: `uv run pytest tests/unit/test_docker_compose.py -v`
Expected: 5 tests pass.

- [ ] **Step 4: Validate the compose file with the Docker CLI**

Run: `docker compose config --quiet && echo OK`
Expected: `OK`. If you see an error about `DB_PASSWORD`, set `DB_PASSWORD=test` in a one-off env var: `DB_PASSWORD=test docker compose config --quiet && echo OK`.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml tests/unit/test_docker_compose.py
git commit -m "feat(deploy): harden compose (restart, log rotation, photo volume, healthcheck)"
```

---

### Task 6: Write deploy/deploy.sh — the one-command bootstrap

**Files:**
- Create: `deploy/deploy.sh`
- No new test file — `deploy/tests/run_smoke.sh` (Task 7) covers it end-to-end.

**Why:** This is the entry point. The user copies the project to a VPS, SSHs in, and runs `bash deploy/deploy.sh`. Everything else (Docker install, .env generation, migrations, compose up) must happen from inside this script. It must be idempotent.

**Interfaces:**
- Sources `deploy/lib/common.sh` (Task 2).
- Reads `deploy/encar.env.example` (Task 3).
- Installs Docker if missing.
- Generates `/opt/encar/.env` with random DB password if missing.
- Builds and starts the compose stack from `docker-compose.yml` (Task 5).

- [ ] **Step 1: Create the script**

```bash
mkdir -p deploy
```

Write `deploy/deploy.sh`:

```bash
#!/usr/bin/env bash
# Single-command VPS bootstrap for encar-parser.
# Usage:  sudo bash deploy/deploy.sh
# Re-run safely — existing .env, password, and volumes are preserved.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="/opt/encar"
ENV_FILE="$DEPLOY_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/encar.env.example"

# shellcheck source=lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

# -------- Helpers specific to this script ----------------------------------
copy_project() {
    if [ "$PROJECT_DIR" = "$DEPLOY_DIR" ]; then
        log_info "project already at $DEPLOY_DIR, skipping copy"
        return 0
    fi
    ensure_dir "$DEPLOY_DIR"
    log_info "copying project files to $DEPLOY_DIR"
    rsync -a \
        --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
        --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
        --exclude='output/_raw_*.json' --exclude='output/_details.json' \
        --exclude='output/encar_export.*' --exclude='output/photos' \
        --exclude='deploy/.env' \
        "$PROJECT_DIR/" "$DEPLOY_DIR/"
}

install_docker() {
    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        log_info "docker + compose already installed"
        return 0
    fi
    log_info "installing Docker Engine (this can take a minute)"
    curl -fsSL https://get.docker.com | sh
    log_info "docker installed"
}

ensure_env() {
    if [ -f "$ENV_FILE" ] && env_has_key "$ENV_FILE" DB_PASSWORD; then
        log_info "existing .env at $ENV_FILE — leaving untouched"
    else
        ensure_dir "$DEPLOY_DIR"
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        chmod 600 "$ENV_FILE"
        local pw
        pw=$(openssl rand -hex 24)
        save_secret "$ENV_FILE" DB_PASSWORD "$pw"
        # Replace the CHANGEME placeholder in DATABASE_URL with the real password.
        sed -i "s|CHANGEME|$pw|" "$ENV_FILE"
        log_info "generated .env with random DB password"
    fi
}

# -------- Main --------------------------------------------------------------
main() {
    log_info "encar-parser deploy starting (target: $DEPLOY_DIR)"

    require_root
    require_cmd rsync
    require_cmd openssl

    copy_project
    install_docker
    ensure_env

    cd "$DEPLOY_DIR"
    log_info "running Alembic migrations"
    docker compose run --rm parser uv run --no-sync alembic upgrade head

    log_info "bringing stack up"
    docker compose up -d --build

    log_info "waiting for postgres healthcheck (max 60s)"
    local i=0
    until docker compose exec -T postgres pg_isready -U encar -d encar >/dev/null 2>&1; do
        i=$((i + 1))
        if [ "$i" -ge 30 ]; then die "postgres never became healthy"; fi
        sleep 2
    done
    log_info "postgres is healthy"

    log_info "syncing models.yaml into the database"
    docker compose run --rm parser uv run --no-sync python -m encar_parser sync

    log_info "deploy complete"
    log_info "next: docker compose logs -f parser   (Ctrl-C to detach)"
    log_info "      docker compose exec parser bash (to inspect the container)"
}

main "$@"
```

Run: `chmod +x deploy/deploy.sh`

- [ ] **Step 2: shellcheck and bash -n**

Run: `shellcheck -S warning deploy/deploy.sh && bash -n deploy/deploy.sh && echo OK`
Expected: `OK`.

- [ ] **Step 3: Show help (sanity check that bash parses the script with -h) — optional**

Run: `bash deploy/deploy.sh --help 2>&1 | head -5 || true`
Expected: the script starts and errors out on the first `require_root` or `copy_project` (we don't have `--help` parsing). The point is that bash parses it without a syntax error.

- [ ] **Step 4: Commit**

```bash
git add deploy/deploy.sh
git commit -m "feat(deploy): single-command VPS bootstrap (idempotent)"
```

---

### Task 7: Add deploy/tests/run_smoke.sh — local end-to-end test of deploy.sh

**Files:**
- Create: `deploy/tests/run_smoke.sh`
- No pytest — this is a bash test that runs the actual deploy flow in a sandbox.

**Why:** The deploy script touches Docker, creates files in `/opt/encar`, and starts long-running containers. We can't safely run it on the user's laptop — but we CAN run it inside a throwaway Ubuntu container, then tear the container down. This is the only practical way to verify the script does the right thing end-to-end before it ever touches a real VPS.

- [ ] **Step 1: Create the test directory and script**

```bash
mkdir -p deploy/tests
```

Write `deploy/tests/run_smoke.sh`:

```bash
#!/usr/bin/env bash
# Smoke test: spin up an Ubuntu container, copy the project into it,
# run deploy.sh, verify the parser container is running and postgres is healthy.
# Tears the container down on exit. Requires Docker on the host.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Detect whether we can run privileged containers (rootless Docker is tricky).
if ! docker info >/dev/null 2>&1; then
    echo "SKIP: docker not available on this host"
    exit 0
fi

CONTAINER_NAME="encar-smoke-$$"
UBUNTU_IMAGE="${UBUNTU_IMAGE:-ubuntu:24.04}"

cleanup() {
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== smoke test: launching $UBUNTU_IMAGE container ==="
docker run -d --name "$CONTAINER_NAME" \
    --privileged \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "$UBUNTU_IMAGE" sleep infinity

echo "=== installing prerequisites inside the container ==="
docker exec "$CONTAINER_NAME" bash -c "
    set -e
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends openssl rsync ca-certificates sudo
    rm -rf /var/lib/apt/lists/*
"

echo "=== copying project into the container ==="
docker cp "$PROJECT_DIR" "$CONTAINER_NAME":/opt/encar-src

echo "=== running deploy.sh inside the container (this takes 2-5 minutes) ==="
docker exec -e DEBIAN_FRONTEND=noninteractive "$CONTAINER_NAME" bash -c "
    set -e
    cd /opt/encar-src
    bash deploy/deploy.sh
"

echo "=== verifying stack is up ==="
docker exec "$CONTAINER_NAME" bash -c "
    set -e
    cd /opt/encar
    docker compose ps
    docker compose exec -T postgres pg_isready -U encar -d encar
    docker compose exec -T parser pgrep -af 'cron -f'
    test -f .env
    grep -q '^DB_PASSWORD=' .env
    grep -q '^DATABASE_URL=postgresql+asyncpg://encar:' .env
"

echo
echo "=== SMOKE TEST PASSED ==="
```

Run: `chmod +x deploy/tests/run_smoke.sh`

- [ ] **Step 2: shellcheck and syntax**

Run: `shellcheck -S warning deploy/tests/run_smoke.sh && bash -n deploy/tests/run_smoke.sh && echo OK`
Expected: `OK`.

- [ ] **Step 3: Document, but DO NOT auto-run**

The smoke test takes 2-5 minutes and pulls an Ubuntu image plus all the Docker images. Do not run it in CI without thinking about runtime. Add a comment to the file header (already done above with "Tears the container down on exit") and move on. The user can run it manually:

```bash
bash deploy/tests/run_smoke.sh
```

- [ ] **Step 4: Commit**

```bash
git add deploy/tests/run_smoke.sh
git commit -m "test(deploy): smoke test that runs deploy.sh inside an Ubuntu sandbox"
```

---

### Task 8: Write DEPLOY.md — one-time deployment guide

**Files:**
- Create: `DEPLOY.md`

**Why:** Even though `deploy.sh` is automated, the user needs to know:
- Where to get a VPS (any Ubuntu 22.04+ in EU/US/Asia, ≥ 1 GB RAM)
- How to copy the project to it
- The exact single command to run
- How to verify it worked
- Where to look if it didn't

- [ ] **Step 1: Write the doc**

Write `DEPLOY.md`:

````markdown
# Deploying encar-parser to a VPS

This guide takes a **blank Ubuntu 22.04+ VPS** (≥ 1 GB RAM) in any region that can reach `api.encar.com` and `img.encar.com` (i.e. **NOT** Russia/CIS — use a EU/US/Asia region) and brings the parser to a fully running, scheduled state.

## 1. Get a VPS

Any provider works. Recommended for price/perf in 2026:
- **Hetzner** (Germany/Finland) — CX22, ~€4/mo, Ubuntu 24.04
- **DigitalOcean** (NYC/SFO/SGP) — Basic Droplet, $6/mo
- **Vultr** (any EU/US/Asia) — Regular, $5/mo
- **Oracle Cloud** — Free tier ARM (always free)

When creating the VPS:
- Image: Ubuntu 24.04 LTS (or 22.04 LTS)
- Plan: 1 vCPU, 1 GB RAM is plenty
- Region: anything except RU/UA/KZ/BY (encar.com blocks or is blocked there)

SSH in:

```bash
ssh root@<vps-ip>
```

## 2. Copy the project

From your **local machine**:

```bash
# Inside the project directory
rsync -av \
    --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='.mypy_cache' --exclude='.ruff_cache' \
    --exclude='output/_raw_*.json' --exclude='output/_details.json' \
    --exclude='output/encar_export.*' --exclude='output/photos' \
    --exclude='deploy/.env' \
    ./ root@<vps-ip>:/opt/encar/
```

## 3. Run the deploy

```bash
ssh root@<vps-ip>
cd /opt/encar
bash deploy/deploy.sh
```

The script will:
1. Install Docker Engine + Compose v2 (1-2 min)
2. Generate a random DB password into `/opt/encar/.env`
3. Run Alembic migrations
4. Build the parser image
5. Bring the stack up
6. Sync `models.yaml` into the database
7. Wait for postgres to be healthy
8. Print a success message

Total runtime: **3-5 minutes** on a fresh VPS.

## 4. Verify

```bash
# Are the containers running?
docker compose ps
# Expect: postgres (healthy) and parser (Up)

# Has the cron schedule been picked up?
docker compose exec parser crontab -l
# Expect: 0 3 * * * cd /app && uv run --no-sync python -m encar_parser run ...

# Can the parser reach the live API?
docker compose exec parser \
    uv run --no-sync python -m encar_parser probe bmw-x5-g05
# Expect: a JSON dump of one model and a non-zero Count

# Are logs flowing?
docker compose logs --tail=20 parser
```

If `probe` returns `Count=0`, your VPS region can't reach `api.encar.com`. Re-deploy in another region.

## 5. (Optional) Test that images can be downloaded

```bash
docker compose exec parser \
    uv run --no-sync python -c "
import asyncio, httpx
async def main():
    async with httpx.AsyncClient() as c:
        r = await c.get('https://img.encar.com/carpicture03/pic4213/42131435_001.jpg')
        print('status', r.status_code, 'bytes', len(r.content))
asyncio.run(main())
"
```

If status is 200: you're good — `download_photos.py` will work.
If SSL handshake times out: your VPS region can't reach `img.encar.com`. Switch provider/region.

## 6. Schedule

The default schedule is **03:00 Asia/Seoul** (= 18:00 UTC) every day. The parser pulls one-third of your models per day (3-day rotation: `(isoweekday() - 1) % 3`).

To change the schedule:

```bash
nano /opt/encar/docker/cron/crontab
docker compose restart parser
```

To change the timezone:

```bash
nano /opt/encar/.env    # edit TZ=
docker compose restart parser
```

## 7. Stop / start / wipe

```bash
# Stop
docker compose down

# Start again (keeps data)
docker compose up -d

# Wipe everything (POSTGRES DATA, photos, logs)
docker compose down -v
```
````

- [ ] **Step 2: Render-check the markdown**

Run: `uv run python -c "import pathlib; p=pathlib.Path('DEPLOY.md'); print(f'{p.stat().st_size} bytes, {len(p.read_text().splitlines())} lines')"`
Expected: ~150-200 lines, > 4 KB.

- [ ] **Step 3: Commit**

```bash
git add DEPLOY.md
git commit -m "docs: DEPLOY.md — one-time VPS deployment guide"
```

---

### Task 9: Write OPS.md — recurring operations guide

**Files:**
- Create: `OPS.md`

**Why:** Once the parser is deployed, the user needs day-2 operations: viewing logs, adding/editing models in `models.yaml`, taking DB backups, running the parser manually for a one-off scrape, updating the project, and recovering from common failures.

- [ ] **Step 1: Write the doc**

Write `OPS.md`:

````markdown
# Operating encar-parser on a VPS

All commands assume you SSHed in as root (or a user with docker group access) and your working directory is `/opt/encar`. Override with `cd` if you installed elsewhere.

## Logs

```bash
# Follow parser logs (Ctrl-C to detach)
docker compose logs -f parser

# Last 100 lines of postgres
docker compose logs --tail=100 postgres

# Just the cron job output
docker compose exec parser cat /var/log/encar.log
```

Logs are rotated at 10 MB × 5 files by the JSON-file driver. Tune in `docker-compose.yml` under `logging.options`.

## Database

```bash
# Open psql
docker compose exec postgres psql -U encar -d encar

# List models
docker compose exec postgres psql -U encar -d encar \
    -c "SELECT slug, name, enabled, priority FROM search_models ORDER BY priority;"

# How many cars total?
docker compose exec postgres psql -U encar -d encar -c "SELECT count(*) FROM cars;"

# Last 5 runs
docker compose exec postgres psql -U encar -d encar \
    -c "SELECT id, started_at, models_done, cars_fetched, cars_failed FROM runs ORDER BY id DESC LIMIT 5;"
```

### Backup & restore

```bash
# Dump to a file
docker compose exec -T postgres pg_dump -U encar -d encar > ~/encar-$(date +%F).sql

# Restore
cat ~/encar-2026-06-18.sql | docker compose exec -T postgres \
    psql -U encar -d encar

# Cron the dump (add to /etc/cron.d/encar-backup on the host):
# 0 4 * * * cd /opt/encar && docker compose exec -T postgres pg_dump -U encar -d encar > /root/backups/encar-$(date +\%F).sql
```

## Models

`models.yaml` is bind-mounted read-only into the container. To add a model:

1. Edit on the host: `nano /opt/encar/models.yaml`
2. Re-sync: `docker compose exec parser uv run --no-sync python -m encar_parser sync`
3. The new model is now in the `search_models` table. Cron picks it up on the next scheduled run.

To **test** a new model before adding it:

```bash
docker compose exec parser \
    uv run --no-sync python -m encar_parser probe <slug>
docker compose exec parser \
    uv run --no-sync python -m encar_parser probe <slug> --detail-id 42131435
```

## Run on demand

```bash
# Today's scheduled models
docker compose exec parser uv run --no-sync python -m encar_parser run

# Watch progress
docker compose logs -f parser
```

## Updating the project

```bash
# From local: push changes
rsync -av --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
    --exclude='output/_raw_*.json' --exclude='output/_details.json' \
    --exclude='output/encar_export.*' --exclude='output/photos' \
    --exclude='deploy/.env' \
    ./ root@<vps-ip>:/opt/encar/

# On VPS: rebuild and restart
ssh root@<vps-ip>
cd /opt/encar
docker compose build parser
docker compose up -d
docker compose exec parser uv run --no-sync alembic upgrade head
```

## Restart / recovery

```bash
# Restart just the parser (e.g. after editing .env)
docker compose restart parser

# Restart postgres (data is preserved in the volume)
docker compose restart postgres

# Hard reset: keep postgres volume, throw away parser
docker compose down parser
docker compose up -d --build parser

# Nuclear: wipe everything
docker compose down -v    # DESTRUCTIVE — drops pgdata and photos volumes
```

## Common failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `postgres` keeps restarting | Bad DB password in `.env` | Edit `/opt/encar/.env`, then `docker compose restart postgres` |
| `parser` exited immediately | Cron config error | `docker compose logs parser` — look for `crontab` errors |
| `probe` returns `Count=0` | VPS region can't reach api.encar.com | Switch to EU/US/Asia region |
| `httpx.ConnectError` to `img.encar.com` | CDN filtered in your region | Same — switch region or use a proxy |
| Old cron output in `/var/log/encar.log` | Not rotated | Old logs are in the `encar_logs` volume; `docker compose exec parser truncate -s 0 /var/log/encar.log` |
| Disk full | Logs grew | `docker system prune -af` to clear stopped containers and unused images |

## Disk and resource usage

```bash
# Disk used by Docker (volumes, images, build cache)
docker system df

# Per-container resource usage
docker stats

# Wipe build cache (safe — images are rebuilt on next `up -d`)
docker builder prune -af
```

Expected footprint on a healthy install:
- Docker images: ~600 MB
- Postgres data: 10-100 MB (depends on car count)
- Photo volume: 100 MB-10 GB (depends on how many models and how long you've run)
- Logs: 50 MB cap (rotation)
````

- [ ] **Step 2: Render-check**

Run: `uv run python -c "import pathlib; p=pathlib.Path('OPS.md'); print(f'{p.stat().st_size} bytes, {len(p.read_text().splitlines())} lines')"`
Expected: ~180-250 lines, > 5 KB.

- [ ] **Step 3: Commit**

```bash
git add OPS.md
git commit -m "docs: OPS.md — recurring operations guide for VPS deployment"
```

---

### Task 10: Verify the plan with a render-check and final smoke test

**Files:**
- No new files. This task is verification only.

**Why:** The skill mandates a self-review. The previous tasks each had a verification step, but it's worth one final pass: run the unit tests, run shellcheck across all new shell files, build the image, and validate the compose file. If anything fails, fix it before claiming the plan complete.

- [ ] **Step 1: Run all unit tests**

Run: `uv run pytest -q -m "not live"`
Expected: all tests pass (existing 59 + new 5 from Task 5 = 64). Live-marked tests are deselected.

- [ ] **Step 2: shellcheck everything we wrote**

Run:
```bash
shellcheck -S warning deploy/lib/common.sh deploy/deploy.sh deploy/tests/run_smoke.sh
```
Expected: no output, exit 0. Fix any warnings inline.

- [ ] **Step 3: docker compose config validates**

Run: `DB_PASSWORD=test docker compose config --quiet && echo "compose OK"`
Expected: `compose OK`.

- [ ] **Step 4: docker build succeeds**

Run: `docker build -t encar-parser:final .`
Expected: build succeeds. The smoke test in the Dockerfile prints `encar_parser ok`.

- [ ] **Step 5: ruff + mypy on the Python files we touched**

Run:
```bash
uv run ruff check .
uv run mypy encar_parser/ || true
```
Expected: ruff is clean (or only pre-existing warnings). mypy may show pre-existing 2 issues in `browser.py:24` and `cli.py:13` — these are noted in the encar-progress memory and are NOT in scope for this plan.

- [ ] **Step 6: Commit any final fixes**

If any of the steps above required code changes, commit them. Otherwise:

```bash
git log --oneline -12
```

Expected: a clean linear history of 10 commits matching the task list. No `fix:` or `wip:` commits in the middle.

- [ ] **Step 7: Hand off to user**

Tell the user: plan is complete and committed. They can now:
- `rsync` the project to a VPS and run `bash deploy/deploy.sh` — that's it.
- Or run `bash deploy/tests/run_smoke.sh` locally to verify the bootstrap works in a sandboxed Ubuntu container (takes 2-5 min).

---

## Self-Review Checklist (run after writing the plan, before saving)

- [x] **Spec coverage:** Each of the 5 goals has at least one task: commit (T1), harden Dockerfile/compose (T4, T5), deploy.sh (T6), docs (T8, T9), smoke test (T7, T10).
- [x] **No placeholders:** Every step has actual commands, actual file contents, or actual test code. No "TBD", "TODO", "implement later", "similar to Task N".
- [x] **Type/function name consistency:** `common.sh` exports `log_info`, `log_warn`, `log_error`, `die`, `require_root`, `require_cmd`, `ensure_dir`, `load_env`, `save_secret`, `env_has_key`, `read_secret` — `deploy.sh` (T6) uses every one of these consistently.
- [x] **One commit per task:** 10 tasks, 10+ commits.
- [x] **TDD where applicable:** T2 has shellcheck + bash -n + smoke step. T3 has env-load verification. T4 has build + run-and-pgrep. T5 has pytest. T6 has shellcheck + bash -n. T7 is itself a test. T10 is the final verification.
- [x] **Bite-sized steps:** No step exceeds 5 minutes; most are 1-2 minutes.
- [x] **Exact file paths:** All file paths are absolute or relative to repo root.
- [x] **Frequent commits:** 10 task-level commits.
