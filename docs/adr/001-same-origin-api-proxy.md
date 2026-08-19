# ADR-001: Proxy same-origin para el dashboard

## Estado

Aceptado.

## Decisión

Frontend y backend permanecen en proyectos Vercel separados. El frontend publica `/api/*` y lo
reescribe al `BACKEND_ORIGIN` correspondiente al entorno. El browser no consume directamente el
dominio del backend.

## Consecuencias

- Las cookies clínicas pueden usar `HttpOnly`, `Secure`, `SameSite=Lax` y `Path=/api`.
- Preview y Production deben definir orígenes backend distintos.
- Los ECG grandes no pasan por el proxy: se descargan desde S3 mediante URLs breves.
- Durante un TTL se conserva una cookie legacy para despliegues escalonados.
