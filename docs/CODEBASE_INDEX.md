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

Entrypoint: `back/app/main.py` — creates `FastAPI(title="Holter ECG API")`, configures transitional CORS, Origin validation, stable error envelopes, request IDs/structured timing logs, registers 7 implemented routers, and exposes liveness/readiness. API docs are disabled in production; startup does not block on the database.

**Layering per module** (`back/app/modules/<name>/`): `*_routes.py` (HTTP) → `*_service.py` (business logic) → `*_repository.py` (DB queries); `*_schemas.py` holds Pydantic DTOs. `_base_schema.py` is the shared base (camelCase aliasing).

| Router (prefix) | Module | Status | Key endpoints |
|---|---|---|---|
| `/auth` | `modules/auth` | **Implemented** | `POST /login`, `POST /logout`, `GET /me`, deprecated `POST /register`, `POST /forgot-password` |
| `/patients` | `modules/patients` | **Implemented** | `GET ""`, `GET /{id}`, `GET /{id}/studies`, `GET /{id}/summary`, `GET /{id}/device`, `POST ""`, `PATCH /{id}`, `DELETE /{id}` |
| `/devices` | `modules/devices` | **Implemented** | `GET ""`, `POST ""`, `GET /{id}`, `PATCH /{id}`, `DELETE /{id}`, `POST /{id}/assign`, `POST /{id}/unassign`, `POST /{id}/reassign`, `GET /{id}/health` |
| `/studies` | `modules/studies` | **Implemented** | paginated `GET ""`, `GET /{id}`, `POST /{id}/complete`, `POST /{id}/cancel`, legacy `GET /{id}/ecg`, `GET /{id}/ecg/manifest` (signal objects + normalized event annotations) |
| `/alerts` | `modules/alerts` | **Implemented** | `GET ""` (paginado, filtros `acknowledged`/`severity`), `POST /{id}/acknowledge` |
| `/doctors` | `modules/doctors` | **Implemented** | admin-only `GET ""` for active doctor options |
| `/users` | `modules/users` | **Implemented** | admin-only list/create/email/reset/delete |
| `/dashboard` | `modules/dashboard` | **Implemented** | consolidated `GET /overview`; compatibility widget endpoints remain |
| `/ingest` | `modules/ingest` | **Implemented** | `POST /ecg-frames` — device-authenticated binary ingestion of Holter frames |

**Core infra** (`back/app/core/`): `config.py` (typed environment/config validation), `security.py` + `jwt_codec.py` (stdlib HS256 and cryptography-backed RS256, fixed algorithms, iss/aud/jti/sessionVersion), `auth0_client.py` (ROPG, Management API, JWKS cache), `request_context.py` (validated Vercel client IP), `logging.py`.

**Dependencies** (`back/app/dependencies/`): `common_dependencies.py` yields an async session without implicit commit. `device_dependencies.py` authenticates the Holter itself (`X-Device-Serial` + bearer compared against `device.api_key_hash` with `hmac.compare_digest`); it shares nothing with the user auth path — the device has no session and no cookie. `auth_dependencies.py` accepts migration cookies (`holter_session_v2` then legacy `session`), validates sessionVersion and active identity, and returns an explicit `RoleScope`: `ADMIN_GLOBAL` or `DOCTOR` with mandatory doctor ID.

**ML** (`back/app/ml/`): `decompression.py` is **implemented** — the Rice frame decoder, a verbatim port of the firmware's normative decoder (`../Holter-ECG-System/tools/holter_frame_decoder.py`). `pipeline.py`, `rpeak_detection.py`, `arrhythmia.py`, `hrv.py` remain docstring-only stubs.

**Migrations** (`back/alembic/versions/`): `4940f777d181_initial_schema.py` → `27e0b772f1dd_device_doctor_id.py` → `8c1f2a7d9b30_security_and_integrity.py` → `a1b70fd51903_ecg_ingest_pipeline.py` (ingest columns on `study`/`ecg_batch`, `DEVICE_API_KEY_ROTATED` audit type) → `b2c3d4e5f6a7_study_lifecycle_and_alerts.py` (`STUDY_COMPLETED`, `STUDY_CANCELLED`, `ALERT_ACKNOWLEDGED` audit types; no table changes). The last migration has preflight aborts for legacy roles, case-colliding emails and inconsistent assignments, then adds identity/session state, rate limits, ECG metadata, indexes and integrity constraints.

