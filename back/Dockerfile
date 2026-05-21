FROM python:3.12-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv .venv
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
