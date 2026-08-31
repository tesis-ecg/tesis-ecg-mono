import asyncio
import hmac
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from app.core.config import settings as _settings
from app.core.logging import setup_logging
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


def _is_origin_exempt(path: str) -> bool:
    return path.startswith(_ORIGIN_EXEMPT_PREFIXES)


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