## Frontend Graph

Entrypoint: `front/src/main.tsx` → `App.tsx` (routes). Feature-folder architecture under `src/features/<domain>/` with `api/`, `components/`, `hooks/`, `types.ts`, `mocks.ts`, `*Schema.ts`, `utils.ts`.

**Routing** (`src/App.tsx`):
- Public (no shell): `/login`, `/forgot-password`, `/403`
- Protected (`ProtectedRoute` → `AppShell`): `/` (Dashboard), `/alerts`, `/patients`, `/patients/:id`, `/devices`, `/devices/:id`, `/studies`, `/studies/:id`; `/__dev/ecg-viewer` exists only in development builds.
- Role-gated (`RoleRoute allow={['admin']}`): `/users`, and `/__sim/vest` — the vest simulator, now **linked from the sidebar for admins** (access control is `RoleRoute`, not obscurity) and available in production.
- Fallback: `*` → NotFound

**Pages → feature ownership** (`src/pages/`):
| Page | Consumes feature |
|---|---|
| `Patients.tsx`, `PatientDetail.tsx` | `features/patients` |
| `Devices.tsx`, `DeviceDetail.tsx` | `features/devices` |
| `Studies.tsx`, `StudyDetail.tsx` | `features/studies` + `features/ecg` |
| `DevEcgViewer.tsx` | `features/ecg` (dev harness) |
| `Alerts.tsx` | `features/alerts` |
| `Login.tsx`, `ForgotPassword.tsx` | `features/auth` |
| `Dashboard.tsx` | consolidated `features/dashboard` overview |

**Features**:
- `features/auth` — memory-only `AuthProvider.tsx` + `AuthContext.ts`, `api.ts`. The HttpOnly cookie and `/auth/me` are the only source of truth; logout/401 cancels and clears TanStack Query without recursive server logout.
- `features/patients` — full CRUD + summary/studies/device hooks (`usePatients`, `usePatient`, `usePatientSummary`, `usePatientStudies`, `usePatientDevice`, create/update/delete).
- `features/devices` — Holter ABM + assign/unassign/reassign + health (`useHolters`, `useHolter`, `useHolterHealth`, `useAssignHolter`, etc.).
- `features/studies` — study metadata **and lifecycle**: `useStudy`, `useStudies`, `useCompleteStudy`, `useCancelStudy`, `StudyHeader` (carries the finish/cancel actions), `CloseStudyDialog`, `StudyBreadcrumb`.
- `features/alerts` — clinical alert inbox: `useAlerts`, `usePendingAlertCount` (feeds the sidebar badge), `useAcknowledgeAlert`, `AlertSeverityBadge`, `labels.ts`.
- `features/ecg` — high-fidelity ECG viewer: `ECGViewer.tsx` (uPlot signal + severity-colored event bands), `ECGMinimap.tsx` (overview + accessible event markers), `ECGFindingsPanel.tsx`, fullscreen/zoom controls, and `useEcgSignal.ts` (polls every 60 s while the study is `in_progress`). Annotation labels, severity rules and viewport navigation live in `annotationMeta.ts`; canvas painting lives in `annotationPlugin.ts`.
- `features/vest-simulator` — Holter simulator: `codec/` (TypeScript port of the firmware's Rice encoder + a decoder used only by round-trip tests), `codec/batchBuilder.ts` (signal → frames → injected anomalies), `workers/vestWorker.ts` (one worker per vest), `hooks/useVestFleet.ts` (N concurrent vests), `storage.ts` (fleet persisted to `localStorage` under `holter:vest-fleet`), `components/`. **The plaintext device API key is returned only once by `POST /devices/{id}/api-key`**, so rotation applies to the vest immediately (outside the dialog's draft) and the fleet is persisted — otherwise a cancel or a reload left the vest holding a dead key and every ingest answered 401.

