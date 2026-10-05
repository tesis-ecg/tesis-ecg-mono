import { mkdirSync, writeFileSync } from 'node:fs'
import { join, relative, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, type Locator } from '@playwright/test'

const E2E_ROOT = fileURLToPath(new URL('..', import.meta.url))

/** Inside Playwright's output dir, so every run starts empty. CI uploads it as an artifact. */
export const PR_SCREENSHOT_DIR = fileURLToPath(
  new URL('../../test-results/pr-screenshots', import.meta.url),
)

/**
 * Captures what a UI change looks like, for the PR description. After a green
 * Playwright check, CI puts the screenshots from every spec the PR added or
 * changed in a "Screenshots" section of the PR body (.github/workflows/playwright-report.yml).
 *
 * Call it at the moment the change is visible, with a short caption saying what
 * it shows. It takes a locator, never the whole page: capture the element that
 * changed (the dialog, the panel, the table). The repository is public, so the
 * images are too: nothing but the demo seed's synthetic data may be on screen.
 * Locally it only writes files under test-results/.
 */
export async function prScreenshot(target: Locator, caption: string): Promise<void> {
  const spec = relative(E2E_ROOT, test.info().file).split(sep).join('/')
  const slug = `${spec.replace(/\.spec\.ts$/, '')}-${caption}`
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 100)

  mkdirSync(PR_SCREENSHOT_DIR, { recursive: true })
  await target.screenshot({
    path: join(PR_SCREENSHOT_DIR, `${slug}.png`),
    animations: 'disabled',
    caret: 'hide',
  })
  writeFileSync(join(PR_SCREENSHOT_DIR, `${slug}.json`), JSON.stringify({ caption, spec }))
}
