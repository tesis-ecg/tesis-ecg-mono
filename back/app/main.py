from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings as _settings
from app.core.logging import setup_logging
from app.db.session import engine
from app.modules.admin import router as admin_router
from app.modules.alerts import router as alerts_router
from app.modules.auth import router as auth_router
from app.modules.dashboard import router as dashboard_router
from app.modules.devices import router as devices_router
from app.modules.doctors import router as doctors_router
from app.modules.ecg_batches import router as ecg_batches_router
from app.modules.patients import router as patients_router
from app.modules.studies import router as studies_router
from app.modules.users import router as users_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    async with engine.connect():
        pass
    yield


app = FastAPI(title="Holter ECG API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> Response:
    # El cliente del frontend lee `message` del cuerpo y solo usa `detail` cuando es
    # string (front/src/lib/apiError.ts): con {"code","message"} anidado dentro de
    # `detail` mostraba siempre el texto genérico por status. Se aplana el payload
    # acá para no tocar los `raise HTTPException(detail={...})` de los services.
    detail = exc.detail
    if isinstance(detail, dict) and "message" in detail:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": detail.get("code"), "message": detail["message"]},
            headers=exc.headers,
        )
    return await default_http_exception_handler(request, exc)


app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(devices_router, prefix="/devices", tags=["devices"])
app.include_router(doctors_router, prefix="/doctors", tags=["doctors"])
app.include_router(patients_router, prefix="/patients", tags=["patients"])
app.include_router(studies_router, prefix="/studies", tags=["studies"])
app.include_router(ecg_batches_router, prefix="/ecg-batches", tags=["ecg-batches"])
app.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(users_router, prefix="/users", tags=["users"])
app.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
