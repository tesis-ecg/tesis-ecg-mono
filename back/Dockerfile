FROM python:3.12.13-slim-bookworm AS builder

RUN pip install --no-cache-dir uv==0.11.20

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups

FROM python:3.12.13-slim-bookworm

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

RUN groupadd --system holter && useradd --system --gid holter --home-dir /app holter \
    && chown -R holter:holter /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER holter

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live')"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
