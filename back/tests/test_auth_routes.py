from starlette.responses import Response

from app.modules.auth.auth_routes import _set_session_cookie


def test_session_cookie_migration_sets_scoped_v2_and_legacy_cookie() -> None:
    response = Response()

    _set_session_cookie(response, "signed-token")

    cookies = response.headers.getlist("set-cookie")
    assert len(cookies) == 2
    assert any(
        cookie.startswith("holter_session_v2=signed-token")
        and "HttpOnly" in cookie
        and "Path=/api" in cookie
        and "SameSite=lax" in cookie
        for cookie in cookies
    )
    assert any(
        cookie.startswith("session=signed-token") and "HttpOnly" in cookie and "Path=/" in cookie
        for cookie in cookies
    )
