---
name: feature-planning
description: "Plan and implement features, fixes, refactors, and technical changes through an interactive discovery and approval workflow. Requires Plan Mode before starting."
---

# Feature Planning & Implementation

Plan and implement features, fixes, refactors, and technical changes using a question-first planning workflow.

## CRITICAL RULES

### 1. Plan Mode is mandatory

If Plan Mode is **not active**:

- Do not inspect the codebase.
- Do not ask questions about the feature.
- Do not propose solutions.
- Do not write code.
- Do not create a plan.

Stop immediately and tell the user:

> Plan Mode must be enabled to use this workflow. Activate Plan Mode and send the feature/fix again.

### 2. Planning comes before implementation

When Plan Mode is active, **do not implement yet**.

First:

- Understand the request.
- Inspect the relevant codebase.
- Find ambiguities and missing requirements.
- Identify important edge cases.
- Challenge questionable assumptions.
- Suggest better approaches when appropriate.
- Ask the user the necessary questions.
- Produce a concrete implementation plan.

# PLANNING PHASE

Treat the user's request as a starting point, not necessarily a complete specification.

## 1. Understand the goal

Determine:

- What problem is being solved?
- What should the user/system experience?
- What existing behavior must remain unchanged?
- What parts of the application may be affected?

Do not make implementation decisions before understanding the desired behavior.

## 2. Inspect the relevant code

After understanding the request, inspect only the parts of the repository relevant to it.

Look for:

- Existing similar functionality.
- Components/hooks/services that can be reused.
- API and database patterns.
- State-management patterns.
- Validation and error handling.
- Authentication/authorization.
- Existing tests.
- Existing abstractions.

Prefer extending existing patterns over introducing new ones.

## 3. Think beyond the request

Proactively identify relevant:

- Edge cases.
- Empty/loading/error states.
- Invalid or missing data.
- Duplicate actions.
- Concurrent requests/race conditions.
- Retry/cancellation behavior.
- Existing data and backward compatibility.
- Security implications.
- Performance implications.
- Testing requirements.

Do not turn this into a generic checklist. Only raise issues relevant to the feature.

## 4. Ask high-value questions

Ask questions when the answer could materially change:

- Product behavior.
- UX.
- Architecture.
- API/database design.
- Security.
- Performance.

Ask iteratively rather than dumping a large questionnaire.

When meaningful alternatives exist, present them:

> **Option A:** ...
>
> **Option B:** ...
>
> **Recommendation:** B, because ...

Challenge the user's proposed approach when there is a clearly better or simpler solution, but respect the final decision.

For minor details, follow existing project conventions or make a reasonable assumption instead of asking.

## 5. Re-evaluate after answers

After each user response:

- Update your understanding.
- Reconsider previous assumptions.
- Check whether new edge cases appeared.
- Ask the next most important question if necessary.

Stop asking questions once the requirements are sufficiently clear.

---

# FINAL PLAN

When discovery is complete, produce an implementation-ready plan containing:

### Goal

What will be achieved.

### Requirements

The behavior agreed upon with the user.

### Approach

The recommended implementation and why.

### Alternatives

Only meaningful alternatives that were considered.

### Changes

Relevant files/modules/components/services and what changes in each.

### Data/API

Database, API, state, or contract changes if applicable.

### Edge Cases

Important cases and their expected behavior.

### Testing

Specific tests that should be added or updated.

### Implementation Steps

An ordered sequence of concrete implementation tasks.

Then **STOP and wait for approval**.

---

# IMPLEMENTATION PHASE

After explicit approval:

1. Re-read the approved plan.
2. Implement it using existing project conventions.
3. Keep the changes focused on the requested scope.
4. Avoid unnecessary abstractions, dependencies, or refactors.
5. Add/update relevant tests.
6. Run appropriate tests, type checks, linting, and/or build checks.
7. Fix issues caused by the implementation.

## Plan changes during implementation

If implementation reveals a **significant problem with the approved approach**:

- Stop.
- Explain what was discovered.
- Explain why the approved plan is insufficient.
- Propose the revised approach.
- Ask for approval before making the significant change.

Do not silently change an important architectural or product decision.

Minor implementation details may be adjusted without approval when they do not change the intended behavior or architecture.

---

# ENGINEERING PRINCIPLES

Apply these throughout the workflow:

- Prefer the simplest robust solution.
- Reuse existing code and architecture.
- Avoid unnecessary abstractions and dependencies.
- Do not overengineer.
- Keep changes scoped.
- Validate user/external input.
- Handle relevant errors and edge cases.
- Consider security and performance when applicable.
- Do not modify unrelated code.

---

# FINAL RESPONSE AFTER IMPLEMENTATION

Summarize:

- What was implemented.
- Important decisions.
- Tests/checks that were actually run.
- Any relevant remaining notes.

Never claim that a test or check passed unless it was actually run.