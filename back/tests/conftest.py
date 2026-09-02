"""Harness de test del backend.

Las variables de entorno se fijan **antes** de importar nada de `app`, porque
`app.core.config.Settings` se instancia al importar el módulo.

`DATABASE_URL` se puede apuntar a otro lado con `TEST_DATABASE_URL` (útil en
local: el compose expone Postgres en 5435, CI lo expone en 5432). Se usa una
variable aparte a propósito — si el override fuera `DATABASE_URL` directo, un
`DATABASE_URL` exportado en la terminal apuntando a la base de desarrollo haría
que la suite la migre y la escriba.
"""

import os

TEST_ENV = {
    "DATABASE_URL": os.environ.get(
        "TEST_DATABASE_URL", "postgresql+asyncpg://holter:holter@localhost:5432/holter_test"
    ),
    "S3_BUCKET_NAME": "holter-test",
    "S3_ENDPOINT_URL": "",
    "S3_PUBLIC_ENDPOINT_URL": "",
    "AWS_ACCESS_KEY_ID": "test-access-key",
    "AWS_SECRET_ACCESS_KEY": "test-secret-key",
    "AWS_REGION": "us-east-1",
    "AUTH0_DOMAIN": "tenant.example.auth0.com",
    "AUTH0_CLIENT_ID": "test-client",
    "AUTH0_CLIENT_SECRET": "test-client-secret",
    "AUTH0_AUDIENCE": "https://api.holter.test",
    "AUTH0_MGMT_CLIENT_ID": "test-mgmt-client",
    "AUTH0_MGMT_CLIENT_SECRET": "test-mgmt-secret",
    "JWT_SECRET": "test-secret-with-at-least-thirty-two-characters",
    "ENVIRONMENT": "test",
    "FRONTEND_URL": "http://localhost:5173",
}

for key, value in TEST_ENV.items():
    os.environ[key] = value

# ruff: noqa: E402  — el entorno tiene que estar armado antes de importar `app`.
import asyncio
import hashlib
import secrets
import uuid
from collections.abc import AsyncGenerator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.models.device import Device, DeviceStatus
from app.db.models.doctor import Doctor
from app.db.models.patient import Patient, PatientSex, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.db.models.user import IdentityStatus, User, UserRole

SAMPLE_RATE = 500


# --------------------------------------------------------------------------- #
# Esquema: se migra con Alembic, no con `create_all`
# --------------------------------------------------------------------------- #
# Correr las migraciones de verdad es lo único que prueba que la migración que
# acompaña a un cambio de modelo existe y aplica. `Base.metadata.create_all`
# construiría un esquema que ninguna base real va a tener.


async def _create_database_if_missing() -> None:
    import asyncpg

    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    base_url, _, dbname = url.rpartition("/")
    dbname = dbname.split("?")[0]
    conn = await asyncpg.connect(f"{base_url}/postgres")
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", dbname)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{dbname}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    from alembic.config import Config

    from alembic import command

    asyncio.run(_create_database_if_missing())
    config = Config("alembic.ini")
    command.upgrade(config, "head")


# --------------------------------------------------------------------------- #
# Sesión de base de datos
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def db() -> AsyncGenerator[AsyncSession, None]:
    """Sesión aislada: todo lo que escribe el test se descarta al terminar.

    La sesión se ata a una conexión con una transacción externa abierta y usa
    `join_transaction_mode="create_savepoint"`, así que un `commit()` del código
    bajo test libera un savepoint pero la transacción externa igual se revierte.
    Sin eso habría que limpiar tablas a mano entre tests.
    """
    engine = create_async_engine(settings.database_url, poolclass=None)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        session = factory()
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()


