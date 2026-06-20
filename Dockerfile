FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    cron \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Install Playwright browsers. The library is in pyproject deps; without
# the browser binary the BrowserFetcher fallback can't launch Chromium and
# the whole run dies inside the `async with BrowserFetcher() as browser:` line.
# Use --with-deps so the OS-level libs (libnss, libatk, etc) are also installed.
RUN uv run --no-sync playwright install chromium --with-deps

COPY encar_parser ./encar_parser
COPY alembic ./alembic
COPY alembic.ini ./
COPY models.yaml ./

# Stage 2: cron runner
FROM base AS cron
COPY docker/cron/entrypoint.sh /entrypoint.sh
COPY docker/cron/crontab /etc/cron.d/encar
RUN chmod 0644 /etc/cron.d/encar && crontab /etc/cron.d/encar
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]


# Stage 3: web viewer (FastAPI + uvicorn on port 8090).
# No Playwright / cron — just the API + image proxy.
FROM base AS web
ENV WEB_PORT=8090
EXPOSE 8090
# Two workers is plenty for a viewer serving ~tens of users; uvicorn forks
# per worker. Bump WEB_WORKERS via .env / compose if traffic grows.
CMD ["sh", "-c", "uv run --no-sync python -m uvicorn encar_parser.web.app:app --host 0.0.0.0 --port ${WEB_PORT:-8090} --workers ${WEB_WORKERS:-2}"]


# Stage 4: web+cron combo — used by `deploy/` single-container setups.
# Cron runs in foreground; uvicorn sits behind it. For the recommended
# multi-service layout use docker-compose (services: parser + web).
FROM cron AS cron-web
ENV WEB_PORT=8090
EXPOSE 8090
CMD ["sh", "-c", "uv run --no-sync python -m uvicorn encar_parser.web.app:app --host 0.0.0.0 --port ${WEB_PORT:-8090} --workers ${WEB_WORKERS:-1} & /entrypoint.sh"]
