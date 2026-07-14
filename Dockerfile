# Production image for the S21 Content Factory API (merged catalog+generation+audit).
# Editable install so package-relative data files (templates, static, *.yaml configs)
# resolve from /app/src exactly as they do in local dev.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependency metadata first for layer caching, then the source tree.
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY static ./static
COPY alembic.ini ./

RUN pip install --upgrade pip && pip install -e .

# Runtime data dirs (mounted as a named volume in compose so content survives redeploys).
RUN mkdir -p /data/uploads /data/artifacts

EXPOSE 8000

# Apply migrations, then serve. Single-process asyncio (GENERATION_WORKER_ENABLED=false):
# durable STATE via checkpoints, recovery-on-startup marks interrupted runs.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn content_factory.api.main:app --host 0.0.0.0 --port 8000"]