class CommittedWorld:
    """Sesiones REALES, con commits que se ven entre conexiones.

    El fixture `db` envuelve todo en una transacción que se revierte, así que
    dos "requests" comparten una sola conexión y nunca compiten de verdad. Para
    probar el `SELECT ... FOR UPDATE` hace falta lo contrario: conexiones
    distintas que se bloqueen entre sí.

    El precio es limpiar a mano — no hay rollback que salve.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self.doctor_ids: list[uuid.UUID] = []

    def session(self) -> AsyncSession:
        return self._factory()

    def track(self, doctor: Doctor) -> None:
        self.doctor_ids.append(doctor.id)


#: Orden de borrado: de las hojas hacia la raíz, respetando las FKs.
_PURGE_STATEMENTS = (
    "DELETE FROM alert WHERE patient_id IN (SELECT id FROM patient WHERE doctor_id = ANY(:ids))",
    "DELETE FROM ecg_event WHERE batch_id IN (SELECT b.id FROM ecg_batch b"
    " JOIN device d ON d.id = b.device_id WHERE d.doctor_id = ANY(:ids))",
    "DELETE FROM ecg_batch WHERE device_id IN (SELECT id FROM device WHERE doctor_id = ANY(:ids))",
    "DELETE FROM study WHERE patient_id IN (SELECT id FROM patient WHERE doctor_id = ANY(:ids))",
    "DELETE FROM device WHERE doctor_id = ANY(:ids)",
    "DELETE FROM patient WHERE doctor_id = ANY(:ids)",
)


@pytest.fixture
async def committed_world() -> AsyncGenerator[CommittedWorld, None]:
    from sqlalchemy import text

    engine = create_async_engine(settings.database_url)
    world = CommittedWorld(async_sessionmaker(engine, expire_on_commit=False))
    try:
        yield world
    finally:
        async with world.session() as cleanup:
            if world.doctor_ids:
                params = {"ids": world.doctor_ids}
                # `user` va después de `doctor` (doctor.user_id apunta a user),
                # así que los ids hay que leerlos antes de borrar los doctores.
                user_ids = list(
                    (
                        await cleanup.scalars(
                            text("SELECT user_id FROM doctor WHERE id = ANY(:ids)"), params
                        )
                    ).all()
                )
                for statement in _PURGE_STATEMENTS:
                    await cleanup.execute(text(statement), params)
                await cleanup.execute(text("DELETE FROM doctor WHERE id = ANY(:ids)"), params)
                if user_ids:
                    await cleanup.execute(
                        text('DELETE FROM "user" WHERE id = ANY(:ids)'), {"ids": user_ids}
                    )
                await cleanup.commit()
        await engine.dispose()


# --------------------------------------------------------------------------- #
# S3 falso
# --------------------------------------------------------------------------- #


@pytest.fixture
def s3() -> Iterator[None]:
    """Monta `moto` y limpia los clientes cacheados de `app.core.s3`.

    El `reset` va antes y después: los clientes tienen `lru_cache`, así que uno
    construido fuera del mock seguiría apuntando a AWS real dentro de él, y uno
    construido adentro seguiría apuntando a moto una vez cerrado.
    """
    from moto import mock_aws

    from app.core.s3 import ensure_bucket, reset_s3_clients

    with mock_aws():
        reset_s3_clients()
        ensure_bucket()
        yield
    reset_s3_clients()


# --------------------------------------------------------------------------- #
# Cliente HTTP
# --------------------------------------------------------------------------- #


@pytest.fixture
def scheduled_batches(monkeypatch: pytest.MonkeyPatch) -> list[uuid.UUID]:
    """Intercepta el `BackgroundTasks` de la ingesta en vez de dejarlo correr.

    La tarea real abre su propia sesión contra el engine global, que es OTRA
    conexión: no vería nada de lo que escribió el test (que vive en una
    transacción sin commitear) y fallaría de forma confusa. Además, dejarla
    correr en paralelo haría los tests de procesamiento no deterministas.

    Los tests que quieren procesar llaman a `process_batch(db, batch_id)` con la
    sesión del test, que es más explícito y permite afirmar sobre el resultado.
    """
    scheduled: list[uuid.UUID] = []

    async def _spy(batch_id: uuid.UUID) -> None:
        scheduled.append(batch_id)

    monkeypatch.setattr("app.modules.ingest.processing.process_batch_task", _spy)
    return scheduled


@pytest.fixture
def sent_pushes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[uuid.UUID, Any]]:
    """Intercepta las notificaciones al paciente, por el mismo motivo que los batches.

    `notify_patient_task` abre su propia sesión contra el engine global: no
    vería los push tokens que el test escribió en su transacción sin commitear,
    así que el envío real sería siempre un no-op silencioso. Interceptándola se
    puede afirmar *qué* se habría mandado, que es lo que importa.
    """
    sent: list[tuple[uuid.UUID, Any]] = []

    async def _spy(patient_id: uuid.UUID, message: Any) -> None:
        sent.append((patient_id, message))

    for module in (
        "app.modules.ingest.ingest_service",
        "app.modules.ingest.processing",
        # `schedule_alert_push` importa la tarea adentro de la función, así que
        # lo que hay que pisar es el atributo del módulo donde vive.
        "app.modules.patient_app.notifications_service",
    ):
        monkeypatch.setattr(f"{module}.notify_patient_task", _spy)
    return sent


@pytest.fixture
async def client(
    db: AsyncSession,
    scheduled_batches: list[uuid.UUID],
    sent_pushes: list[tuple[uuid.UUID, Any]],
) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP que comparte la sesión (y la transacción) del test.

    Se usa `AsyncClient` sobre ASGI y no `TestClient`: `TestClient` corre la app
    en su propio event loop, y la sesión async del test está atada al loop de
    pytest — mezclarlos da "attached to a different loop" en cada query.
    """
    from app.dependencies.common_dependencies import get_db
    from app.main import app

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest.fixture
def as_user(client: AsyncClient) -> Callable[[User], AsyncClient]:
    """Autentica el cliente como un usuario, salteando Auth0 y la cookie."""
    from app.dependencies.auth_dependencies import get_current_user
    from app.main import app

    def _login(user: User) -> AsyncClient:
        async def _override() -> User:
            return user

        app.dependency_overrides[get_current_user] = _override
        return client

    return _login


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_user(db: AsyncSession) -> Callable[..., "asyncio.Future[User]"]:
    async def _make(role: UserRole = UserRole.MEDICO, **kwargs: object) -> User:
        suffix = uuid.uuid4().hex[:12]
        user = User(
            auth0_id=kwargs.pop("auth0_id", f"auth0|{suffix}"),
            email=kwargs.pop("email", f"{suffix}@example.test"),
            full_name=kwargs.pop("full_name", f"Usuario {suffix}"),
            role=role,
            is_active=True,
            identity_status=IdentityStatus.ACTIVE,
            session_version=1,
            **kwargs,
        )
        db.add(user)
        await db.flush()
        return user

    return _make  # type: ignore[return-value]