**Shared infra** (`src/lib/`): `api.ts` (axios, `withCredentials`, fixed `/api`), `apiError.ts` (stable backend envelope mapping), `queryClient.ts` (GET-only bounded retry), centralized `queryKeys.ts`, `time.ts`, `utils.ts`. `src/generated/openapi.ts` is generated from `back/openapi.json`; CI checks drift through `app.scripts.export_openapi`.

**Tests and CI**: backend tests live in `back/tests/` (Pytest, with a DB + `moto` S3 harness in `conftest.py`; `-m slow` covers a realistic 1 h batch and runs as a separate CI step). `back/tests/fixtures/frames_golden.bin` is produced by the **TypeScript** encoder and decoded by the Python decoder, which is what verifies the codec port across languages — regenerate it with `UPDATE_GOLDEN=1 npx vitest run src/features/vest-simulator/codec/goldenFrames.test.ts`; focused frontend security/contract tests live beside source as `*.test.ts` and run with Vitest. Root `.github/workflows/ci.yml` validates formatting, lint/type checks, coverage, OpenAPI drift, Alembic on PostgreSQL, dependency/security scans, Compose and Docker builds. Frontend chunk limits are enforced by `front/scripts/check-bundle-budget.mjs`.

> `mocks.ts` files exist per feature domain but are **not imported by any hook or API module** — they are unused development-phase leftovers. All hooks call the real API via their `api/` module. No mock-flag mechanism exists.

## Cross-App Request Flows

**Auth/login**: browser uses same-origin `/api` → Vite/Vercel proxy → `POST /auth/login` → PostgreSQL HMAC rate limit → Auth0 ROPG → verified Auth0 RS256 token → pre-provisioned active DB identity only → internal versioned JWT. During migration login emits `holter_session_v2` (`HttpOnly`, `Secure` outside dev/test, `SameSite=Lax`, `Path=/api`) plus legacy `session`; logout increments sessionVersion and removes both.

**User provisioning**: login never auto-provisions. Admin-only `/users` owns the pending → active/error Auth0–DB workflow; deprecated `/auth/register` delegates to it. `app.scripts.reconcile_identities` repairs pending/error states. Inconsistent identities cannot authenticate.

**ECG viewing**: StudyDetail prefers `GET /studies/{id}/ecg/manifest` (versioned little-endian raw metadata + SHA-256 + presigned raw/min-max levels, 10-minute expiry, plus normalized `annotations`). `studies_repository.list_ecg_events` associates modern events through `ecg_batch.study_id` and falls back to legacy `metadata.studyId`; the service clips sample/time coordinates to the recording and exposes lower-case kind/category/severity. The client selects a pyramid level capped at 20,000 points, validates size/checksum, overlays annotations in uPlot/minimap, and drives the findings panel from the same payload. A 404 falls back to deprecated `/ecg` with no annotations. S3 downloads remain direct and private/presigned.

**ECG ingestion (device → study)**: the vest POSTs `N × 256 B` Rice-compressed frames as `application/octet-stream` to `/ingest/ecg-frames`. The service validates each frame (magic → version → CRC-32), resolves `serial → device.patient_id → open in-progress study` (creating one if needed, and never joining a study that already holds a legacy `ecg_s3_key` blob), archives the accepted contiguous run to S3 and answers `202` with a go-back-N ACK delta. A `BackgroundTasks` job then decodes to float32 mV, writes one segment plus one bucket-16 envelope per batch, rebuilds the coarse pyramid levels from the envelopes, and derives `ecg_event`/`alert` rows. `/ingest` is exempt from the Origin check — it carries no cookie, so it has no CSRF surface, and the ESP32 co-processor sends no `Origin` header.

**Holter assignment**: `front` devices/patient UI → `POST /devices/{id}/assign|unassign|reassign` → `devices_service` maintains the bidirectional invariant `device.patient_id ↔ patient.assigned device`.

**Study lifecycle**: ingestion is the only creator (`IN_PROGRESS`). It ends two ways. Explicitly — `POST /studies/{id}/complete|cancel` → `studies_service._transition`, which locks the row `FOR UPDATE` (serialising against an in-flight batch), stamps `ended_at`/`duration_ms`, and re-syncs `patient.study_status`; a second close answers `409 STUDY_NOT_OPEN` rather than a silent 200. Implicitly — unassigning, reassigning or retiring the Holter, and deleting the patient, all call `studies_service.close_open_studies_for_device` / `close_open_studies_for_patient` **before** the `patient_id` is dropped, because afterwards there is no way to tell whose study it was. Moving the vest closes as `COMPLETED` (that is how a Holter normally ends); deleting the patient cancels.

