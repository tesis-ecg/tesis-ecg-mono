---
name: linear-issue-sync
description: >
  Keeps Linear issue discussions and updates synchronized with real MCP state.
  Use when users reference issue IDs, ask about issue status/progress, request status/comment updates,
  or when implementation for an issue has just finished and a post-implementation sync is required.
license: MIT
metadata:
  author: tesis-ecg-mono
  version: "1.0.0"
---

# Linear Issue Sync

Use this skill whenever the user discusses Linear issues, roadmaps, sprint planning, or gives issue IDs (for example: `TESIS-16`, `TESIS-42`).

## Goal

Anchor discussion and execution to the real Linear issue state, not memory.

## Trigger Rules

Run this workflow when any of the following is true:

- The user references an issue ID pattern like `[A-Z]+-\d+`.
- The user asks about issue status, progress, blockers, acceptance criteria, or what to do next for an issue.
- The user asks to update issue status, comments, labels, assignee, or links.
- An implementation flow (for example `linear-issue-pull`) has just finished and needs an automatic Linear update.

## Required Workflow

1. Fetch issue context first

- Call `mcp__linear__get_issue` for each referenced issue ID.
- If no explicit issue ID is provided, find candidate issues with `mcp__linear__list_issues` using `query` and project/team filters from user context.

2. Verify before proposing work

- Confirm current `status`, `priority`, `assignee`, `project`, and acceptance criteria from `description`.
- Do not assume state from prior conversation.

3. Align implementation to acceptance criteria

- Map completed code/test changes to issue tasks and acceptance criteria.
- Explicitly list gaps if criteria are not fully met.

4. Update Linear when requested or when clearly implied by user intent

- Status change: `mcp__linear__save_issue` with `state`.
- Progress note: `mcp__linear__save_comment` with concise implementation evidence and validation results.
- Keep comments factual and audit-friendly (what changed, where, how validated).

5. Confirm outcome to user

- Return a compact summary: issue -> old state -> new state -> comment posted (yes/no).

## Comment Template

Use this structure for implementation updates:

- Scope delivered
- Files/modules touched
- Validation performed
- Remaining follow-ups (if any)

## Guardrails

- Never mark `Done` if acceptance criteria are not met.
- Never update unrelated issues.
- If multiple issues are mentioned, handle each explicitly and report per-issue results.
- If project/team is ambiguous, fetch and confirm before editing.
