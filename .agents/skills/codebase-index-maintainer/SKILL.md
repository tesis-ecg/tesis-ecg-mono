---
name: codebase-index-maintainer
description: >
  Create or update an agent-facing CODEBASE_INDEX.md for a repository. Use when
  invoked explicitly, when no CODEBASE_INDEX.md exists for a codebase, or after
  significant structural changes to apps, packages, routes, modules, data models,
  entrypoints, integrations, or request flows.
---

# Codebase Index Maintainer

Create or update a concise, evidence-based graph index for the current codebase.

## When to use

- The user explicitly asks to create, update, refresh, rebuild, or repeat the codebase index task.
- A discovery/analysis task finds no `CODEBASE_INDEX.md` and the user wants an index.
- A significant structural change was performed, including new/removed workspaces, backend modules, frontend route trees, API clients, controllers, database models, queues, integrations, or major cross-app flows.

## Target file

Prefer `docs/CODEBASE_INDEX.md`. If the repository has no `docs/` directory and creating one is inappropriate, use root `CODEBASE_INDEX.md`.

## Workflow

1. Check whether `docs/CODEBASE_INDEX.md` or `CODEBASE_INDEX.md` already exists.
2. If it exists, read it first and update only the sections affected by current evidence.
3. If it does not exist, create a new index using the structure below.
4. Gather evidence from manifests, entrypoints, route declarations, API clients, backend controllers/services/repositories, schema files, queue/workers, integration clients, and important docs.
5. Write concise summaries with file-path evidence. Do not produce a raw import dump.
6. Label inferred or uncertain relationships explicitly.
7. Re-read the final Markdown and remove fluff, stale claims, and low-value folder listings.

## Required sections

Use these headings unless the repo has a strong reason to vary:

1. `# Codebase Index`
2. `## How To Use This Index`
3. `## Monorepo Overview` or `## Repository Overview`
4. `## Workspace Graph` when applicable
5. `## Backend Graph` when applicable
6. `## Frontend Graph` when applicable
7. `## Cross-App Request Flows` when applicable
8. `## Data Model Graph` when applicable
9. `## External Integrations Graph` when applicable
10. `## Important Docs`
11. `## Suggested Reading Paths`
12. `## Unknowns / Inferred Edges`

## Content standards

- Optimize for future agents choosing what to read first.
- Include route/page/API/controller ownership where evidence exists.
- Include backend module or service ownership where evidence exists.
- Include data model summaries from schema/migration/model files.
- Include external systems and queue/background processors.
- Include concrete end-to-end flow traces for important workflows.
- Keep generated/artifact directories out of the index.
- Do not claim relationships from filenames alone; read enough code to confirm behavior.

## Update threshold

Update the index after structural changes that would make an agent open the wrong file or miss an important owner. Small internal implementation edits usually do not require an index update.
