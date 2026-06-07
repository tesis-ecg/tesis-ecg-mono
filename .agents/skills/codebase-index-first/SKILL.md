---
name: codebase-index-first
description: >
  Use when the user asks to discover, inspect, analyze, understand, search, map,
  navigate, review architecture, or investigate a codebase. Always check for a
  CODEBASE_INDEX.md first and use it as the initial navigation source when present.
---

# Codebase Index First

When a task involves discovering, analyzing, searching, navigating, or understanding a codebase, always check for an existing codebase index before broader exploration.

## Required first step

From the current repository root, check for an index in this order:

1. `docs/CODEBASE_INDEX.md`
2. `CODEBASE_INDEX.md`
3. Any file matching `**/CODEBASE_INDEX.md` outside ignored/generated directories

Use fast local search such as `test -f`, `rg --files`, or equivalent.

## If an index exists

- Read the relevant sections before searching the repo broadly.
- Treat it as the navigation map for entrypoints, module owners, route/API mappings, data models, integrations, and suggested reading paths.
- Verify facts in source before editing or making high-impact claims.
- If the index says an edge is inferred or unknown, do not promote it to fact without checking code.
- Prefer the index's suggested reading paths over starting with a raw directory walk.

## If no index exists

- Continue with normal codebase discovery.
- If the task is mostly exploratory or architecture-oriented, mention that no `CODEBASE_INDEX.md` was found and consider invoking the `codebase-index-maintainer` skill to create one.

## If the index appears stale

Use it as a starting hypothesis only. If significant structural changes are observed, invoke or follow `codebase-index-maintainer` to update it after completing the primary task.
