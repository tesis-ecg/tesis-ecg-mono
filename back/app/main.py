import asyncio
import hmac
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from asyncpg.exceptions import LockNotAvailableError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import settings as _settings
from app.core.logging import setup_logging
from app.core.request_limits import MAX_CLINICAL_REPORT_PDF_BYTES
from app.db.session import engine
from app.modules.alerts import router as alerts_router
from app.modules.auth import router as auth_router
from app.modules.dashboard import router as dashboard_router
from app.modules.devices import router as devices_router
from app.modules.doctors import router as doctors_router
from app.modules.ingest import router as ingest_router
from app.modules.patient_app import router as patient_app_router
from app.modules.patients import router as patients_router
from app.modules.studies import router as studies_router
from app.modules.users import router as users_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    yield
    await engine.dispose()


is_production = _settings.environment.value == "production"
app = FastAPI(
    title="Holter ECG API",
    lifespan=lifespan,
    docs_url=None if is_production else "/docs",
    redoc_url=None if is_production else "/redoc",
    openapi_url=None if is_production else "/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[str(_settings.frontend_url).rstrip("/")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    # `Authorization` y `X-Device-*` los usa el simulador de chaleco, que
    # postea a /ingest con bearer en vez de cookie.
    allow_headers=[
        "Content-Type",
        "X-Request-ID",
        "Authorization",
        "X-Device-Serial",
        "X-Device-Uptime-Ms",
        "X-Firmware-Version",
        "X-Battery-Pct",
        "X-Bridge-Epoch-Ms",
        "X-Time-Sync-Source",
        "X-Time-Sync-Uncertainty-Ms",
        # Diagnóstico del paquete de STATUS (`INTEGRACION.md` §11.1). El equipo
        # real no pasa por CORS —no es un navegador—, pero el simulador de
        # chaleco del dashboard sí, y sin esto los perdería en el preflight.
        "X-Device-Lead-Flags",
        "X-Device-Loss-Flags",
        "X-Device-Status-Flags",
        "X-Device-Backlog-Seconds",
    ],
)

logger = structlog.get_logger(__name__)
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Rutas exentas del chequeo de Origin.
#
# El chequeo existe para frenar CSRF: un sitio hostil que hace que el navegador
# de un médico logueado dispare un POST con su cookie adjunta. `/ingest` no
# tiene esa superficie — no lee ninguna cookie, se autentica con un bearer
# explícito por dispositivo (ver `device_dependencies`), y una cookie de sesión
# no sirve para nada ahí. Exceptuarlo no debilita nada.
#
# Y hace falta: el co-procesador WiFi del chaleco es un cliente HTTP, no un
# navegador, así que no manda header `Origin`. Sin esta excepción, en preview y
# producción todo lote del equipo real se rechazaría con ORIGIN_FORBIDDEN.
# `/mobile` entra por lo mismo: la app usa `Authorization: Bearer` y no toca
# ninguna cookie, así que no hay nada que un sitio hostil pueda hacer que el
# celular adjunte solo. Y tampoco manda `Origin` — React Native no es un
# navegador —, así que sin la excepción todo POST del paciente daría 403 en
# preview y producción.
_ORIGIN_EXEMPT_PREFIXES = ("/ingest/", "/mobile/")
_CLINICAL_REPORT_FINALIZE_PATH = re.compile(r"^/studies/[^/]+/clinical-report/finalize$")


def _is_origin_exempt(path: str) -> bool:
    return path.startswith(_ORIGIN_EXEMPT_PREFIXES)


def _is_clinical_report_finalize(path: str) -> bool:
    return _CLINICAL_REPORT_FINALIZE_PATH.fullmatch(path) is not None


