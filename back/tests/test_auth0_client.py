"""Respuestas de Auth0 que no son JSON.

Auth0 no siempre contesta JSON: un dominio mal configurado, una caída o un proxy
en el medio devuelven HTML o texto plano. Sin protección, el `resp.json()` tira
un `JSONDecodeError` que sube hasta el handler genérico y el usuario ve
"Ocurrió un error interno" — sin ninguna pista de que el problema es el `.env`.
"""

import httpx
import pytest

from app.core.auth0_client import Auth0Error, _json_body


def _response(status: int, text: str, content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=text.encode(),
        headers={"content-type": content_type},
        request=httpx.Request("POST", "https://tenant.example.auth0.com/oauth/token"),
    )


def test_parses_a_normal_json_body() -> None:
    response = _response(400, '{"error": "invalid_grant"}', "application/json")

    assert _json_body(response) == {"error": "invalid_grant"}


def test_a_non_json_body_becomes_a_clear_502() -> None:
    """El caso real: `AUTH0_DOMAIN=your-tenant.auth0.com` en un `.env` sin completar."""
    response = _response(404, "Unknown host: your-tenant.auth0.com")

    with pytest.raises(Auth0Error) as error:
        _json_body(response)

    assert error.value.status == 502
    assert error.value.code == "AUTH0_UNAVAILABLE"
    # El mensaje tiene que decir dónde mirar, no solo que algo falló.
    assert "AUTH0_DOMAIN" in error.value.message
    assert "Unknown host" in error.value.message


def test_an_html_error_page_does_not_leak_the_whole_page() -> None:
    response = _response(502, "<html>" + "x" * 5000 + "</html>")

    with pytest.raises(Auth0Error) as error:
        _json_body(response)

    assert len(error.value.message) < 400


def test_a_json_array_is_rejected_as_unexpected() -> None:
    response = _response(200, "[1, 2, 3]", "application/json")

    with pytest.raises(Auth0Error) as error:
        _json_body(response)

    assert error.value.status == 502


def test_an_empty_body_becomes_a_502_not_a_crash() -> None:
    response = _response(500, "")

    with pytest.raises(Auth0Error) as error:
        _json_body(response)

    assert error.value.status == 502
