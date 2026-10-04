#!/bin/sh
# Dev container entrypoint. node_modules lives in an anonymous volume that survives image
# rebuilds, so it goes stale when package-lock.json changes on the host. Reinstall when the
# bind-mounted lockfile no longer matches the one node_modules was installed from.
set -e

HASH_FILE=node_modules/.lockfile.sha256

if ! sha256sum -c "$HASH_FILE" >/dev/null 2>&1; then
  echo "package-lock.json changed — reinstalling dependencies…"
  # --ignore-scripts skips the host-only husky `prepare` hook from the mounted package.json.
  npm ci --ignore-scripts --no-audit --no-fund
  npm rebuild esbuild
  sha256sum package-lock.json > "$HASH_FILE"
fi

exec "$@"
