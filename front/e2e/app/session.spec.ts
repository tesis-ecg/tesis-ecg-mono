import { expect, test } from '@playwright/test'

// No spec here (or anywhere in e2e/app) may log out: logout bumps the user's
// session_version, which invalidates the saved session every other spec shares.

test('a signed-in user lands on the dashboard instead of bouncing to login', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('navigation', { name: 'Navegación principal' })).toBeVisible()
  await expect(page).not.toHaveURL(/\/login/)
})

test('the backend accepts the saved session', async ({ page }) => {
  const meCall = page.waitForResponse((res) => new URL(res.url()).pathname === '/api/auth/me')
  await page.goto('/')
  expect((await meCall).ok()).toBe(true)
})
