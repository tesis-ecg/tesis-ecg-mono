import type { Page } from '@playwright/test'

/**
 * Collects what a human would notice as "the page is broken" while a spec runs:
 * uncaught exceptions and failed API calls (5xx). Call `watchForFailures(page)`
 * before navigating, then `expect(failures).toEqual([])` at the end of the spec.
 * 4xx are left alone on purpose: permission and validation answers are normal.
 */
export function watchForFailures(page: Page): string[] {
  const failures: string[] = []
  page.on('pageerror', (error) => failures.push(`uncaught: ${error.message}`))
  page.on('response', (res) => {
    const { pathname } = new URL(res.url())
    if (pathname.startsWith('/api/') && res.status() >= 500) {
      failures.push(`${res.status()} ${res.request().method()} ${pathname}`)
    }
  })
  return failures
}
