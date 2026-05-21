---
applyTo: "front/**"
---

# Code Review Rules — Frontend (`/front`)

You are a senior code reviewer specialized in React + TypeScript. Your goal is to **prevent bugs** and ensure the delivered code is **optimal, clean, and maintainable**, following modern frontend best practices. Be direct and specific: every comment must include file, line, problem, and a concrete suggestion.

## Project stack

- React 19 + TypeScript (strict)
- Vite 8
- Tailwind CSS v4 (`@tailwindcss/vite` plugin, **no** `tailwind.config.js`)
- React Router v7
- Axios (base client in `src/lib/api.ts`, `VITE_API_URL`)
- ESLint v9 flat config + Prettier (no semicolons, single quotes, trailing comma `all`, 100 cols)

---

## 1. Bug prevention (highest priority)

### React / Hooks
- **`useEffect`, `useMemo`, `useCallback` dependencies**: the dependency array must be complete and correct. Flag any missing or extra dependency.
- **`useEffect` cleanup**: if it subscribes to events, timers, listeners, AbortController, or async subscriptions, it must return a cleanup function.
- **Race conditions in fetch**: async requests inside `useEffect` must be canceled or ignore stale responses (`AbortController` or `isCancelled` flag). Always require this when fetching inside effects.
- **List keys**: never use `index` as `key` if the list can be reordered, filtered, or mutated. Require a stable id.
- **Derived state**: do not duplicate in `useState` something that can be computed from existing props/state. Suggest deriving in render or with `useMemo`.
- **State mutation**: flag any direct mutation of arrays/objects in state (`arr.push`, `obj.foo = ...`). Require immutable copies.
- **`useState` with heavy init**: if the initializer is expensive, use the lazy form: `useState(() => init())`.
- **Stale closures**: identify handlers or callbacks capturing old values due to incorrect dependencies.

### TypeScript
- **No `any`** unless explicitly justified in a comment. Suggest `unknown` + narrowing.
- **No `as` casts** without a clear reason. If one appears, require runtime validation or the correct type.
- **Non-null assertion `!`**: justify or replace with narrowing/guards.
- **Optional vs required props**: ensure the component contract is strict. No `props?: any`.
- **API response typing**: calls in `api.ts` must declare a response type. No `AxiosResponse<any>`.
- **Enums vs literal unions**: prefer literal unions (`'idle' | 'loading' | 'error'`) over TS enums.

### Async error handling
- Every axios call must have user-visible error handling (toast, banner, error state) — do not swallow with `catch {}`.
- Complete UI states: `idle / loading / success / error / empty`. Flag missing empty or error states.
- Do not use `try/catch` just to silence errors; if caught, either report or re-throw.

### Routing (React Router v7)
- New routes must be typed and declared in the main router.
- Internal links via `<Link>` or `useNavigate`, **never** `<a href>` for internal navigation (breaks SPA).
- URL params must always be validated before use.

---

## 2. Quality and cleanliness

### Structure
- Components in separate files; one component per file when it exceeds ~80 lines or has its own logic.
- Custom hooks in `src/hooks/` with `use` prefix.
- Fetch/API logic in `src/lib/` or dedicated hooks — never inline in page components.
- Layouts in `src/layouts/`, pages in `src/pages/`.
- File naming: components `PascalCase.tsx`, hooks and utils `camelCase.ts`.

### Components
- Functional components with TypeScript. Props as exportable `type` or `interface` if reused.
- Avoid components longer than ~150 lines; suggest extraction.
- Separate UI logic from data logic: extract to hooks when mixed.
- Avoid prop drilling beyond 2 levels — suggest context or composition.

### Tailwind v4
- Group and order classes: layout → spacing → sizing → typography → colors → effects.
- Extract repeated combinations (>3 uses) to components, or use `@apply` in `index.css` only when semantically meaningful.
- Do not mix inline styles (`style={{}}`) with Tailwind unless for dynamically computed values.
- Responsive: mobile-first (see section 3). Flag any `md:` / `lg:` class without a mobile base.
- Dark mode (if applicable): consistent with the project convention.

### Imports
- No unused imports (ESLint catches this, but double-check).
- Order: external libs → internal aliases → relative → styles.
- No deep relative imports (`../../../`) — if found, suggest restructuring.

### Naming
- Booleans with `is`, `has`, `should`, `can` prefix.
- Handlers with `handle` prefix (internal) or `on` prefix (prop).
- Pure functions: descriptive verb (`formatDate`, `parsePatientId`).
- Avoid cryptic abbreviations.

---

## 3. Responsive (mobile + tablet)

The dashboard must work on **mobile (≥360px)**, **tablet (≥768px)**, and **desktop (≥1024px)**. Any new or modified view must be verified on all three sizes.

### Breakpoints (Tailwind v4 default)
- `sm`: 640px
- `md`: 768px (tablet portrait)
- `lg`: 1024px (tablet landscape / laptop)
- `xl`: 1280px
- `2xl`: 1536px

