# Documentación técnica — Holter ECG

Documentación de implementación para el equipo de Informática.

| Archivo | Contenido |
|---|---|
| `backend/01-arquitectura.md` | Estructura del proyecto FastAPI, capas, convenciones |
| `backend/02-modelos.md` | Modelos SQLAlchemy, relaciones, soft deletes |
| `backend/03-endpoints.md` | Rutas de la API — dispositivo y dashboard médico |
| `backend/04-autenticacion.md` | Auth0 JWT (médicos) + API Key (dispositivo) |
| `backend/05-pipeline-ml.md` | Pipeline de análisis ECG con neurokit2 |
| `backend/06-setup-local.md` | Docker Compose, variables de entorno, Alembic |

> La documentación de arquitectura de alto nivel (decisiones SIM vs BLE, firmware, batería) está en `info del proyecto/`.
