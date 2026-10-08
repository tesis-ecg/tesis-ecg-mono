#!/usr/bin/env bash
# Runs one project hook with the same command string under Claude Code and Codex.
#
# Claude Code sets CLAUDE_PROJECT_DIR and runs hooks from the project dir; Codex
# only guarantees cwd = session dir and passes no project variable. This script
# derives the project root from its own location so both hosts agree.
#
# When ~/.agents/hooks/project_hook_adapter.py exists (personal shared Claude/Codex
# setup) the hook runs through it so Codex payloads are normalized to the Claude
# shape. On any other machine the hook runs directly — nothing personal is required.
# The formatter needs the adapter under Codex (it maps apply_patch to per-file Edit events).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
CMD="${1:?usage: run-hook.sh '<command>'}"
ADAPTER="${HOME}/.agents/hooks/project_hook_adapter.py"

if [ -f "$ADAPTER" ]; then
  if [ -x /opt/homebrew/bin/python3 ]; then PY=/opt/homebrew/bin/python3; else PY="$(command -v python3)"; fi
  exec "$PY" "$ADAPTER" "$ROOT" "$CMD"
fi

export CLAUDE_PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$ROOT}"
cd "$ROOT" && exec bash -c "$CMD"