## Data Model Graph

Models in `back/app/db/models/`, base/mixins in `back/app/db/base.py`. All use SQLAlchemy 2.0 `Mapped[...]` typing. UUID PKs + `TimestampMixin` (`id`, `created_at`, `updated_at`, `deleted_at` — soft-delete field on all models).

Three-table auth/profile design: `user` (auth identity) ←1:1→ `doctor` (profile for medico users); `doctor` ←1:N→ `patient`; `patient` has an optional `user_id` FK for a future patient mobile app.

| Table | File | Key columns | Relationships |
|---|---|---|---|
| `user` | `user.py` | `auth0_id`, normalized `email` (uniq), `role`, `is_active`, `identity_status`, `pending_email`, `session_version` | `doctor_profile` (0..1→Doctor), `patient_profile` (0..1→Patient via `patient.user_id`) |
| `doctor` | `doctor.py` | `user_id` (FK→user, NOT NULL, uniq), `specialty`, `license_number` | `user` (→User), `patients` (1→N→Patient) |
| `patient` | `patient.py` | `doctor_id` (FK→doctor, NOT NULL), `user_id` (FK→user, nullable, uniq — future mobile app), `medical_record_num` (uniq), `first_name`, `last_name`, `date_of_birth`, `dni`, `sex`, `study_status`, `last_data_received_at`, `phone`, `email`, `notes` | `doctor` (→Doctor), `user_account` (→User via `user_id`, nullable), `devices`, `alerts` |
| `device` | `device.py` | `serial_number`, `doctor_id`, `patient_id`, telemetry nullable, `status`; DB checks + one-active-device-per-patient partial unique index | `patient`, `ecg_batches` |
| `study` | `study.py` | IDs/times/counts plus `ecg_encoding`, byte length, SHA-256 and JSONB pyramid levels | `patient`, `device` |
| `auth_rate_limit` | `auth_rate_limit.py` | HMAC `key`, fixed `bucket_start`, attempts | — |
| `ecg_batch` | `ecg_batch.py` | `device_id`, nullable legacy-compatible `study_id`, timing/sample metadata, processing state, raw/frame S3 keys and ingest sequence fields | `device`, optional `study`, `events` |
| `ecg_event` | `ecg_event.py` | `batch_id`, `event_type`, `severity`, `timestamp_in_recording`, `duration_seconds`, `confidence_score`, `event_metadata` (JSONB) | `batch`, `alerts` |
| `alert` | `alert.py` | `patient_id`, `event_id`, `severity`, `message`, `seen_at`, `acknowledged_at`, `acknowledged_by` (FK→doctor, nullable) | `patient`, `event`, `acknowledged_by_doctor` |
| `audit_event` | `audit_event.py` | `user_id`, `event_type`, `ip_address`, `event_metadata` (JSONB) | — |

**Enums**: `UserRole` (medico/admin only) · `IdentityStatus` (pending/active/error) · `DeviceStatus` (available/assigned/maintenance/retired) · `PatientSex` (M/F/X) · `PatientStudyStatus` (active/completed/paused/none) · `StudyStatus` (in_progress/completed/cancelled/scheduled).

> Single autogenerated migration reflects the final schema. `doctor` is a lean profile table linked 1:1 to `user` via `user_id`. A `doctor` row is always created alongside the `user` row for any `medico` account.

## External Integrations Graph

