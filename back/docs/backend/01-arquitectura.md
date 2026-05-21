# Backend — Arquitectura y estructura del proyecto

## Stack tecnológico

| Componente | Elección | Justificación |
|---|---|---|
| Package manager | `uv` | Rápido, determinístico, lockfile incluido |
| Framework | FastAPI 0.115+ | Async nativo, docs OpenAPI automáticas, Pydantic v2 integrado |
| ORM | SQLAlchemy 2.0 async (`asyncpg`) | API moderna async, evita hacks de greenlet |
| Migraciones | Alembic (async `env.py`) | Estándar para SQLAlchemy |
| Validación | Pydantic v2 | `model_config = ConfigDict(from_attributes=True)` para ORM → DTO |
| S3 | `boto3` via `asyncio.to_thread()` | Estable; local con MinIO |
| Background jobs | FastAPI `BackgroundTasks` (MVP) | Sin infraestructura extra; path de upgrade a Celery claro |
| Auth médicos | Auth0 JWT (RS256) via `python-jose` | Planificado — no implementado aún |
| Auth dispositivo | `X-API-Key` header custom | Planificado — no implementado aún |
| ML | `numpy`, `scipy`, `neurokit2` | neurokit2 cubre R-peak, HRV, limpieza de señal out of the box |
| Logging | `structlog` (o stdlib `logging` en JSON) | Logs estructurados compatibles con cualquier aggregator |
| Linting/formato | `ruff` | Reemplaza flake8 + isort + black en una sola herramienta |
| Type checking | `mypy` | Verificación estática estricta |
| Testing | `pytest`, `pytest-asyncio`, `httpx` | Stack estándar para FastAPI |

## Estructura de carpetas

```
back/
├── pyproject.toml               # uv project manifest + todas las deps
├── uv.lock
├── .env.example
├── Dockerfile
├── docker-compose.yml           # FastAPI + PostgreSQL + MinIO
├── alembic.ini
├── alembic/
│   ├── env.py                   # Alembic async
│   └── versions/                # 001_initial.py, 002_..., etc.
└── app/
    ├── main.py                  # App factory, lifespan, registro de routers, middleware
    ├── core/
    │   ├── config.py            # pydantic-settings BaseSettings
    │   ├── logging.py           # structured logging setup
    │   └── security.py          # helpers de auth compartidos
    ├── db/
    │   ├── session.py           # motor SQLAlchemy async + dependencia AsyncSession
    │   ├── base.py              # Base declarativa + TimestampMixin
    │   └── models/              # modelos ORM SQLAlchemy (un archivo por dominio)
    ├── modules/                 # un directorio por dominio
    │   ├── devices/
    │   │   ├── devices_routes.py
    │   │   ├── devices_service.py
    │   │   ├── devices_repository.py
    │   │   ├── devices_schemas.py
    │   │   └── __init__.py
    │   ├── doctors/             (misma estructura)
    │   ├── patients/            (misma estructura)
    │   ├── ecg_batches/         (misma estructura)
    │   ├── alerts/              (misma estructura)
    │   └── admin/               (misma estructura)
    ├── dependencies/
    │   └── common_dependencies.py # get_db
    │   # auth_dependencies.py — a agregar cuando se implemente auth
    ├── auth/                    # a implementar en fase futura
    │   ├── jwt.py               # Auth0 RS256 JWT validation + JWKS cache
    │   └── api_key.py           # Device X-API-Key header dependency
    └── ml/                      # Pipeline de análisis ECG
        ├── pipeline.py
        ├── decompression.py
        ├── rpeak_detection.py
        ├── arrhythmia.py
        └── hrv.py
```

## Regla de capas

```
route → service → repository
```

- Los routes manejan HTTP (request parsing, response status, dependencias). Nada más.
- Los services contienen toda la lógica de negocio. Siguen el patrón **RORO** (ver abajo). Nunca tocan `Request`/`Response`.
- Los repositories contienen **todas** las queries SQLAlchemy. Sin lógica de negocio.
- Los boundaries de transacción van en los services, no en los routes.

### Patrón RORO (Receive an Object, Return an Object)

Todas las funciones de service reciben y retornan un objeto tipado:

```python
# devices_schemas.py
class ReceiveECGBatchInput(BaseModel):
    device: Device
    payload: ECGBatchPayload
    background_tasks: BackgroundTasks

class ReceiveECGBatchOutput(BaseModel):
    batch_id: UUID
    status: str
    chunks_received: int

# devices_service.py
async def receive_ecg_batch(
    input_data: ReceiveECGBatchInput, db: AsyncSession
) -> ReceiveECGBatchOutput:
    ...
```

Ventaja: los services son fáciles de testear — se pasa el input, se verifica el output, sin mock de Request.

### Patrón de error handling (guard clauses)

```python
async def get_patient(input_data: GetPatientInput, db: AsyncSession) -> GetPatientOutput:
    patient = await patient_repository.get_by_id(db, input_data.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if patient.doctor_id != input_data.current_doctor_id:
        raise HTTPException(status_code=404, detail="Patient not found")  # no revelar existencia
    return GetPatientOutput(patient=patient)
```

- Early returns para precondiciones.
- Happy path al final.
- Retornar 404 (no 403) cuando un médico intenta acceder al paciente de otro médico.

## Módulos principales

### `app/main.py`
- `lifespan` context manager: valida conexión DB, pre-carga JWKS de Auth0.
- Registra todos los routers con sus prefijos y tags.
- Middleware CORS (origins desde config) + middleware de logging de requests.
- Handler global para `IntegrityError` → HTTP 409 Conflict.

### `app/core/config.py`
Usa `pydantic-settings` `BaseSettings`. La app falla en startup con mensaje claro si falta alguna variable requerida.

Variables clave:
```
DATABASE_URL            postgresql+asyncpg://user:pass@db:5432/holter
S3_BUCKET_NAME          ecg-batches
S3_ENDPOINT_URL         http://minio:9000   # vacío en producción → usa AWS
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION              us-east-1
AUTH0_DOMAIN            <tenant>.auth0.com
AUTH0_AUDIENCE          https://api.holter.app
DEVICE_KEY_SALT_ROUNDS  12
ENVIRONMENT             development
```

### `app/core/logging.py`
Structured logging con `structlog` o el módulo estándar `logging` configurado en JSON:
- En `development`: salida legible por humanos.
- En `production`: JSON por línea (compatible con CloudWatch / cualquier log aggregator).
- Incluye: `request_id`, `duration_ms`, `status_code`, `path` en cada log de request.

### `app/db/session.py`
```python
engine = create_async_engine(settings.DATABASE_URL)
async_session = async_sessionmaker(engine, expire_on_commit=False)
```

### `app/dependencies/common_dependencies.py`
```python
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

### `app/dependencies/auth_dependencies.py` — a implementar en fase futura
- `get_current_doctor()` — valida JWT, busca Doctor por `auth0_id`, retorna ORM object.
- `require_admin()` — llama a `get_current_doctor()` y verifica rol ADMIN.
- `device_auth()` — valida API key contra tabla Device.
- Ver diseño completo en `04-autenticacion.md`.
