"""Alta del paciente con su cuenta de la app.

Desde que la app existe, crear un paciente crea también su usuario: es lo que
le permite entrar al celular y ver su chaleco. El punto delicado es el orden —
primero la fila local en `PENDING`, después Auth0 — para que un fallo del
proveedor deje una cuenta reintentable y nunca un usuario en Auth0 que acá no
existe.

La contraseña se entrega una sola vez. Auth0 guarda el hash: no hay endpoint
que la pueda volver a leer, y guardarla nosotros sería dejar credenciales de
pacientes recuperables desde la base.
"""

from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import Auth0Error
from app.core.passwords import PASSWORD_LENGTH, generate_patient_password
from app.db.models.patient import Patient
from app.db.models.push_token import PushPlatform, PushToken
from app.db.models.user import IdentityStatus, User, UserRole

NEW_PATIENT = {
    "fullName": "Ana Gómez",
    "dni": "33111222",
    "birthDate": "1962-04-11",
    "sex": "F",
    "contactEmail": "ana.gomez@example.test",
    "contactPhone": "+541155550000",
}


async def _doctor_user(db: AsyncSession, doctor: Any) -> User:
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


def _fake_auth0_create(monkeypatch: pytest.MonkeyPatch, auth0_id: str | None) -> list[str]:
    calls: list[str] = []

    async def _create(email: str, password: str, full_name: str) -> str:
        calls.append(password)
        if auth0_id is None:
            raise Auth0Error("AUTH0_ERROR", "Auth0 no responde.", 502)
        return auth0_id

    monkeypatch.setattr("app.modules.patients.patients_service.create_auth0_user", _create)
    return calls


# --------------------------------------------------------------------------- #
# Generador de contraseñas
# --------------------------------------------------------------------------- #


def test_password_cumple_la_politica_de_auth0() -> None:
    """Las tres clases están garantizadas, no libradas al azar.

    Un rechazo del Management API a mitad del alta deja la ficha creada y la
    cuenta no, así que la contraseña no puede depender de la suerte.
    """
    for _ in range(200):
        password = generate_patient_password()
        assert len(password) == PASSWORD_LENGTH
        assert password.isalnum()
        assert any(c.islower() for c in password)
        assert any(c.isupper() for c in password)
        assert any(c.isdigit() for c in password)
        # Se dicta por teléfono: nada que se confunda al leer en voz alta.
        assert not set(password) & set("0O1lI")


# --------------------------------------------------------------------------- #
# Alta
# --------------------------------------------------------------------------- #


async def test_alta_crea_cuenta_y_devuelve_password(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = await make_doctor()
    calls = _fake_auth0_create(monkeypatch, "auth0|nuevo-paciente")

    response = await as_user(await _doctor_user(db, doctor)).post("/patients", json=NEW_PATIENT)

    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["generatedPassword"]) == PASSWORD_LENGTH
    assert body["hasAppAccount"] is True
    # Es la misma contraseña que se mandó a Auth0, no una distinta que el médico
    # dictaría en vano.
    assert calls == [body["generatedPassword"]]

    patient = await db.scalar(select(Patient).where(Patient.dni == NEW_PATIENT["dni"]))
    assert patient is not None and patient.user_id is not None
    account = await db.get(User, patient.user_id)
    assert account is not None
    assert account.role == UserRole.PACIENTE
    assert account.email == NEW_PATIENT["contactEmail"]
    assert account.is_active is True
    assert account.identity_status == IdentityStatus.ACTIVE
    assert account.auth0_id == "auth0|nuevo-paciente"


async def test_alta_sin_email_es_422(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
) -> None:
    doctor = await make_doctor()
    payload = {key: value for key, value in NEW_PATIENT.items() if key != "contactEmail"}

    response = await as_user(await _doctor_user(db, doctor)).post("/patients", json=payload)

    assert response.status_code == 422


async def test_alta_con_email_ya_usado_es_409(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_user: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
) -> None:
    doctor = await make_doctor()
    await make_user(UserRole.MEDICO, email=NEW_PATIENT["contactEmail"])

    response = await as_user(await _doctor_user(db, doctor)).post("/patients", json=NEW_PATIENT)

    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_CONFLICT"


