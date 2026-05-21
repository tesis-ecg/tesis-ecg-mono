# Backend — Autenticación y autorización

> **Estado actual: sin autenticación implementada.** Todos los endpoints son abiertos en el MVP.
> La integración con Auth0 está planificada como implementación futura.

---

## Plan de autenticación (implementación futura)

Dos planos independientes:

| Cliente | Mecanismo | Estado |
|---|---|---|
| Dispositivo Holter | `X-API-Key` header, hash bcrypt en DB | Futuro |
| Médico (dashboard) | Auth0 JWT RS256 | Futuro |

---

## Autenticación del dispositivo — diseño futuro

Al provisionar el dispositivo, el backend generará una API key con `secrets.token_urlsafe(32)`, guardará el hash bcrypt en `Device.api_key_hash`, y retornará la key en texto plano **una sola vez**. El firmware la almacena en flash y la envía en cada request como header `X-API-Key`.

```python
# app/auth/api_key.py (a implementar)
async def device_auth(
    device_id: str,
    x_api_key: str = Header(...),
    db: AsyncSession = Depends(get_db),
) -> Device:
    device = await device_repository.get_by_id(db, device_id)
    if not device or not bcrypt.checkpw(x_api_key.encode(), device.api_key_hash):
        raise HTTPException(status_code=401, detail="Invalid device credentials")
    return device
```

---

## Autenticación de médicos — diseño futuro (Auth0)

El backend actuará como **resource server puro** (valida tokens, no los genera):

1. El dashboard hace login vía Auth0 PKCE flow → recibe JWT.
2. JWT enviado como `Authorization: Bearer <token>`.
3. Validación: firma RS256 via JWKS, claims `iss`/`aud`/`exp`, custom claim `https://holter.app/roles` → `["DOCTOR"]` o `["ADMIN"]`.

Control de acceso: los médicos solo pueden acceder a sus propios pacientes — verificado en la capa de servicio (no en el router). Se retorna 404 (no 403) para no revelar la existencia del recurso.

Variables de entorno necesarias cuando se implemente:
```
AUTH0_DOMAIN      <tenant>.auth0.com
AUTH0_AUDIENCE    https://api.holter.app
```
