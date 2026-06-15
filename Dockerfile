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