async def test_auth0_caido_deja_la_cuenta_reintentable(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La ficha queda creada y la cuenta en `ERROR`, nunca al revés.

    Un usuario en Auth0 sin fila local sería un huérfano que nadie ve; una fila
    local en `ERROR` es visible y se puede reintentar desde el portal.
    """
    doctor = await make_doctor()
    _fake_auth0_create(monkeypatch, None)

    response = await as_user(await _doctor_user(db, doctor)).post("/patients", json=NEW_PATIENT)

    assert response.status_code == 502
    db.expire_all()
    patient = await db.scalar(select(Patient).where(Patient.dni == NEW_PATIENT["dni"]))
    assert patient is not None and patient.user_id is not None
    account = await db.get(User, patient.user_id)
    assert account is not None
    assert account.identity_status == IdentityStatus.ERROR
    assert account.is_active is False
    assert account.auth0_id is None


# --------------------------------------------------------------------------- #
# Contraseña
# --------------------------------------------------------------------------- #


async def test_regenerar_password_cierra_las_sesiones_vivas(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
    mobile_headers: Callable[[User], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La contraseña vieja deja de servir, pero el token ya emitido vive 60 días.

    Sin bumpear `session_version`, alguien con el celular del paciente seguiría
    entrando dos meses después de que le "cambiaron" la clave.
    """
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    account = await make_patient_account(patient)
    headers = mobile_headers(account)
    db.add(PushToken(user_id=account.id, token="ExponentPushToken[x]", platform=PushPlatform.IOS))
    await db.flush()

    applied: list[str] = []

    async def _update(auth0_id: str, password: str) -> None:
        applied.append(password)

    monkeypatch.setattr("app.modules.patients.patients_service.update_auth0_user_password", _update)

    assert (await client.get("/mobile/me", headers=headers)).status_code == 200

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/patients/{patient.id}/app-password"
    )

    assert response.status_code == 200, response.text
    password = response.json()["password"]
    assert len(password) == PASSWORD_LENGTH
    assert applied == [password]

    db.expire_all()
    assert (await client.get("/mobile/me", headers=headers)).status_code == 401
    token = await db.scalar(select(PushToken).where(PushToken.user_id == account.id))
    assert token is not None and token.deleted_at is not None


async def test_regenerar_password_sin_cuenta_es_409(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/patients/{patient.id}/app-password"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "NO_APP_ACCOUNT"


async def test_crear_acceso_a_paciente_viejo(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Los pacientes cargados antes de la app se completan a mano desde la ficha."""
    doctor = await make_doctor()
    patient = await make_patient(doctor, email="viejo@example.test")
    patient_id = patient.id
    _fake_auth0_create(monkeypatch, "auth0|retro")

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/patients/{patient.id}/app-account"
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["password"]) == PASSWORD_LENGTH
    # El id se guarda antes: `expire_all` también vence la PK del objeto en
    # memoria, y leerla después dispararía IO fuera del greenlet de SQLAlchemy.
    db.expire_all()
    refreshed = await db.get(Patient, patient_id)
    assert refreshed is not None and refreshed.user_id is not None


async def test_crear_acceso_sin_email_es_422(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor, email=None)

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/patients/{patient.id}/app-account"
    )

    assert response.status_code == 422
    assert response.json()["code"] == "EMAIL_REQUIRED"


async def test_crear_acceso_dos_veces_es_409(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    await make_patient_account(patient)

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/patients/{patient.id}/app-account"
    )

    assert response.status_code == 409
    assert response.json()["code"] == "APP_ACCOUNT_EXISTS"


async def test_reset_por_email(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    account = await make_patient_account(patient)
    sent: list[str] = []

    async def _reset(email: str) -> None:
        sent.append(email)

    monkeypatch.setattr("app.modules.patients.patients_service.trigger_password_reset", _reset)

    response = await as_user(await _doctor_user(db, doctor)).post(
        f"/patients/{patient.id}/password-reset"
    )

    assert response.status_code == 204
    assert sent == [account.email]


# --------------------------------------------------------------------------- #
# Baja
# --------------------------------------------------------------------------- #


async def test_baja_del_paciente_cierra_el_acceso(
    client: AsyncClient,
    db: AsyncSession,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    as_user: Callable[[User], AsyncClient],
    mobile_headers: Callable[[User], dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin esto el paciente dado de baja seguiría entrando y recibiendo avisos."""
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    account = await make_patient_account(patient)
    headers = mobile_headers(account)
    db.add(PushToken(user_id=account.id, token="ExponentPushToken[y]", platform=PushPlatform.IOS))
    await db.flush()

    blocked: list[str] = []

    async def _block(auth0_id: str) -> None:
        blocked.append(auth0_id)

    monkeypatch.setattr("app.modules.patients.patients_service.block_auth0_user", _block)
    patient_id, account_id, auth0_id = patient.id, account.id, account.auth0_id

    assert (await client.get("/mobile/me", headers=headers)).status_code == 200

    response = await as_user(await _doctor_user(db, doctor)).delete(f"/patients/{patient_id}")

    assert response.status_code == 204
    assert blocked == [auth0_id]
    db.expire_all()
    assert (await client.get("/mobile/me", headers=headers)).status_code == 401
    token = await db.scalar(select(PushToken).where(PushToken.user_id == account_id))
    assert token is not None and token.deleted_at is not None
