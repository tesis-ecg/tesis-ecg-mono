---
name: backend-skill
description: >
  Expert guidance for the FastAPI, SQLAlchemy 2.0, and PostgreSQL backend architecture in the TESIS monorepo.
  Use when writing, reviewing, or refactoring any code in back/ — routers, services,
  repositories, schemas, database models, migrations, and API dependencies.
license: MIT
metadata:
  author: tesis-ecg-mono
  version: "2.0.0"
---

# Backend Development Skill (FastAPI + SQLAlchemy)

You are an expert in FastAPI and Python backend development for the TESIS ECG Holter backend.

---

## 1. Core Principles

- Write concise, technical responses with accurate Python examples.
- Favor functional, declarative programming over class-based approaches.
- Prioritize modularization to eliminate code duplication.
- Use descriptive variable names with auxiliary verbs (`is_active`, `has_permission`, `can_process`).
- Use lowercase with underscores for files and directories (`modules/ecg_batches/ecg_batches_routes.py`).
- Follow RORO (Receive an Object, Return an Object) for service functions.
- Use `def` for pure synchronous functions and `async def` for I/O-bound operations.
- Use type hints for all function signatures.

---

## 2. SOLID Principles

Applied pragmatically to this stack — no textbook definitions, only concrete rules.

### Single Responsibility (SRP)
Each layer has exactly one reason to change:
- Route changes when the HTTP contract changes (path, status code, auth).
- Service changes when business rules change.
- Repository changes when the query or schema changes.
- ML module changes when the algorithm changes.

A service that also builds HTTP responses, or a route that contains `WHERE` clauses, violates SRP.

### Open/Closed (OCP)
Code should be open for extension, closed for modification. Most relevant in the ML pipeline:

```python
# arrhythmia.py — add new detectors without modifying existing ones
DETECTORS: list[Callable[[np.ndarray, int], list[ECGEventData]]] = [
    detect_tachycardia,
    detect_bradycardia,
    detect_pause,
    detect_pvc,
    # new detector: just append here, nothing else changes
]

def classify(signal: np.ndarray, sample_rate: int) -> list[ECGEventData]:
    return [event for detect in DETECTORS for event in detect(signal, sample_rate)]
```

Also applies to alert severity rules — add new thresholds without touching the alert creation flow.

### Liskov Substitution (LSP)
Any repository can be replaced with a test double without breaking the service. This is why repositories only expose typed methods and never leak SQLAlchemy internals to the service layer:

```python
# Service works the same whether it receives a real repo or a fake one in tests
async def get_patient(input_data: GetPatientInput, db: AsyncSession) -> GetPatientOutput:
    patient = await patient_repository.get_by_id(db, input_data.patient_id)
    ...
```

### Interface Segregation (ISP)
Keep RORO input schemas focused — only expose what the service actually needs. Don't pass the entire `Request` object or a god-object into a service:

```python
# Bad — service knows too much about HTTP context
async def receive_batch(request: Request, db: AsyncSession): ...

# Good — service only knows what it needs
async def receive_batch(input_data: ReceiveECGBatchInput, db: AsyncSession): ...
```

Also: separate `CreatePatientInput` from `UpdatePatientInput` from `GetPatientInput` — don't reuse a single fat schema across all operations.

### Dependency Inversion (DIP)
High-level modules (services) must not depend on low-level modules (concrete DB calls). Use FastAPI's `Depends` system:

```python
# Route depends on the injected service/repo, not on a concrete instantiation
@router.post("/devices/{device_id}/ecg-batch")
async def receive_ecg_batch(
    device: Device = Depends(device_auth),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks,
):
    output = await devices_service.receive_ecg_batch(
        ReceiveECGBatchInput(device=device, background_tasks=background_tasks),
        db=db,
    )
    return output
```

Never instantiate repositories directly inside a service function — always receive the `db` session via injection and pass it to repository calls.

---

## 3. Project Architecture

The backend lives in `back/app/` and is organized into domain-driven modules.