- **Auth0** — identity provider, backend-mediated (ROPG + Management API). Client: `back/app/core/auth0_client.py`. Config: `auth0_*` settings.
- **PostgreSQL** — primary DB. Async engine in `back/app/db/session.py` uses timeouts and `NullPool` in preview/production. Local via root docker-compose (`postgres:16.10-alpine`).
- **S3 / MinIO** — ECG binary blob storage (pre-signed URLs). Client built in `studies_service._get_s3_client()` (boto3, s3v4). Local via docker-compose (`minio/minio`). Config: `s3_*`/`aws_*` settings.
- **Local ECG annotation showcase** — `python -m app.scripts.seed_ecg_showcase` creates/replaces only `SHOWCASE-ECG-ALERTS` and `showcases/ecg-alerts/` in development/test. It writes one deterministic 10-minute study, raw + pyramid objects, and six quality/clinical/patient events across all severities; default owner is `dev@tesis.com`, which must be an active doctor.
- **The Holter device (firmware)** — producer of `ecg_batch` rows + S3 objects through `POST /ingest/ecg-frames`, authenticated with `device.api_key_hash`. The frame format is normative and lives in the sibling repo `../Holter-ECG-System` (`INTEGRACION.md` §3-4). Admins mint credentials with `POST /devices/{id}/api-key`.

## Important Docs

- Root `CLAUDE.md` / `AGENTS.md` — project + agent conventions (authoritative for FE component flow, tokens, monorepo layout).
- `docs/adr/001-004` — same-origin API proxy, explicit access scopes, accepted Auth0 ROPG exception and versioned ECG manifest decisions.
- `docs/operations.md` — environment isolation, deploy/rollback, Auth0 controls, backup/restore, S3 lifecycle and secret rotation runbook.
- `back/docs/backend/01-arquitectura.md` … `06-setup-local.md` — backend prose (modelos, endpoints, auth, ML pipeline, local setup). **Note staleness** (see Unknowns).
- `info del proyecto/` — system architecture (WiFi comms + SoftAP provisioning, firmware, battery, security). Index at `info del proyecto/README.md`. Note: `08-sim-celular-descartado.md` documents the **rejected** LTE-M design — do not treat as current.
- `Requerimientos.md` — requirements.

## Suggested Reading Paths

- **Add/modify a backend endpoint**: `back/app/main.py` → target route → service → repository → schemas; use `get_current_user` for identity and `get_doctor_scope` for explicit admin/doctor scoping. Regenerate OpenAPI/types.
- **Work on the ECG viewer or findings**: `StudyDetail.tsx` → `features/ecg/{api,hooks,components,annotationMeta.ts,annotationPlugin.ts}` → `studies_service.get_study_ecg_manifest` / `studies_repository.list_ecg_events` → private S3 objects + `ecg_event` rows.
- **Auth changes**: `back/app/modules/auth/` + `core/auth0_client.py` + `core/security.py` + `dependencies/auth_dependencies.py`; FE `front/src/features/auth/` + `lib/api.ts`.
- **Data model / migration**: `back/app/db/models/` → add Alembic migration in `back/alembic/versions/`.
- **New frontend feature**: mirror an existing folder under `front/src/features/` (e.g. `patients`); follow shadcn flow in `CLAUDE.md`.

## Unknowns / Inferred Edges

- `back/docs/backend/04-autenticacion.md` states "sin autenticación implementada" — **confirmed stale** (auth is fully implemented via Auth0 ROPG + JWT httpOnly cookie). Treat all backend prose docs as potentially stale; source is authoritative.
- `modules/admin` and `modules/ecg_batches` are still source placeholders, intentionally not registered publicly — ingestion lives in `modules/ingest`, because the contract is "receive frames", not "CRUD batches". `modules/alerts` **is now implemented and registered**.
- `PatientStudyStatus.PAUSED` has no writer anywhere: nothing in the domain pauses a study. It is kept in the enum for existing rows but removed from the patients filter UI.
- `app/ml/*` are confirmed docstring-only placeholders — no analysis pipeline runs yet.
- `patient.user_id` is nullable — planted for future patient mobile app. All current patients have `user_id = NULL`.
- `patient.study_status` / `patient.last_data_received_at` are a **maintained cache**, not derived state: `ingest_service.ingest_frames` writes them on every accepted batch, and `studies_service` rewrites them on every close. The dashboard counts patients off these columns, so any new path that opens or closes a study must keep them in sync.
- `patient.assigned device` field referenced in the Holter assignment flow is maintained via service-layer logic, not a DB-level column — the actual FK is `device.patient_id` **(inferred naming inconsistency, verify in patients_service.py before editing)**.
