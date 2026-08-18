---
name: run-holter
description: Levanta la app localmente (dashboard + API) — container de Postgres/MinIO, migraciones de Alembic, backend FastAPI y frontend Vite. Usar cuando pidan correr, arrancar, levantar o parar el proyecto en local, ver el dashboard en el navegador, o cuando algo del stack local no arranca.
---

# Levantar el stack local

Dos procesos nativos en la Mac (backend FastAPI + frontend Vite) contra
Postgres y MinIO en Docker. **El orden importa**: uvicorn abre una conexión a
Postgres en su `lifespan` y muere al arrancar si la db no está lista, así que
el container va siempre primero.

Todas las rutas de este documento son relativas a la raíz del repo.

## Camino rápido (recomendado)

```bash
.claude/skills/run-holter/stack.sh up
```

Hace, en orden: `docker compose up -d db minio` → espera a que Postgres esté
`healthy` → `uv sync` + `alembic upgrade head` → arranca Vite → lee el puerto
real que consiguió Vite → arranca uvicorn con ese puerto en `FRONTEND_URL`
(si no, CORS bloquea todo — ver Gotchas) → espera `/health`.

Tarda ~2 s con los containers ya arriba y **devuelve la terminal**: los
servidores quedan corriendo desacoplados, con los logs en
`.claude/skills/run-holter/.run/`.

Salida verificada:

```
▸ Docker: db + minio
▸ Esperando a que Postgres esté healthy…
▸ Migraciones (alembic upgrade head)
▸ Frontend: vite
▸ Backend: uvicorn (CORS ← http://localhost:5173)

▸ Listo:
   Dashboard  http://localhost:5173
   API        http://localhost:8000   (docs en /docs)
   MinIO      http://localhost:9001   (minioadmin/minioadmin)
   Postgres   localhost:5435          (holter/holter)
```

Los otros dos subcomandos:

```bash
.claude/skills/run-holter/stack.sh status     # qué está up y en qué puerto
.claude/skills/run-holter/stack.sh down       # baja back y front, deja la db viva
.claude/skills/run-holter/stack.sh down --db  # además para los containers
```

## Camino manual (paso a paso)

Mismos comandos, a mano, cada uno en su terminal. Útil para ver los logs en
vivo o levantar solo una parte.

**1. Base de datos y S3 local** — siempre primero:

```bash
docker compose up -d db minio
```

**2. Migraciones** (desde `back/`):

```bash
cd back && uv sync && uv run alembic upgrade head
```

Con la db al día imprime solo las dos líneas de contexto de Alembic y ninguna
línea `Running upgrade`. Para confirmar la revisión aplicada:

```bash
cd back && uv run alembic current
```

**3. Backend** (desde `back/`, queda en foreground con hot reload):

```bash
cd back && uv run uvicorn app.main:app --reload --port 8000
```

**4. Frontend** (desde `front/`, queda en foreground con HMR):

```bash
cd front && npm run dev
```

La primera vez, o después de tocar `package.json`: `cd front && npm install`.
**Nunca pnpm.**

## Verificar que quedó bien

```bash
curl -s http://localhost:8000/health        # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5173/   # 200
```

Un `GET /auth/me` sin cookie devuelve `401 {"code":"UNAUTHORIZED"}` — eso es
lo correcto, no un error del arranque.

## Gotchas

- **Si el puerto 5173 está ocupado, la app queda rota sin decir nada.** Vite no
  falla: se corre solo a 5174 e imprime `Port 5173 is in use, trying another
  one...`. Pero el backend habilita CORS únicamente para el origen que tenga en
  `FRONTEND_URL` (default `http://localhost:5173`), así que desde 5174 **toda**
  llamada a la API muere con `net::ERR_FAILED` y el dashboard se ve vacío o
  colgado en el login. Pasó de verdad en esta máquina: había otro proyecto
  escuchando en 5173.
  - `stack.sh up` lo resuelve solo (lee el puerto de Vite y se lo pasa a
    uvicorn).
  - A mano: liberá el 5173 (`lsof -nP -iTCP:5173 -sTCP:LISTEN`) o arrancá el
    backend con el origen correcto:
    `cd back && FRONTEND_URL=http://localhost:5174 uv run uvicorn app.main:app --reload --port 8000`

- **Postgres escucha en 5435, no en 5432.** El `docker-compose.yml` mapea
  `5435:5432` para no chocar con otro Postgres local. El README dice 5432 —
  está desactualizado; `back/.env.example` tiene el valor bueno.
  Para entrar por psql (no está en el PATH del host):
  `docker exec tesis-ecg-mono-db-1 psql -U holter -d holter -c '\dt'`

- **`S3_ENDPOINT_URL=http://minio:9000` solo resuelve dentro de Docker.**
  Corriendo el backend nativo, `minio` no es un hostname válido en la Mac. El
  arranque y todo lo que no toque S3 anda igual; si vas a probar subida o
  descarga de batches de ECG, cambiá esa línea de `back/.env` a
  `http://localhost:9000`.

- **`docker compose up` (sin argumentos) levanta un stack paralelo.** El compose
  de la raíz también define servicios `back` y `front` en contenedor, que pelean
  por los puertos 8000 y 5173 con los procesos nativos. Para el flujo de esta
  skill levantá **solo** `db` y `minio`.

- **Hay dos compose files.** `docker-compose.yml` en la raíz (db, minio, back,
  front) y `back/docker-compose.yml` (db, minio, api). Definen los mismos
  puertos, así que no se pueden usar los dos a la vez. Esta skill usa siempre
  el de la raíz.

- **El login pasa por Auth0 real** (tenant `holter-ecg-dev`, flujo ROPG). No hay
  usuario de prueba semilla: para entrar al dashboard hace falta una cuenta que
  exista en Auth0. Para darle rol admin a una cuenta ya existente:
  `cd back && uv run python -m app.scripts.seed_admin --email <mail>`

- **Matar uvicorn requiere insistir.** Con `--reload` hay un proceso reloader
  padre que sobrevive a `pkill -f "uvicorn app.main:app"` y sigue reteniendo el
  8000. `stack.sh down` ya mata el árbol; a mano:
  `kill -9 $(lsof -t -nP -iTCP:8000 -sTCP:LISTEN)`

## Troubleshooting

| Síntoma | Causa / arreglo |
|---|---|
| `Port 5173 is in use, trying another one...` en el log de Vite | Ver el primer Gotcha. Liberá el puerto o ajustá `FRONTEND_URL`. |
| El dashboard carga pero se queda en el login / sin datos, y en la consola del navegador hay `net::ERR_FAILED http://localhost:8000/...` | CORS: el origen de Vite no coincide con `FRONTEND_URL` del backend. |
| `docker compose` falla con `Cannot connect to the Docker daemon` | Docker Desktop cerrado. Abrilo y reintentá. |
| El backend arranca y muere en el `lifespan` | La db no está arriba o todavía no está `healthy`. `docker compose ps db` y esperá el `(healthy)`. |
| `address already in use` en el 8000 | Quedó un reloader colgado: `kill -9 $(lsof -t -nP -iTCP:8000 -sTCP:LISTEN)` |
| `relation "..." does not exist` | Faltan migraciones: `cd back && uv run alembic upgrade head` |

## Archivos de la skill

- `stack.sh` — el script de arranque/parada descripto arriba.
- `.gitignore` — deja fuera del repo `.run/` (logs y pidfiles).
