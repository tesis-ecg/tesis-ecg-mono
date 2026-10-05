import { existsSync, mkdirSync } from 'node:fs'
import { dirname } from 'node:path'
import { expect, request, test as setup } from '@playwright/test'
import { AUTH_FILE, requireEnv } from './support/session'

/**
 * Is the saved session still good for this user? Login is rate limited (5 attempts
 * per 15 minutes per account and IP), and the session lasts an hour, so a re-run
 * while iterating on a spec must not sign in again. CI never has a saved session.
 */
async function savedSessionStillWorks(baseURL: string | undefined, email: string) {
  if (!existsSync(AUTH_FILE)) return false
  const api = await request.newContext({ baseURL, storageState: AUTH_FILE })
  try {
    const res = await api.get('/api/auth/me')
    return res.ok() && (await res.json()).email === email.toLowerCase()
  } finally {
    await api.dispose()
  }
}

setup('sign in through the login form and save the session', async ({ page, baseURL }) => {
  const email = requireEnv('E2E_EMAIL')
  const password = requireEnv('E2E_PASSWORD')
  if (await savedSessionStillWorks(baseURL, email)) return

  await page.goto('/login')
  const emailField = page.getByLabel('Email', { exact: true })
  const passwordField = page.getByLabel('Contraseña', { exact: true })
  await emailField.fill(email)
  await passwordField.fill(password)

  const loginResponse = page.waitForResponse(
    (res) => new URL(res.url()).pathname === '/api/auth/login',
  )
  await page.getByRole('button', { name: 'Ingresar' }).click()
  await loginResponse
  // The submit already captured the values. If sign-in fails, Playwright's failure
  // snapshot (error-context.md, uploaded as a CI artifact of a public repository)
  // would otherwise write the filled-in credentials into a file.
  await emailField.clear()
  await passwordField.clear()

  const sidebar = page.getByRole('navigation', { name: 'Navegación principal' })
  const error = page.getByRole('alert')
  await expect(sidebar.or(error)).toBeVisible()
  if (await error.isVisible()) {
    throw new Error(
      `Sign-in failed with "${(await error.textContent())?.trim()}". Either the Auth0 test ` +
        'user does not exist / has another password, or it has no active row in the database ' +
        '(python -m app.scripts.seed_e2e_user).',
    )
  }

  mkdirSync(dirname(AUTH_FILE), { recursive: true })
  await page.context().storageState({ path: AUTH_FILE })
})