```
back/
├── pyproject.toml
├── uv.lock
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── alembic/
│   ├── env.py                          # async Alembic env
│   └── versions/                       # 001_initial.py, etc.
└── app/
    ├── main.py                         # app factory, middleware, router registration
    ├── core/
    │   ├── config.py                   # Pydantic Settings v2 — env validation at startup
    │   ├── logging.py                  # structured logging setup
    │   └── security.py                 # shared auth helpers
    ├── db/
    │   ├── session.py                  # async engine + AsyncSession dependency
    │   ├── base.py                     # declarative Base + TimestampMixin
    │   └── models/                     # SQLAlchemy 2.0 ORM models (one file per domain)
    │       ├── doctor.py / patient.py / device.py
    │       ├── ecg_batch.py / ecg_event.py / alert.py
    ├── modules/
    │   ├── devices/
    │   │   ├── devices_routes.py       # HTTP layer only
    │   │   ├── devices_service.py      # business logic (RORO)
    │   │   ├── devices_repository.py   # all SQLAlchemy calls
    │   │   ├── devices_schemas.py      # Pydantic request/response DTOs
    │   │   └── __init__.py
    │   ├── doctors/   (same structure)
    │   ├── patients/  (same structure)
    │   ├── ecg_batches/ (same structure)
    │   ├── alerts/    (same structure)
    │   └── admin/     (same structure)
    ├── dependencies/
    │   ├── auth_dependencies.py        # get_current_doctor, require_admin
    │   └── common_dependencies.py      # get_db
    ├── auth/
    │   ├── jwt.py                      # Auth0 RS256 JWT validation + JWKS cache
    │   └── api_key.py                  # Device X-API-Key header dependency
    └── ml/
        ├── pipeline.py                 # orchestrator: decompress → analyse → persist
        ├── decompression.py            # delta decode + base64 (numpy)
        ├── rpeak_detection.py          # neurokit2 R-peak detection
        ├── arrhythmia.py               # rule-based classifier (MVP)
        └── hrv.py                      # SDNN, RMSSD, mean_hr
```

**Rule:** Routes never execute raw DB queries. Services never depend on `Request`/`Response`. Repositories only perform DB calls. See `docs/backend/01-arquitectura.md` for full layer rules.

---

## 4. FastAPI and Python Standards

- Use type hints for all function signatures. Prefer Pydantic models over raw dictionaries.
- Declare route return type annotations clearly.
- Keep route functions thin — delegate all orchestration to service functions.
- Use FastAPI dependency injection (`Depends`) for auth, DB sessions, and shared concerns.
- Prefer `lifespan` context managers for startup/shutdown behavior (not deprecated `on_event`).
- Use middleware for request logging, error monitoring, and performance instrumentation.

---

## 5. Database and Persistence

- Use SQLAlchemy 2.0 async APIs with `asyncpg` as the PostgreSQL driver.
- Keep transaction boundaries in service functions, not route handlers.
- Use Alembic for migrations; never manually edit generated migration files after applying them.
- Name migrations with sequential prefix: `001_initial`, `002_add_alert_seen_at`, etc.

**ECG-specific normalization decisions:**

| Data | Storage | Reason |
|---|---|---|
| `ecg_batch` raw binary | S3 (key in DB) | Files are 2–3 MB; never queried field-by-field |
| `ecg_event.metadata` | JSONB column | Algorithm-specific details vary per event type; never queried individually |
| `doctor`, `patient`, `device`, `alert` | Normalized tables | Queried individually, filtered, paginated |

All tables have `deleted_at TIMESTAMP NULLABLE` for soft deletes. Repositories filter `WHERE deleted_at IS NULL` by default.

---

## 6. Error Handling Pattern

- Handle edge cases at function entry points.
- Use guard clauses and early returns for preconditions — happy path logic goes last.
- Avoid unnecessary `else` branches after `return`.
- Raise `HTTPException` for expected API errors.
- Add structured logs for failures; return user-friendly messages.
- Return 404 (not 403) when a doctor tries to access another doctor's patient — don't reveal existence.

Example (RORO + guard clauses):

```python
from fastapi import HTTPException
from app.modules.patients.patients_schemas import GetPatientInput, GetPatientOutput

async def get_patient(input_data: GetPatientInput, db: AsyncSession) -> GetPatientOutput:
    patient = await patient_repository.get_by_id(db, input_data.patient_id)
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if patient.doctor_id != input_data.current_doctor_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    return GetPatientOutput(patient=patient)
```

---

## 7. Auth Patterns

