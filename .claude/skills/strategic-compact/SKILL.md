---
name: strategic-compact
description: >
  Suggests /compact at logical workflow boundaries to avoid arbitrary mid-task context truncation.
  Use when approaching context limits, switching task phases, or after completing a major milestone.
  Also surfaces when the suggest-compact.js hook fires during Edit/Write operations.
---

## Purpose

Auto-compaction fires at arbitrary token boundaries — often mid-task, cutting off variable names, file paths, and partial reasoning. Strategic compaction at logical boundaries preserves the right context.

## When to Suggest /compact

| Transition | Suggest? | Reason |
|---|---|---|
| Research → Planning | Yes | Research context is bulky; the plan is the distilled output |
| Planning → Implementation | Yes | Plan lives in TodoWrite or a file; free context for code |
| Implementation → Testing | Maybe | Keep if tests reference recent code; compact if switching focus |
| After a failed approach | Yes | Clear dead-end reasoning before retrying |
| Debugging → Next feature | Yes | Debug traces pollute unrelated work |
| Mid-implementation | No | Losing variable names, file paths, partial state is costly |

## How to Respond When the Hook Fires

When `[strategic-compact]` appears in tool feedback, surface it to the user:

> "You've hit [N] tool calls. This is a good moment to `/compact` before [next phase].  
> Run: `/compact Focus on [what you're doing next]`"

Then continue with the current operation — the hook doesn't block.

## What Survives /compact

**Persists:** CLAUDE.md, TodoWrite tasks, memory files, git state, all files on disk  
**Lost:** intermediate reasoning, previously-read file contents, tool call history, verbal preferences

## Best Practice Reminders

- Compact *after* research, *before* starting implementation
- Compact *after* planning is saved to TodoWrite, not before
- Always add a focus hint: `/compact Focus on implementing X next`
- Save important insights to memory files before compacting if they'd otherwise be lost
