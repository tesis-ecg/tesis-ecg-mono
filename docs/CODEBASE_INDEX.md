# Codebase Index

Agent-facing navigation map for the Holter Wearable ECG monorepo (Universidad Austral TFG). Optimized for choosing what to read first. Verify facts against source before high-impact edits.

## How To Use This Index

- This is a **navigation map**, not source of truth — read the cited files before editing or claiming behavior.
- Backend lives in `back/` (FastAPI + SQLAlchemy 2.0 async + PostgreSQL + S3). Frontend lives in `front/` (Vite + React 19 + TS).
- For backend conventions (router → service → repository layering, DTO usage), the `backend-skill` skill is authoritative; `back/docs/backend/` has prose docs (some stale — see Unknowns).
- For frontend conventions (shadcn/ui flow, tokens, feature-folder layout), see root `CLAUDE.md`.
- Edges marked **(inferred)** were not fully confirmed in code.

## Repository Overview

Monorepo, git root at repository top. Two product surfaces plus project documentation.

| Path | What it is | Stack |
|---|---|---|
| `back/` | Cloud API for the Holter device + medical dashboard | FastAPI, SQLAlchemy 2.0 (async), Alembic, PostgreSQL, S3/MinIO, Auth0 |
| `front/` | Medical dashboard (web) | Vite, React 19, TypeScript, Tailwind v4, React Router v7, TanStack Query, Axios, shadcn/ui |
| `info del proyecto/` | System/communication architecture docs (Spanish) | Markdown |
| `Entregables/` | Formal thesis deliverables | PDF/Markdown |
| `back/docs/backend/` | Backend prose docs (partly stale) | Markdown |

Key root files: `CLAUDE.md` / `AGENTS.md` (agent instructions), `Requerimientos.md`, `README.md`.

## Backend Graph

Entrypoint: `back/app/main.py` — creates `FastAPI(title="Holter ECG API")`, configures CORS from `settings.frontend_url`, registers 8 routers, exposes `GET /health`. Lifespan opens a DB connection on startup.

**Layering per module** (`back/app/modules/<name>/`): `*_routes.py` (HTTP) → `*_service.py` (business logic) → `*_repository.py` (DB queries); `*_schemas.py` holds Pydantic DTOs. `_base_schema.py` is the shared base (camelCase aliasing).