@pytest.fixture
def make_doctor(db: AsyncSession, make_user: Callable[..., object]) -> Callable[..., object]:
    async def _make(**kwargs: object) -> Doctor:
        user = await make_user(UserRole.MEDICO)  # type: ignore[misc]
        doctor = Doctor(
            user_id=user.id,
            specialty=kwargs.pop("specialty", "Cardiología"),
            license_number=kwargs.pop("license_number", f"MN-{uuid.uuid4().hex[:8]}"),
            **kwargs,
        )
        db.add(doctor)
        await db.flush()
        return doctor

    return _make


@pytest.fixture
def make_patient(db: AsyncSession, make_doctor: Callable[..., object]) -> Callable[..., object]:
    async def _make(doctor: Doctor | None = None, **kwargs: object) -> Patient:
        if doctor is None:
            doctor = await make_doctor()  # type: ignore[misc]
        suffix = uuid.uuid4().hex[:10]
        patient = Patient(
            doctor_id=doctor.id,
            medical_record_num=kwargs.pop("medical_record_num", f"HC-{suffix}"),
            first_name=kwargs.pop("first_name", "Paciente"),
            last_name=kwargs.pop("last_name", suffix),
            date_of_birth=kwargs.pop("date_of_birth", datetime(1970, 1, 1).date()),
            dni=kwargs.pop("dni", str(20_000_000 + int(suffix[:6], 16) % 10_000_000)),
            sex=kwargs.pop("sex", PatientSex.M),
            study_status=kwargs.pop("study_status", PatientStudyStatus.ACTIVE),
            **kwargs,
        )
        db.add(patient)
        await db.flush()
        return patient

    return _make


