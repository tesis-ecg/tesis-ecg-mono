from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.logging import setup_logging
from app.db.session import engine
from app.modules.admin import router as admin_router
from app.modules.alerts import router as alerts_router
from app.modules.devices import router as devices_router
from app.modules.doctors import router as doctors_router
from app.modules.ecg_batches import router as ecg_batches_router
from app.modules.patients import router as patients_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    async with engine.connect():
        pass
    yield


app = FastAPI(title="Holter ECG API", lifespan=lifespan)

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(devices_router, prefix="/devices", tags=["devices"])
app.include_router(doctors_router, prefix="/doctors", tags=["doctors"])
app.include_router(patients_router, prefix="/patients", tags=["patients"])
app.include_router(ecg_batches_router, prefix="/ecg-batches", tags=["ecg-batches"])
app.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