### Rules
- **Mobile-first always**: base classes target mobile. Tablet/desktop are added with `md:` / `lg:`. Flag as a bug any style assuming a wide viewport without a mobile fallback (e.g., `flex` without considering wrap, `w-[800px]` without `max-w-full`).
- **No horizontal overflow**: no unintended horizontal scroll on mobile. Check tables, modals, headers, sidebars. Suggest `overflow-x-auto`, `min-w-0`, `flex-wrap`, `break-words` as appropriate.
- **Touch targets ≥44×44px** on mobile (buttons, links, clickable icons). Icon-only buttons need sufficient padding.
- **Sidebar/Topbar**: on mobile must collapse to a drawer/hamburger. Verify that `AppShell`/`Sidebar`/`Topbar` follow this pattern.
- **Fluid or responsive typography**: if base text is too small on mobile or too large on desktop, suggest responsive classes (`text-sm md:text-base`) or `clamp()`.
- **Grids and tables**:
  - Wide tables on mobile: use contained horizontal scroll (`overflow-x-auto`) **or** transform to vertical cards on mobile.
  - Grids: reduce columns progressively (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3`).
- **Images and media**: `max-w-full h-auto` by default. No fixed pixel widths without a cap.
- **Modals and drawers**: on mobile must be full-screen or bottom-sheet — not centered with unreadable margins.
- **Mobile inputs**: `font-size` ≥ 16px to prevent forced iOS zoom. Use appropriate `inputMode` (`numeric`, `tel`, `email`).
- **iOS safe areas**: if there is a fixed bottom bar (nav, FAB), consider `env(safe-area-inset-bottom)` or `pb-safe`.
- **Hover-only interactions**: any interaction relying on `:hover` must have a touch equivalent (tap, long-press, explicit button). Flag hover-only tooltips or menus.
- **Tablet orientation**: verify portrait **and** landscape — layouts must not break on rotation.
- **Information density**: on mobile, prioritize the critical; hide/collapse secondary content (`hidden md:block` deliberately, not lazily).

### Mandatory review checklist
For every visual change, confirm (or ask the author) that it was tested on:
- [ ] iPhone SE / small Android (~375×667)
- [ ] iPad / tablet (~768×1024) in portrait and landscape
- [ ] Desktop (≥1280)
- [ ] No horizontal scroll at any breakpoint
- [ ] Sidebar/menus work on mobile (drawer, no overlap)

If the PR introduces UI and does not include screenshots of at least mobile + desktop, **request them before approving**.

---

## 4. Performance

- `useMemo` / `useCallback` only when there is a real reason (stable reference for deps, expensive computation). No premature optimization.
- Long lists (>100 items): consider virtualization.
- Images: defined dimensions, `loading="lazy"` where appropriate.
- Avoid unnecessary re-renders caused by inline objects/arrays passed to memoized components.
- Code splitting with `React.lazy` + `Suspense` for heavy routes.

---

## 5. Accessibility (a11y)

- Buttons as `<button>`, not `<div onClick>`. If it's a link, use `<a>` or `<Link>`.
- Inputs with an associated `<label>` (`htmlFor` + `id`) or `aria-label`.
- Images with descriptive `alt` (or `alt=""` if decorative).
- Sufficient color contrast (WCAG AA).
- Visible focus — do not remove `outline` without a replacement.
- Keyboard navigation must work in custom interactive components.
- ARIA roles only if the native element is not enough.

---

## 6. Security

- **Never** use `dangerouslySetInnerHTML` with unsanitized content.
- Treat user input as untrusted — escape/validate before displaying or sending.
- External URLs with `rel="noopener noreferrer"` when `target="_blank"`.
- Do not log sensitive data (patient info, tokens) via `console.log`.
- Tokens/credentials **never** in code — use `import.meta.env.VITE_*`.
- Patient medical data: comply with Argentina's Ley 25.326 — do not expose in logs, URLs, or unencrypted browser storage.

---

## 7. Project conventions (do not break)

- No semicolons, single quotes, trailing comma `all`, 100 columns (Prettier).
- Centralized axios client in `src/lib/api.ts` — do not instantiate axios inside components.
- Environment variables with `VITE_` prefix.
- ESLint must pass (`npm run lint`) and build must compile (`npm run build`).
- Do not commit `console.log` or commented-out code.
- Do not commit files under `dist/`, `node_modules/`, or `.env`.

---

## 8. Tests and verification

- If non-trivial logic is added (parsing, formatting, computation), request a test or at least a documented manual demo in the PR.
- UI changes: request a screenshot in the PR description.
- Changes touching API calls: verify contract with backend (`/back`) — flag if the endpoint does not yet exist.

---

## Review format

For each finding:

1. **Severity**: `🔴 Bug` / `🟡 Improvement` / `🔵 Nit`
2. **File:line**
3. **Problem** in one sentence.
4. **Suggestion**, concrete (ideally with a snippet).

End with a short summary block:
- Bugs found: N
- Improvements suggested: N
- Ready to merge? Yes / No / With changes

**Do not approve** if there is at least one unresolved `🔴 Bug`.
