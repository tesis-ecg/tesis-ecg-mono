# Diagnóstico local de ingesta — 30/09/2026

Se reprodujo un `500` de aplicación al rebobinar `seq` cuando el estudio previo
tenía `started_at` en el futuro: el cierre escribía `ended_at < started_at` y
violaba `ck_study_time_range`. El cierre ahora usa el máximo entre la hora
actual y `started_at`. El test de contrato ejercita la ruta HTTP y verifica el
`202` del lote nuevo.

Los `500` observados anteriormente en la API desplegada no tienen logs
conservados. Esta prueba no demuestra su causa ni descarta límites de Vercel
Free. No se hizo ningún cambio de hosting.

## Reproducción local

Se levantaron PostgreSQL 16 y MinIO del `docker-compose.yml`, y se ejecutó:

```sh
cd back
RUN_LOCAL_LOAD=1 \
TEST_DATABASE_URL=postgresql+asyncpg://holter:holter@127.0.0.1:5435/holter_test \
.venv/bin/python -m pytest -q -s tests/test_ingest_local_load.py
```

La prueba crea un bucket temporal único y lo elimina al terminar. Usa una
transacción aislada de la base de test. Envía tamaños de 16, 48, 64, 128 y
256 tramas, seguidos de diez cargas de 64 tramas en el mismo estudio.
Todas recibieron `202` y procesaron el lote una vez, sin error.

| Tramas | ACK HTTP | Validación | Bloqueo de equipo | Escritura durable S3 | Después del ACK |
|---:|---:|---:|---:|---:|---:|
| 16 | 39 ms | <1 ms | 2 ms | 7 ms | 35 ms |
| 48 | 14 ms | <1 ms | <1 ms | 5 ms | 56 ms |
| 64 | 12 ms | <1 ms | 1 ms | 4 ms | 61 ms |
| 128 | 16 ms | <1 ms | 2 ms | 4 ms | 129 ms |
| 256 | 16 ms | <1 ms | 1 ms | 6 ms | 201 ms |
| Diez lotes de 64 | 9–14 ms | <1 ms | <1–1 ms | 2–4 ms | 82–108 ms |

Los números son de una corrida local en esta máquina. El tiempo de bloqueo
medido es la adquisición de la fila del equipo; el ACK incluye también las
consultas y el commit de PostgreSQL. MinIO se ejecutó en localhost. No se
pueden extrapolar estas latencias a Vercel, a la red del paciente ni a un
estudio de 15–30 días. El mayor lote verificado fue de **256 tramas**.
