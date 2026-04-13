FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update -o Acquire::Retries=5 -o Acquire::https::Timeout=30 \
    && apt-get install -y --fix-missing --no-install-recommends -o Acquire::Retries=5 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY alembic.ini /app/
COPY alembic /app/alembic

FROM base AS runtime

RUN pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["sh", "-lc", "PYTHONPATH=src python -m alembic upgrade head && PYTHONPATH=src python -m uvicorn meow_toilet.app.main:app --host \"${APP_HOST:-0.0.0.0}\" --port \"${APP_PORT:-8000}\""]

FROM base AS test

RUN pip install --no-cache-dir -e ".[dev]"

CMD ["sh", "-lc", "PYTHONPATH=src pytest -q"]
