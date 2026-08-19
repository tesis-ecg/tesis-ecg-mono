# Operación segura del dashboard y API

Este runbook cubre la infraestructura de frontend, backend, PostgreSQL y S3. No incluye firmware ni hardware.

## Entornos y despliegue

- Development, Preview y Production deben usar proyectos, bases, buckets, credenciales Auth0 y secretos distintos. Nunca conectar un Preview a datos de Production.
- Configurar `BACKEND_ORIGIN` y `S3_PUBLIC_ORIGIN` en el proyecto Vercel del frontend. `/api` debe reescribir al backend antes del fallback SPA.
- Fijar frontend y backend en una región cercana a PostgreSQL/S3. En Preview/Production el backend usa `NullPool`; `DATABASE_URL` debe apuntar al pooler administrado si el proveedor lo ofrece.
- Desplegar primero migraciones compatibles, luego backend y por último frontend. Para la migración de sesión, conservar ambas cookies durante al menos un TTL completo antes de retirar la legacy y CORS con credenciales.

## Auth0 ROPG

ROPG es una excepción aceptada para este proyecto. En cada tenant hay que habilitar Brute-force Protection, Suspicious IP Throttling y Breached Password Detection. El backend envía `auth0-forwarded-for` solamente con una IP validada desde los headers de Vercel.

Las identidades se crean desde `/users`. Si Auth0 y PostgreSQL quedan desincronizados:

```bash
cd back
uv run python -m app.scripts.reconcile_identities
```

Un usuario `pending` o `error` permanece inactivo hasta completar la reconciliación.

## PostgreSQL

- Ejecutar un backup administrado y verificar su finalización antes de cada migración. Conservar snapshots diarios y PITR según la retención acordada.
- Ensayar restauración trimestralmente en un proyecto aislado: restaurar, ejecutar `alembic upgrade head`, verificar `/health/ready` y una matriz de autorización sin usar datos reales.
- Alertar por conexiones, almacenamiento, locks prolongados, consultas lentas y fallos de readiness. Revisar con `EXPLAIN (ANALYZE, BUFFERS)` los listados y dashboard al cambiar volumen o índices.

## S3

- Bucket privado, bloqueo de acceso público, cifrado SSE administrado, TLS y credenciales IAM exclusivas con permisos limitados al prefijo ECG.
- CORS sólo para los orígenes de frontend del entorno y métodos `GET`/`HEAD`; las URLs presignadas expiran en 10 minutos.
- La ingesta debe persistir tamaño y SHA-256 antes de publicar el manifest. Configurar lifecycle para abortar multipart incompletos y mover/eliminar objetos según la política clínica aprobada; no aplicar una retención destructiva sin esa aprobación.

## Secretos e incidentes

- Rotar trimestralmente y ante cualquier sospecha: credenciales DB/S3, secretos Auth0, `JWT_SECRET` y `AUTH_RATE_LIMIT_SECRET`. Hacer rotación coordinada porque cambiar `JWT_SECRET` invalida todas las sesiones.
- Los logs no deben contener cookies, tokens, emails, DNI ni URLs presignadas. Correlacionar incidentes mediante `X-Request-ID` y eventos de auditoría.
- Ante exposición: revocar credenciales, incrementar `sessionVersion` de usuarios afectados, bloquear identidades comprometidas, preservar auditoría y documentar alcance/recuperación.
