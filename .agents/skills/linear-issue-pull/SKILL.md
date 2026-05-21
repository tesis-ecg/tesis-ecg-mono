---
name: linear-issue-pull
description: >
  Start implementation from a Linear issue key (for example TESIS-123) by pulling fresh issue data,
  creating and switching to a working branch, and entering a planning-first implementation flow.
  Use when the user asks to start work from an issue ID, says "issue TESIS-123", or requests branch setup from Linear.
---

# Linear Issue Pull

Use this skill when the user provides an issue key (for example `TESIS-123`) and wants to begin implementation.

## Inputs

- Required: issue key in format `[A-Z]+-\d+` (example: `TESIS-456`).
- Optional: branch type override (`feat` or `fix`).

## Workflow

1. Resolve the issue from Linear first

- Call `mcp__linear__get_issue` using the issue key.
- Read at minimum: `identifier`, `title`, `description`, `state`, `priority`, and labels.

2. Determine branch type

- Use `fix` when the issue title or labels indicate a bug/hotfix/regression.
- Otherwise default to `feat`.
- If the user explicitly asks for `feat` or `fix`, respect the explicit request.

3. Build a deterministic branch name

- `issueName`: example: `<TEAM_NAME>-123`.
- `smallDescription`: 2 to 5 extra keywords from the issue scope in kebab-case.
- Final format: `{feat|fix}/{issueName}-{smallDescription}`.
- Keep only `[a-z0-9-]`, collapse duplicate dashes, and cap total branch length to 60 chars.

4. Create and switch branch

- Run `git checkout -b <branch>`.
- If branch exists already, run `git checkout <branch>`.
- Confirm active branch with `git branch --show-current`.

5. Enter planning-first mode immediately

- Switch to a plan-mode behavior before editing files.
- If issue requires backend work, use 'backend-skill'.
- Start with an implementation prompt tied to the issue, for example:
  `Implement <ISSUE_KEY>: <ISSUE_TITLE>. Build a minimal, testable solution and list acceptance criteria coverage.`
- Produce a concrete step plan, then execute.

6. Auto-run Linear sync after implementation completes

- After code changes and validation are complete, immediately invoke `linear-issue-sync`.
- Pass the same issue key and a concise implementation summary (scope delivered, files touched, validation run, remaining follow-ups).
- Use `linear-issue-sync` to post/update Linear status and comment before considering the task complete.

## Trigger Examples

- `issue TESIS-123`
- `start TESIS-123`
- `pull TESIS-123 and implement`

## Guardrails

- Do not use stale issue context; always fetch from Linear first.
- Do not create a branch until a valid issue key is confirmed.
- Do not skip planning-first behavior.
- Do not finish without running the post-implementation `linear-issue-sync` handoff.