| Router (prefix) | Module | Status | Key endpoints |
|---|---|---|---|
| `/auth` | `modules/auth` | **Implemented** | `POST /login`, `POST /logout`, `GET /me`, `POST /register`, `POST /forgot-password` |
| `/patients` | `modules/patients` | **Implemented** | `GET ""`, `GET /{id}`, `GET /{id}/studies`, `GET /{id}/summary`, `GET /{id}/device`, `POST ""`, `PATCH /{id}`, `DELETE /{id}` |
| `/devices` | `modules/devices` | **Implemented** | `GET ""`, `POST ""`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`, `POST /{id}/assign`, `POST /{id}/unassign`, `POST /{id}/reassign`, `GET /{id}/health` |
| `/studies` | `modules/studies` | **Implemented** | `GET /{id}`, `GET /{id}/ecg` |
| `/doctors` | `modules/doctors` | **Stub** | `GET /` placeholder |
| `/ecg-batches` | `modules/ecg_batches` | **Stub** | `GET /` placeholder |
| `/alerts` | `modules/alerts` | **Stub** | `GET /` placeholder |
| `/admin` | `modules/admin` | **Stub** | `GET /` placeholder |

**Core infra** (`back/app/core/`): `config.py` (pydantic-settings; DB, S3/AWS, Auth0, JWT, `frontend_url`), `security.py` (JWT create/decode, HS256), `auth0_client.py` (ROPG login, Management API user creation + password reset, in-memory mgmt-token cache), `logging.py`.

**Dependencies** (`back/app/dependencies/`): `common_dependencies.py` (`get_db` async session), `auth_dependencies.py` (`get_current_user` — reads JWT from **httpOnly `session` cookie**, not Authorization header; `get_current_doctor` — wraps `get_current_user` and looks up the `doctor` row by `user_id`, raises 403 if none).

**ML** (`back/app/ml/`): `pipeline.py`, `decompression.py`, `rpeak_detection.py`, `arrhythmia.py`, `hrv.py` — **all placeholders** (docstring-only stubs). Future ECG analysis pipeline.

**Migrations** (`back/alembic/versions/`): `001_initial.py`, `002_auth.py`, `003_schema_alignment.py`, `004_drop_doctor_add_patient_user_id.py`.

## Frontend Graph

Entrypoint: `front/src/main.tsx` → `App.tsx` (routes). Feature-folder architecture under `src/features/<domain>/` with `api/`, `components/`, `hooks/`, `types.ts`, `mocks.ts`, `*Schema.ts`, `utils.ts`.

**Routing** (`src/App.tsx`):
- Public (no shell): `/login`, `/forgot-password`, `/403`
- Protected (`ProtectedRoute` → `AppShell`): `/` (Dashboard), `/patients`, `/patients/:id`, `/devices`, `/devices/:id`, `/studies`, `/studies/:id`, `/settings`, `/__dev/ecg-viewer`
- Role-gated (`RoleRoute allow={['investigador']}`): `/research`
- Fallback: `*` → NotFound

**Pages → feature ownership** (`src/pages/`):
| Page | Consumes feature |
|---|---|
| `Patients.tsx`, `PatientDetail.tsx` | `features/patients` |
| `Devices.tsx`, `DeviceDetail.tsx` | `features/devices` |
| `Studies.tsx`, `StudyDetail.tsx` | `features/studies` + `features/ecg` |
| `DevEcgViewer.tsx` | `features/ecg` (dev harness) |
| `Login.tsx`, `ForgotPassword.tsx` | `features/auth` |
| `Dashboard.tsx`, `Research.tsx`, `Settings.tsx` | placeholders / TBD |

**Features**:
- `features/auth` — `AuthProvider.tsx` + `AuthContext.ts` (session state), `api.ts`, `storage.ts`. Registers 401 handler into the axios client.
- `features/patients` — full CRUD + summary/studies/device hooks (`usePatients`, `usePatient`, `usePatientSummary`, `usePatientStudies`, `usePatientDevice`, create/update/delete).
- `features/devices` — Holter ABM + assign/unassign/reassign + health (`useHolters`, `useHolter`, `useHolterHealth`, `useAssignHolter`, etc.).
- `features/studies` — study metadata (`useStudy`, `StudyHeader`, `StudyBreadcrumb`).
- `features/ecg` — high-fidelity ECG viewer: `ECGViewer.tsx` (uPlot), `ECGMinimap.tsx`, `ECGZoomControls.tsx`, `ECGFullscreenDialog.tsx`, `useEcgSignal.ts`.

**Shared infra** (`src/lib/`): `api.ts` (axios instance, `withCredentials: true`, baseURL `VITE_API_URL`), `apiError.ts` (`mapAxiosError`, `ApiError` types), `apiRetry.ts`, `queryClient.ts` (TanStack Query), `time.ts`, `utils.ts` (`cn()`). UI primitives in `src/components/ui/` (shadcn). Layout in `src/layouts/` (`AppShell`, `Sidebar`, `Topbar`).

> `mocks.ts` files exist per feature domain but are **not imported by any hook or API module** — they are unused development-phase leftovers. All hooks call the real API via their `api/` module. No mock-flag mechanism exists.

## Cross-App Request Flows

**Auth/login**: `front` Login form → `POST /auth/login` (email+password) → `auth_service` calls Auth0 ROPG (`auth0_client.authenticate_user`) → backend issues own JWT (`security.create_access_token`) set as httpOnly `session` cookie → subsequent requests authenticated via `get_current_user` (cookie) → `GET /auth/me` returns `UserOut`. The FE never talks to Auth0 directly ("Auth0 mediado por backend").

**ECG viewing**: `front` StudyDetail (`/studies/:id`) → `GET /studies/:id` (metadata: patientName + deviceSerial denormalized) and `GET /studies/:id/ecg` → backend returns a **pre-signed S3 URL** (`StudyEcgOut.url`, 1h expiry) + `sampleRate`/`startTimestamp`/`durationMs`/`sampleCount` → FE fetches binary from S3 directly (`ecgApi.ts`), validates `byteLength === sampleCount × 4` (Float32), creates `Float32Array(buffer)`, and renders via uPlot in `ECGViewer`.

**Holter assignment**: `front` devices/patient UI → `POST /devices/{id}/assign|unassign|reassign` → `devices_service` maintains the bidirectional invariant `device.patient_id ↔ patient.assigned device`.

## Data Model Graph

Models in `back/app/db/models/`, base/mixins in `back/app/db/base.py`. All use SQLAlchemy 2.0 `Mapped[...]` typing. UUID PKs + `TimestampMixin` (`id`, `created_at`, `updated_at`, `deleted_at` — soft-delete field on all models).

Three-table auth/profile design: `user` (auth identity) ←1:1→ `doctor` (profile for medico users); `doctor` ←1:N→ `patient`; `patient` has an optional `user_id` FK for a future patient mobile app.

| Table | File | Key columns | Relationships |
|---|---|---|---|
| `user` | `user.py` | `auth0_id`, `email` (uniq), `full_name`, `role` (UserRole), `is_active`, `last_logout_at` | `doctor_profile` (0..1→Doctor), `patient_profile` (0..1→Patient via `patient.user_id`) |
| `doctor` | `doctor.py` | `user_id` (FK→user, NOT NULL, uniq), `specialty`, `license_number` | `user` (→User), `patients` (1→N→Patient) |
| `patient` | `patient.py` | `doctor_id` (FK→doctor, NOT NULL), `user_id` (FK→user, nullable, uniq — future mobile app), `medical_record_num` (uniq), `first_name`, `last_name`, `date_of_birth`, `dni`, `sex`, `study_status`, `last_data_received_at`, `phone`, `email`, `notes` | `doctor` (→Doctor), `user_account` (→User via `user_id`, nullable), `devices`, `alerts` |
| `device` | `device.py` | `serial_number` (uniq), `model`, `patient_id` (FK), `api_key_hash`, `firmware_version`, `last_seen_at`, `last_battery_pct`, `last_sd_free_mb`, `status` | `patient`, `ecg_batches` |
| `study` | `study.py` | `patient_id`, `device_id`, `started_at`, `ended_at`, `status`, `samples_count`, `events_count`, `ecg_s3_key`, `sample_rate`, `duration_ms` | `patient`, `device` |
| `ecg_batch` | `ecg_batch.py` | `device_id`, `received_at`, `batch_timestamp`, `duration_seconds`, `sample_rate`, `num_channels`, `compression_type`, `s3_key`, `processing_status` | `device`, `events` |
| `ecg_event` | `ecg_event.py` | `batch_id`, `event_type`, `severity`, `timestamp_in_recording`, `duration_seconds`, `confidence_score`, `event_metadata` (JSONB) | `batch`, `alerts` |
| `alert` | `alert.py` | `patient_id`, `event_id`, `severity`, `message`, `seen_at`, `acknowledged_at`, `acknowledged_by` (FK→doctor, nullable) | `patient`, `event`, `acknowledged_by_doctor` |
| `audit_event` | `audit_event.py` | `user_id`, `event_type`, `ip_address`, `event_metadata` (JSONB) | — |

**Enums**: `UserRole` (medico/paciente/admin/investigador/asistente) · `DeviceStatus` (available/assigned/maintenance/retired) · `PatientSex` (M/F/X) · `PatientStudyStatus` (active/completed/paused/none) · `StudyStatus` (in_progress/completed/cancelled/scheduled).

> Migration 003 migrated existing doctor rows into `user` preserving UUIDs. Migration 004 restructured `doctor` as a lean profile table (`user_id` FK, `specialty`, `license_number`) and re-pointed `patient.doctor_id` and `alert.acknowledged_by` from `user.id` → `doctor.id`.

## External Integrations Graph

- **Auth0** — identity provider, backend-mediated (ROPG + Management API). Client: `back/app/core/auth0_client.py`. Config: `auth0_*` settings.
- **PostgreSQL** — primary DB. Async engine in `back/app/db/session.py`. Local via docker-compose (`postgres:16-alpine`).
- **S3 / MinIO** — ECG binary blob storage (pre-signed URLs). Client built in `studies_service._get_s3_client()` (boto3, s3v4). Local via docker-compose (`minio/minio`). Config: `s3_*`/`aws_*` settings.
- **The Holter device (firmware)** — future producer of `ecg_batch` rows + S3 uploads; authenticated via `device.api_key_hash` **(inferred; ingestion endpoints are still stubs)**.

## Important Docs

- Root `CLAUDE.md` / `AGENTS.md` — project + agent conventions (authoritative for FE component flow, tokens, monorepo layout).
- `back/docs/backend/01-arquitectura.md` … `06-setup-local.md` — backend prose (modelos, endpoints, auth, ML pipeline, local setup). **Note staleness** (see Unknowns).
- `info del proyecto/` — system architecture (SIM/LTE-M comms, firmware, battery, security). Index at `info del proyecto/README.md`.
- `Requerimientos.md` — requirements.

## Suggested Reading Paths

- **Add/modify a backend endpoint**: `back/app/main.py` (router registration) → target `modules/<name>/<name>_routes.py` → `_service.py` → `_repository.py` → `_schemas.py`; use `get_current_user` for auth-only guards, `get_current_doctor` for doctor-scoped endpoints. Invoke `backend-skill`.
- **Work on the ECG viewer**: `front/src/pages/StudyDetail.tsx` → `features/ecg/components/ECGViewer.tsx` + `features/ecg/hooks/useEcgSignal.ts` → backend `modules/studies/studies_service.py` (`get_study_ecg`).
- **Auth changes**: `back/app/modules/auth/` + `core/auth0_client.py` + `core/security.py` + `dependencies/auth_dependencies.py`; FE `front/src/features/auth/` + `lib/api.ts`.
- **Data model / migration**: `back/app/db/models/` → add Alembic migration in `back/alembic/versions/`.
- **New frontend feature**: mirror an existing folder under `front/src/features/` (e.g. `patients`); follow shadcn flow in `CLAUDE.md`.

## Unknowns / Inferred Edges

- `back/docs/backend/04-autenticacion.md` states "sin autenticación implementada" — **confirmed stale** (auth is fully implemented via Auth0 ROPG + JWT httpOnly cookie). Treat all backend prose docs as potentially stale; source is authoritative.
- `modules/doctors`, `modules/ecg_batches`, `modules/alerts`, `modules/admin` are confirmed stub routers (`GET /` placeholder with `summary=` only) — no real logic yet.
- `app/ml/*` are confirmed docstring-only placeholders — no analysis pipeline runs yet.
- `device.api_key_hash` column exists in the `device` model, suggesting planned device authentication for firmware ECG ingestion; the ingestion route (`/ecg-batches`) is a stub — no wiring confirmed.
- `patient.user_id` is nullable — planted for future patient mobile app. All current patients have `user_id = NULL`.
- `patient.assigned device` field referenced in the Holter assignment flow is maintained via service-layer logic, not a DB-level column — the actual FK is `device.patient_id` **(inferred naming inconsistency, verify in patients_service.py before editing)**.
