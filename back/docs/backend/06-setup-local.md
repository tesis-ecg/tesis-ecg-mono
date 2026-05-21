# Backend — Setup local y Docker Compose

## Prerequisitos

- `uv` instalado: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker Desktop corriendo

## Levantar el stack completo

```bash
cd back/
cp .env.example .env       # completar variables
docker compose up --build  # FastAPI + PostgreSQL + MinIO
```

Servicios disponibles:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- MinIO console: http://localhost:9001 (usuario: minioadmin / pass: minioadmin)

## Docker Compose — servicios

### `db` (PostgreSQL 16-alpine)
```yaml
db:
  image: postgres:16-alpine
  environment:
    POSTGRES_DB: holter
    POSTGRES_USER: holter
    POSTGRES_PASSWORD: holter
  ports:
    - "5432:5432"
  volumes:
    - postgres_data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U holter"]
    interval: 5s
    retries: 5
```

### `minio` (S3 local)
```yaml
minio:
  image: minio/minio:latest
  command: server /data --console-address ":9001"
  environment:
    MINIO_ROOT_USER: minioadmin
    MINIO_ROOT_PASSWORD: minioadmin
  ports:
    - "9000:9000"
    - "9001:9001"
  volumes:
    - minio_data:/data
```

El bucket `ecg-batches` se crea automáticamente en el startup via script de init o con:
```bash
# Una sola vez, después de que MinIO esté corriendo:
docker run --rm --network host minio/mc \
  alias set local http://localhost:9000 minioadmin minioadmin && \
  mc mb local/ecg-batches
```

### `api` (FastAPI)
```yaml
api:
  build: .
  command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
  ports:
    - "8000:8000"
  volumes:
    - ./app:/app/app   # hot reload
  env_file: .env
  depends_on:
    db:
      condition: service_healthy
    minio:
      condition: service_started
```

## Variables de entorno (`.env.example`)

```bash
# Database
DATABASE_URL=postgresql+asyncpg://holter:holter@db:5432/holter

# S3 / MinIO
S3_BUCKET_NAME=ecg-batches
S3_ENDPOINT_URL=http://minio:9000      # vacío en producción
AWS_ACCESS_KEY_ID=minioadmin
AWS_SECRET_ACCESS_KEY=minioadmin
AWS_REGION=us-east-1

# Auth0
AUTH0_DOMAIN=<tu-tenant>.auth0.com
AUTH0_AUDIENCE=https://api.holter.app

# Seguridad
DEVICE_KEY_SALT_ROUNDS=12

# Entorno
ENVIRONMENT=development                # development | production
```

## Migraciones con Alembic

```bash
# Aplicar todas las migraciones (primera vez o después de git pull)
uv run alembic upgrade head

# Crear nueva migración tras cambiar modelos
uv run alembic revision --autogenerate -m "descripcion_del_cambio"

# Rollback un paso
uv run alembic downgrade -1

# Ver historial
uv run alembic history
```

Convención de nombres: `001_initial`, `002_add_alert_seen_at`, `003_...` — un cambio lógico por migración.

**Regla**: nunca editar archivos de `versions/` una vez que se aplicaron a alguna base de datos compartida.

## Validación de código (obligatorio antes de commit)

Correr siempre en este orden:

```bash
cd back/
uv run ruff check app/           # linting
uv run ruff format --check app/  # formato
uv run mypy app/                 # type checking
uv run pytest tests/             # tests
```

Configuración en `pyproject.toml`:
```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP"]   # pycodestyle, pyflakes, isort, pyupgrade

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true
```

`ruff format` reemplaza a `black` + `isort`. Si hay errores de mypy que no son del código propio (ej: librería sin stubs), usar `# type: ignore  # no stubs for neurokit2` con el motivo.

## Correr solo tests

```bash
cd back/
uv run pytest                          # todos los tests
uv run pytest tests/test_device_upload.py -v   # test específico
uv run pytest --cov=app                # con coverage
```

Los tests usan una base de datos de test separada y un contenedor MinIO efímero (ver `tests/conftest.py`).

## Comandos útiles de desarrollo

```bash
# Instalar deps (primera vez)
uv sync

# Agregar una dependencia
uv add neurokit2

# Agregar dependencia de dev
uv add --group dev pytest-cov

# Ejecutar un script
uv run python -m app.scripts.provision_device --serial HOLTER-001
```

## Dockerfile (producción)

Build multi-etapa:
1. Etapa `builder`: instala deps con `uv sync --frozen --no-dev`
2. Etapa final: copia el virtualenv, expone puerto 8000, corre `uvicorn` sin `--reload`

```dockerfile
FROM python:3.12-slim AS builder
RUN pip install uv
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
```
