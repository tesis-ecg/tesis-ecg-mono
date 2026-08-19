# ADR-003: Excepción controlada para Auth0 ROPG

## Estado

Aceptado por requerimiento de producto.

## Decisión

Se mantiene el formulario email/contraseña mediado por backend. Se compensa el riesgo con
aprovisionamiento administrativo obligatorio, validación RS256/JWKS del token Auth0, propagación
del IP validado, rate limiting persistente y protecciones de ataque del tenant.

## Consecuencias

- El backend procesa la contraseña y debe tratarse como cliente altamente confiable.
- Un usuario existente sólo en Auth0 no obtiene rol ni acceso local.
- La migración futura recomendada sigue siendo Authorization Code + PKCE/Universal Login.
