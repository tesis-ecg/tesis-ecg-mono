---
name: strategic-compact
description: >
  Suggests /compact at logical workflow boundaries to avoid arbitrary mid-task context truncation.
  Use when approaching context limits, switching task phases, or after completing a major milestone.
  Also surfaces when the suggest-compact hook fires during edit or write operations.
---

## Purpose

Automatic compaction can happen at poor boundaries, often in the middle of filenames, traces, or partially built plans. Strategic compaction keeps only the useful state.

## When to Suggest /compact

| Transition | Suggest? | Reason |
|---|---|---|
| Research -> Planning | Yes | Research context is bulky; the plan is the distilled output |
| Planning -> Implementation | Yes | Capture the plan first, then free context for edits and verification |
| Implementation -> Testing | Maybe | Keep recent code context if tests depend on it; compact if focus is shifting |
| After a failed approach | Yes | Clear dead-end reasoning before retrying |
| Debugging -> Next feature | Yes | Logs and traces pollute unrelated work |
| Mid-implementation | No | Losing filenames, variable names, and partial state is costly |

## How to Respond When the Hook Fires

When `[strategic-compact]` appears in tool feedback, surface it to the user:

> "This is a good moment to `/compact` before [next phase].  
> Run: `/compact Focus on [what you're doing next]`"

Then continue. The hook is advisory, not blocking.

## What Survives /compact

**Persists:** `AGENTS.md`, repo instructions stored on disk, current plan state or tracked task notes, git state, and all files on disk  
**Lost:** intermediate reasoning, recently read file contents, transient tool output not saved elsewhere, and conversational context that was never written down

## Best Practice Reminders

- Compact after research, before major implementation work
- Compact after the plan is captured in the plan tool, task notes, or a file
- Always add a focus hint: `/compact Focus on implementing X next`
- Save critical findings to disk before compacting if they will matter later