def _owner_doctor_id(doctor: Doctor | None, patient: Patient | None) -> uuid.UUID | None:
    if doctor is not None:
        return doctor.id
    return patient.doctor_id if patient is not None else None


@pytest.fixture
def make_device(db: AsyncSession) -> Callable[..., object]:
    async def _make(
        patient: Patient | None = None,
        doctor: Doctor | None = None,
        status: DeviceStatus | None = None,
        **kwargs: object,
    ) -> tuple[Device, str]:
        """Devuelve `(device, api_key_en_claro)`.

        La key en claro solo existe acá: en la base va el sha256, igual que en
        producción.
        """
        api_key = kwargs.pop("api_key", secrets.token_urlsafe(32))
        assert isinstance(api_key, str)
        if status is None:
            status = DeviceStatus.ASSIGNED if patient is not None else DeviceStatus.AVAILABLE
        device = Device(
            serial_number=kwargs.pop("serial_number", f"HOL-{uuid.uuid4().hex[:10].upper()}"),
            model=kwargs.pop("model", "Holter ECG"),
            api_key_hash=hashlib.sha256(api_key.encode()).hexdigest(),
            patient_id=patient.id if patient is not None else None,
            doctor_id=_owner_doctor_id(doctor, patient),
            status=status,
            firmware_version=kwargs.pop("firmware_version", "1.0.0"),
            **kwargs,
        )
        db.add(device)
        await db.flush()
        return device, api_key

    return _make


@pytest.fixture
def make_study(db: AsyncSession) -> Callable[..., object]:
    async def _make(patient: Patient, device: Device, **kwargs: object) -> Study:
        started_at = kwargs.pop("started_at", datetime.now(UTC) - timedelta(hours=1))
        assert isinstance(started_at, datetime)
        study = Study(
            patient_id=patient.id,
            device_id=device.id,
            started_at=started_at,
            status=kwargs.pop("status", StudyStatus.IN_PROGRESS),
            sample_rate=kwargs.pop("sample_rate", SAMPLE_RATE),
            **kwargs,
        )
        db.add(study)
        await db.flush()
        return study

    return _make


# --------------------------------------------------------------------------- #
# App móvil del paciente
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_patient_account(db: AsyncSession) -> Callable[..., object]:
    """Le da a un paciente su cuenta de la app, como haría el alta del portal."""

    async def _make(patient: Patient, **kwargs: object) -> User:
        suffix = uuid.uuid4().hex[:12]
        user = User(
            auth0_id=kwargs.pop("auth0_id", f"auth0|{suffix}"),
            email=kwargs.pop("email", f"paciente-{suffix}@example.test"),
            full_name=f"{patient.first_name} {patient.last_name}".strip(),
            role=UserRole.PACIENTE,
            is_active=kwargs.pop("is_active", True),
            identity_status=kwargs.pop("identity_status", IdentityStatus.ACTIVE),
            session_version=1,
            **kwargs,
        )
        db.add(user)
        await db.flush()
        patient.user_id = user.id
        patient.email = user.email
        await db.flush()
        return user

    return _make


@pytest.fixture
def mobile_headers() -> Callable[[User], dict[str, str]]:
    """Bearer real, firmado como en producción.

    A diferencia de `as_user`, que sobreescribe la dependencia, acá se ejercita
    la cadena entera: firma, audience móvil, `session_version` y rol. Es lo que
    se está construyendo, así que saltearlo no probaría nada.
    """
    from app.core.security import create_mobile_tokens

    def _headers(user: User) -> dict[str, str]:
        access, _, _ = create_mobile_tokens(user)
        return {"Authorization": f"Bearer {access}"}

    return _headers
