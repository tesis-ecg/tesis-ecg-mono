"""Login del paciente en la app móvil.

Lo que se está probando acá no es "que se pueda entrar", sino los tres límites
que separan la app del portal:

- el token es Bearer y tiene audience propia, así que una cookie del portal no
  sirve en `/mobile` ni un token móvil en `/patients`;
- el paciente solo puede ser paciente: una cuenta de médico con credenciales
  correctas no entra;
- un refresh de 60 días no puede usarse como access de 60 minutos.
"""

from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth0_client import Auth0Error
from app.core.security import create_access_token, create_mobile_tokens
from app.db.models.patient import Patient
from app.db.models.user import User, UserRole

PASSWORD = "Abc23xyz"  # nosec B105 - contraseña de prueba


def _fake_auth0(monkeypatch: pytest.MonkeyPatch, auth0_id: str | None) -> list[str]:
    """Sustituye el ROPG. Devuelve la lista de emails con los que se lo llamó."""
    calls: list[str] = []

    async def _authenticate(email: str, password: str, ip: str | None = None) -> str:
        calls.append(email)
        if auth0_id is None or password != PASSWORD:
            raise Auth0Error("INVALID_CREDENTIALS", "Credenciales inválidas.", 401)
        return auth0_id

    monkeypatch.setattr(
        "app.modules.patient_app.patient_app_service.authenticate_user", _authenticate
    )
    return calls


async def _account(
    make_patient: Callable[..., Any], make_patient_account: Callable[..., Any]
) -> tuple[Patient, User]:
    patient = await make_patient()
    user = await make_patient_account(patient)
    return patient, user


async def test_login_con_email(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patient, user = await _account(make_patient, make_patient_account)
    calls = _fake_auth0(monkeypatch, user.auth0_id)

    response = await client.post(
        "/mobile/auth/login", json={"identifier": user.email, "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accessToken"] and body["refreshToken"]
    assert body["patient"]["id"] == str(patient.id)
    assert body["patient"]["dni"] == patient.dni
    assert calls == [user.email]


async def test_login_con_dni_traduce_a_email(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auth0 solo entiende email: el DNI se resuelve antes de llamarlo."""
    patient, user = await _account(make_patient, make_patient_account)
    calls = _fake_auth0(monkeypatch, user.auth0_id)

    response = await client.post(
        "/mobile/auth/login", json={"identifier": patient.dni, "password": PASSWORD}
    )

    assert response.status_code == 200, response.text
    assert calls == [user.email], "el ROPG tiene que recibir el email, no el DNI"


async def test_dni_inexistente_no_llama_a_auth0(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Y responde lo mismo que una contraseña mala.

    Si distinguiera, el login sería un padrón de pacientes consultable a fuerza
    de probar DNIs.
    """
    calls = _fake_auth0(monkeypatch, "auth0|nadie")

    response = await client.post(
        "/mobile/auth/login", json={"identifier": "99999999", "password": PASSWORD}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
    assert calls == []


async def test_password_incorrecta(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, user = await _account(make_patient, make_patient_account)
    _fake_auth0(monkeypatch, user.auth0_id)

    response = await client.post(
        "/mobile/auth/login", json={"identifier": user.email, "password": "otra-cosa"}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


async def test_cuenta_de_medico_no_entra_a_la_app(
    client: AsyncClient, make_user: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Credenciales válidas, rol equivocado: se corta después del ROPG."""
    doctor_user = await make_user(UserRole.MEDICO)
    _fake_auth0(monkeypatch, doctor_user.auth0_id)

    response = await client.post(
        "/mobile/auth/login", json={"identifier": doctor_user.email, "password": PASSWORD}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.parametrize("path", ["/patients", "/studies", "/dashboard/overview"])
async def test_token_de_paciente_no_abre_el_portal(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
    path: str,
) -> None:
    """El portal usa cookie, no Bearer: sin cookie el paciente es un anónimo."""
    _, user = await _account(make_patient, make_patient_account)

    response = await client.get(path, headers=mobile_headers(user))

    assert response.status_code == 401


async def test_token_del_portal_no_sirve_en_mobile(
    client: AsyncClient, make_user: Callable[..., Any]
) -> None:
    """La audience separada es lo que lo impide: los firma el mismo secreto."""
    doctor_user = await make_user(UserRole.MEDICO)
    portal_token, _ = create_access_token(doctor_user)

    response = await client.get("/mobile/me", headers={"Authorization": f"Bearer {portal_token}"})

    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_EXPIRED"


async def test_me_devuelve_paciente_y_medico(
    client: AsyncClient,
    make_doctor: Callable[..., Any],
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    user = await make_patient_account(patient)

    response = await client.get("/mobile/me", headers=mobile_headers(user))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == str(patient.id)
    assert body["doctor"]["fullName"]


async def test_refresh_devuelve_access_nuevo(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
) -> None:
    _, user = await _account(make_patient, make_patient_account)
    _, refresh_token, _ = create_mobile_tokens(user)

    response = await client.post("/mobile/auth/refresh", json={"refreshToken": refresh_token})

    assert response.status_code == 200, response.text
    assert response.json()["accessToken"]


async def test_refresh_no_sirve_como_access(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
) -> None:
    """Sin el chequeo de `typ`, un token robado valdría 60 días en vez de una hora."""
    _, user = await _account(make_patient, make_patient_account)
    _, refresh_token, _ = create_mobile_tokens(user)

    response = await client.get("/mobile/me", headers={"Authorization": f"Bearer {refresh_token}"})

    assert response.status_code == 401
    assert response.json()["code"] == "SESSION_EXPIRED"


async def test_logout_invalida_access_y_refresh(
    client: AsyncClient,
    db: AsyncSession,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
) -> None:
    """`session_version` mata a los dos de una: no hay tabla de refresh tokens."""
    _, user = await _account(make_patient, make_patient_account)
    access, refresh_token, _ = create_mobile_tokens(user)
    headers = {"Authorization": f"Bearer {access}"}

    assert (await client.get("/mobile/me", headers=headers)).status_code == 200

    assert (await client.post("/mobile/auth/logout", headers=headers)).status_code == 204
    db.expire_all()

    assert (await client.get("/mobile/me", headers=headers)).status_code == 401
    refreshed = await client.post("/mobile/auth/refresh", json={"refreshToken": refresh_token})
    assert refreshed.status_code == 401


async def test_cuenta_inactiva_no_entra(
    client: AsyncClient,
    make_patient: Callable[..., Any],
    make_patient_account: Callable[..., Any],
    mobile_headers: Callable[[User], dict[str, str]],
) -> None:
    patient = await make_patient()
    user = await make_patient_account(patient, is_active=False)

    response = await client.get("/mobile/me", headers=mobile_headers(user))

    assert response.status_code == 401


async def test_sin_authorization_header(client: AsyncClient) -> None:
    response = await client.get("/mobile/me")

    assert response.status_code == 401
