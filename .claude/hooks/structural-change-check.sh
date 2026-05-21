#!/bin/bash
# Checks if any structural project changes were made (compared to HEAD).
# Only prints a reminder if relevant files were modified.

STRUCTURAL_PATTERNS=(
  "apps/frontend/src/pages/"
  "apps/frontend/src/components/"
  "apps/frontend/src/api/"
  "apps/frontend/src/App.tsx"
  "apps/backend/src/modules/"
  "apps/backend/prisma/schema/"
  "apps/backend/prisma/migrations/"
  "package.json"
  "turbo.json"
)

# Get list of changed files (staged + unstaged + untracked compared to HEAD)
CHANGED=$(git -C "$(git rev-parse --show-toplevel)" diff --name-only HEAD 2>/dev/null)
CHANGED+=$'\n'
CHANGED+=$(git -C "$(git rev-parse --show-toplevel)" diff --name-only 2>/dev/null)
CHANGED+=$'\n'
CHANGED+=$(git -C "$(git rev-parse --show-toplevel)" ls-files --others --exclude-standard 2>/dev/null)

MATCHED=""
for pattern in "${STRUCTURAL_PATTERNS[@]}"; do
  if echo "$CHANGED" | grep -q "$pattern"; then
    MATCHED="$MATCHED\n  - $pattern"
  fi
done

if [ -n "$MATCHED" ]; then
  echo "REMINDER: Structural changes detected in:$MATCHED"
  echo "Please update CLAUDE.md at the project root to reflect the current state."
fi