> **Not implemented yet.** All endpoints are currently open. Auth0 integration is planned for a future phase. See `docs/backend/04-autenticacion.md` for the full design.

**Planned security planes (future):**

| Client | Mechanism | Module |
|---|---|---|
| Holter device | `X-API-Key` header, bcrypt hash stored in DB | `app/auth/api_key.py` |
| Doctor (dashboard) | Auth0 JWT RS256, validated via JWKS | `app/auth/jwt.py` |

When implementing:
- Protect dashboard routes by default using `Depends(get_current_doctor)`.
- Public endpoints must be explicitly declared: `/health`, device upload.
- Keep JWT payload typed with a Pydantic schema.
- Doctors can only access their own patients — enforce in service layer, not route.
- Custom Auth0 claim for roles: `https://holter.app/roles` → `["DOCTOR"]` or `["ADMIN"]`.

---

## 8. Performance Guidelines

- Minimize blocking I/O — use `async def` for all DB and external API calls.
- Wrap `boto3` (sync) with `asyncio.to_thread()` to avoid blocking the event loop.
- Use pagination for all list endpoints (`page`, `per_page` params).
- JWKS is fetched once at startup and cached; re-fetch only on `JWKError` (key rotation).
- The device upload endpoint must respond in < 30 s — ML processing runs in `BackgroundTasks`.

---

## 9. Key Modules Reference

| Module | Responsibility |
|---|---|
| **auth** | Auth0 JWT validation, device API key check, role dependencies |
| **devices** | Device provisioning, ECG batch ingestion (`POST /devices/{id}/ecg-batch`) |
| **doctors** | Doctor profile, assigned patient list, unacknowledged alerts |
| **patients** | Patient CRUD, ECG batch history, per-patient alert history, ECG summary |
| **ecg_batches** | Batch metadata, event list, presigned S3 download URL |
| **alerts** | Alert list, acknowledge, seen |
| **admin** | Device provisioning (returns API key once), doctor creation via Auth0 |
| **ml** | ECG analysis pipeline: decompression, R-peak, HRV, arrhythmia classification |
| **db** | Async engine/session, model base, TimestampMixin |
| **core** | Config (pydantic-settings), structured logging, shared security helpers |

---

## 10. Testing and Validation (Mandatory)

After completing any task involving files in `back/`, run in this order:

```bash
cd back/
uv run ruff check app/          # linting
uv run ruff format --check app/ # formatting
uv run mypy app/                # type checking
uv run pytest tests/            # tests
```

Fix all errors and warnings before marking the task complete. No exceptions for type errors — use `# type: ignore` only with a comment explaining why.

---

## 11. Environment Variables

Validated at startup via `app/core/config.py` (Pydantic `BaseSettings`). App crashes with a clear message if any required var is missing.

| Var | Example | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://holter:holter@db:5432/holter` | SQLAlchemy async |
| `S3_BUCKET_NAME` | `ecg-batches` | |
| `S3_ENDPOINT_URL` | `http://minio:9000` | Empty in production (uses AWS default) |
| `AWS_ACCESS_KEY_ID` | `minioadmin` | MinIO in dev, real key in prod |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin` | |
| `AWS_REGION` | `us-east-1` | |
| `AUTH0_DOMAIN` | `<tenant>.auth0.com` | For JWKS endpoint — needed when auth is implemented |
| `AUTH0_AUDIENCE` | `https://api.holter.app` | Validated in JWT `aud` claim — needed when auth is implemented |
| `DEVICE_KEY_SALT_ROUNDS` | `12` | bcrypt rounds for API key hashing — needed when auth is implemented |
| `ENVIRONMENT` | `development` | Toggles logging level, Swagger visibility |

---

## 12. Dependencies

```toml
[project.dependencies]
fastapi = ">=0.115"
uvicorn = {extras = ["standard"]}
sqlalchemy = {extras = ["asyncio"], version = ">=2.0"}
asyncpg = ">=0.30"
alembic = ">=1.14"
pydantic = ">=2.0"
pydantic-settings = ">=2.0"
boto3 = ">=1.35"
python-jose = {extras = ["cryptography"]}
httpx = ">=0.27"
passlib = {extras = ["bcrypt"]}
numpy = ">=2.0"
scipy = ">=1.14"
neurokit2 = ">=0.2"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "pytest-cov", "ruff", "mypy", "faker"]
```