@app.middleware("http")
async def request_security_and_logging(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    request_id_header = request.headers.get("x-request-id", "")
    try:
        request_id = str(uuid.UUID(request_id_header))
    except ValueError:
        request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    origin = request.headers.get("origin")
    allowed_origin = str(_settings.frontend_url).rstrip("/")
    origin_checked = request.method in _UNSAFE_METHODS and not _is_origin_exempt(request.url.path)
    origin_missing_in_secure_environment = (
        origin_checked and _settings.is_secure_environment and not origin
    )
    origin_is_invalid = (
        origin_checked and origin is not None and origin.rstrip("/") != allowed_origin
    )
    if origin_missing_in_secure_environment or origin_is_invalid:
        return JSONResponse(
            status_code=403,
            content={
                "code": "ORIGIN_FORBIDDEN",
                "message": "Origen no permitido.",
                "fields": None,
                "requestId": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    # FastAPI materializa `Body(..., bytes)` antes de entrar al handler. Este
    # endpoint sólo recibe un ArrayBuffer generado por el portal, por lo que
    # exigir Content-Length evita bufferizar una carga chunked o demasiado
    # grande antes de que el servicio pueda responder 413.
    if request.method == "POST" and _is_clinical_report_finalize(request.url.path):
        content_length = request.headers.get("content-length")
        try:
            declared_size = int(content_length) if content_length is not None else None
        except ValueError:
            declared_size = None
        if declared_size is None or declared_size < 0:
            return JSONResponse(
                status_code=411,
                content={
                    "code": "CONTENT_LENGTH_REQUIRED",
                    "message": "El informe debe indicar el tamaño del PDF.",
                    "fields": None,
                    "requestId": request_id,
                },
                headers={"X-Request-ID": request_id},
            )
        if declared_size > MAX_CLINICAL_REPORT_PDF_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "code": "REPORT_TOO_LARGE",
                    "message": "El PDF supera el límite de 20 MB.",
                    "fields": None,
                    "requestId": request_id,
                },
                headers={"X-Request-ID": request_id},
            )

    started_at = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers["Cache-Control"] = "private, no-store"
    matched_route = request.scope.get("route")
    route_path = getattr(matched_route, "path", request.url.path)
    await logger.ainfo(
        "request_completed",
        request_id=request_id,
        method=request.method,
        route=route_path,
        status=response.status_code,
        duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
    )
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    # El cliente del frontend lee `message` del cuerpo y solo usa `detail` cuando es
    # string (front/src/lib/apiError.ts): con {"code","message"} anidado dentro de
    # `detail` mostraba siempre el texto genérico por status. Se aplana el payload
    # acá para no tocar los `raise HTTPException(detail={...})` de los services.
    detail = exc.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    message = detail.get("message") if isinstance(detail, dict) else None
    fields = detail.get("fields") if isinstance(detail, dict) else None
    if not isinstance(message, str):
        message = detail if isinstance(detail, str) else "La solicitud no pudo completarse."
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": code or f"HTTP_{exc.status_code}",
            "message": message,
            "fields": fields,
            "requestId": getattr(request.state, "request_id", None),
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
    fields: dict[str, str] = {}
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part != "body")
        fields[location or "request"] = str(error["msg"])
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION",
            "message": "Los datos enviados no son válidos.",
            "fields": fields,
            "requestId": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(DBAPIError)
async def database_contention_handler(request: Request, exc: DBAPIError) -> Response:
    """La contención de lock es una espera, no una falla del servidor.

    Dos escrituras sobre la misma fila de `study` —una ingesta y el procesamiento
    del lote anterior— se serializan con `FOR UPDATE`. La que pierde espera hasta
    el `lock_timeout` y Postgres la aborta. Hasta acá eso caía en el handler
    genérico y salía como `500 INTERNAL_ERROR`: el equipo lo leía como "el
    backend está roto" cuando en realidad solo había que reintentar. Es
    exactamente lo que reportó Biomédica el 8/9/2026.

    Un `503` con `Retry-After` dice lo que pasa de verdad. El cursor no se movió,
    así que reintentar el mismo lote es seguro — la ingesta es idempotente por
    `seq` y esa es justamente la propiedad que hace que no se pierda señal.

    Solo `LockNotAvailableError` (SQLSTATE 55P03), que es lo que levanta el
    `lock_timeout`. El `statement_timeout` levanta `QueryCanceledError` (57014) y
    ese NO es contención: es una consulta que de verdad tardó 15 s. Taparlo con
    "reintentá en unos segundos" escondería una regresión del trabajo cuadrático
    que este cambio vino a sacar, y dejaría al equipo reintentando contra algo
    que no se va a arreglar solo. Ese cae al handler genérico y sale como 500,
    que es lo que hay que ver.
    """
    if not isinstance(getattr(exc, "orig", None), LockNotAvailableError):
        return await unhandled_exception_handler(request, exc)
    await logger.awarning(
        "database_contention",
        request_id=getattr(request.state, "request_id", None),
        route=request.url.path,
    )
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "5"},
        content={
            "code": "SERVICE_BUSY",
            "message": "El estudio está siendo actualizado. Reintentar en unos segundos.",
            "fields": None,
            "requestId": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    await logger.aexception(
        "unhandled_request_error",
        request_id=getattr(request.state, "request_id", None),
        route=request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "Ocurrió un error interno.",
            "fields": None,
            "requestId": getattr(request.state, "request_id", None),
        },
    )


app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(devices_router, prefix="/devices", tags=["devices"])
app.include_router(doctors_router, prefix="/doctors", tags=["doctors"])
app.include_router(patients_router, prefix="/patients", tags=["patients"])
app.include_router(studies_router, prefix="/studies", tags=["studies"])
app.include_router(users_router, prefix="/users", tags=["users"])
app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
app.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
app.include_router(ingest_router, prefix="/ingest", tags=["ingest"])
app.include_router(patient_app_router, prefix="/mobile", tags=["mobile"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(request: Request) -> dict[str, str]:
    if _settings.is_secure_environment:
        provided_token = request.headers.get("x-readiness-token", "")
        expected_token = _settings.readiness_token or ""
        if not hmac.compare_digest(provided_token, expected_token):
            raise HTTPException(
                status_code=404,
                detail={"code": "NOT_FOUND", "message": "Recurso no encontrado."},
            )
    try:
        async with asyncio.timeout(2):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (TimeoutError, OSError, SQLAlchemyError) as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "NOT_READY", "message": "Base de datos no disponible."},
        ) from exc
    return {"status": "ready"}
