# Holter ECG — Tesis

Dispositivo wearable tipo Holter ECG integrado en chaleco textil con electrodos secos, orientado a monitoreo cardíaco preventivo continuo.

> Documentación de arquitectura completa en `info del proyecto/` · Documentación técnica del backend en `docs/backend/`

---

## Repositorio

| Directorio | Contenido |
|---|---|
| `front/` | Dashboard médico (Vite + React 19 + TypeScript + Tailwind v4) |
| `back/` | API backend (FastAPI + PostgreSQL + S3) |
| `docs/` | Documentación técnica de implementación |
| `info del proyecto/` | Documentación de arquitectura del sistema |

---

## Backend (`back/`)

### Prerequisitos

- [uv](https://astral.sh/uv) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- [Docker Desktop](https://www.docker.com/products/docker-desktop/)

### Primer uso

```bash
cd back/
cp .env.example .env
uv sync                      # instala dependencias y crea .venv
```

### Levantar stack completo

```bash
cd back/
docker compose up --build
```

| Servicio | URL |
|---|---|
| API | http://localhost:8000 |
| Swagger UI | http://localhost:8000/docs |
| MinIO console (S3 local) | http://localhost:9001 — `minioadmin` / `minioadmin` |
| PostgreSQL | `localhost:5432` — `holter` / `holter` |

### Levantar solo la base de datos y MinIO (dev sin Docker para la API)

```bash
cd back/
docker compose up db minio
uv run uvicorn app.main:app --reload
```

### Migraciones

```bash
cd back/
uv run alembic upgrade head                              # aplicar todas las migraciones
uv run alembic revision --autogenerate -m "descripcion" # crear nueva migración
uv run alembic downgrade -1                              # rollback un paso
uv run alembic history                                   # ver historial
```

### Mental model:

1. Edit ORM models (like editing Prisma schema).
2. Generate migration diff with Alembic autogenerate.
3. Review migration file.
4. Apply with alembic upgrade head.

### Formato, linting y tipos

```bash
cd back/
uv run ruff format app/          # formatear
uv run ruff check app/           # lint
uv run ruff check --fix app/     # lint con auto-fix
uv run mypy app/                 # type checking
```

### Tests

```bash
cd back/
uv run pytest                        # todos los tests
uv run pytest tests/test_foo.py -v   # test específico
uv run pytest --cov=app              # con coverage
```

### Agregar dependencias

```bash
cd back/
uv add <paquete>              # dependencia de producción
uv add --group dev <paquete>  # dependencia de desarrollo
```

---

## Frontend (`front/`)

```bash
cd front/
npm install
npm run dev        # dev server → http://localhost:5173
npm run build      # build de producción
npm run lint       # ESLint
npm run format     # Prettier
```

---

## Git hooks

Los hooks de pre-commit están configurados con Husky y se instalan automáticamente con `npm install` desde la raíz del monorepo. Ejecutan automáticamente:

- **Frontend**: Prettier + ESLint sobre archivos `.ts`/`.tsx` en stage
- **Backend**: `ruff format` + `ruff check --fix` sobre archivos `.py` en stage
