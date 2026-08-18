#!/usr/bin/env bash
# Levanta el stack local en el orden correcto: db+minio en Docker, migraciones,
# backend (uvicorn) y frontend (vite), ambos nativos en la Mac.
#
#   .claude/skills/run-holter/stack.sh up       # arranca todo y espera a que responda
#   .claude/skills/run-holter/stack.sh status   # qué está corriendo y en qué puerto
#   .claude/skills/run-holter/stack.sh down     # baja back y front (deja la db viva)
#   .claude/skills/run-holter/stack.sh down --db # además para los containers
#
# El orden importa: uvicorn abre una conexión a Postgres en el lifespan y muere
# al arrancar si la db no está lista. Y vite se levanta ANTES que uvicorn para
# poder leer el puerto real que consiguió y pasárselo al backend como
# FRONTEND_URL — si no, CORS bloquea todas las llamadas (ver Gotchas del SKILL).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
RUN="$HERE/.run"
mkdir -p "$RUN"

BACK_LOG="$RUN/back.log"
FRONT_LOG="$RUN/front.log"
BACK_PID="$RUN/back.pid"
FRONT_PID="$RUN/front.pid"

log() { printf '\033[1;34m▸\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m✗\033[0m %s\n' "$*" >&2; exit 1; }

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

up_db() {
  log "Docker: db + minio"
  docker info >/dev/null 2>&1 || die "Docker no está corriendo. Abrí Docker Desktop."
  (cd "$ROOT" && docker compose up -d db minio >/dev/null)

  log "Esperando a que Postgres esté healthy…"
  for _ in $(seq 1 60); do
    if docker compose -f "$ROOT/docker-compose.yml" ps db --format '{{.Health}}' 2>/dev/null \
        | grep -q healthy; then
      return 0
    fi
    sleep 1
  done
  die "Postgres no llegó a healthy. Mirá: docker compose logs db"
}

migrate() {
  log "Migraciones (alembic upgrade head)"
  (cd "$ROOT/back" && uv sync --quiet && uv run alembic upgrade head)
}

up_front() {
  if alive "$FRONT_PID"; then log "Front ya corriendo (pid $(cat "$FRONT_PID"))"; return; fi
  log "Frontend: vite"
  : > "$FRONT_LOG"
  # Doble fork + </dev/null: el subshell externo muere enseguida y vite queda
  # huérfano (reparentado a launchd). Sin esto el server sigue siendo hijo del
  # script, y quien lo invoque esperando a que termine se cuelga para siempre.
  ( (cd "$ROOT/front" && npm run dev >>"$FRONT_LOG" 2>&1 </dev/null & echo $! > "$FRONT_PID") & )

  # Vite imprime el puerto recién cuando está listo; y si 5173 está ocupado se
  # corre solo a 5174/5175 sin fallar.
  for _ in $(seq 1 60); do
    if grep -qE 'Local:.*http://localhost:[0-9]+' "$FRONT_LOG"; then return 0; fi
    sleep 0.5
  done
  die "Vite no arrancó. Mirá: $FRONT_LOG"
}

front_url() {
  grep -oE 'http://localhost:[0-9]+' "$FRONT_LOG" | head -1
}

up_back() {
  if alive "$BACK_PID"; then log "Back ya corriendo (pid $(cat "$BACK_PID"))"; return; fi
  local origin; origin="$(front_url)"
  log "Backend: uvicorn (CORS ← $origin)"
  : > "$BACK_LOG"
  ( (cd "$ROOT/back" && FRONTEND_URL="$origin" \
      uv run uvicorn app.main:app --reload --port 8000 >>"$BACK_LOG" 2>&1 </dev/null \
      & echo $! > "$BACK_PID") & )

  for _ in $(seq 1 60); do
    if curl -sf http://localhost:8000/health >/dev/null; then return 0; fi
    sleep 1
  done
  die "El backend no respondió /health. Mirá: $BACK_LOG"
}

cmd_up() {
  up_db
  migrate
  up_front
  up_back
  echo
  log "Listo:"
  echo "   Dashboard  $(front_url)"
  echo "   API        http://localhost:8000   (docs en /docs)"
  echo "   MinIO      http://localhost:9001   (minioadmin/minioadmin)"
  echo "   Postgres   localhost:5435          (holter/holter)"
  echo "   Logs       $BACK_LOG · $FRONT_LOG"
}

cmd_status() {
  alive "$BACK_PID"  && echo "back   up   pid $(cat "$BACK_PID")"  || echo "back   down"
  alive "$FRONT_PID" && echo "front  up   pid $(cat "$FRONT_PID") $(front_url 2>/dev/null)" \
                     || echo "front  down"
  (cd "$ROOT" && docker compose ps --format '{{.Service}}\t{{.Status}}' 2>/dev/null) || true
}

cmd_down() {
  for f in "$BACK_PID" "$FRONT_PID"; do
    if alive "$f"; then
      # uvicorn --reload y vite arrancan hijos: matar el grupo entero.
      pkill -P "$(cat "$f")" 2>/dev/null || true
      kill "$(cat "$f")" 2>/dev/null || true
      sleep 1
      kill -9 "$(cat "$f")" 2>/dev/null || true
    fi
    rm -f "$f"
  done
  log "back y front detenidos"
  if [ "${1:-}" = "--db" ]; then
    (cd "$ROOT" && docker compose stop db minio >/dev/null)
    log "containers detenidos"
  fi
}

case "${1:-up}" in
  up)     cmd_up ;;
  status) cmd_status ;;
  down)   cmd_down "${2:-}" ;;
  *)      die "uso: stack.sh [up|status|down [--db]]" ;;
esac
