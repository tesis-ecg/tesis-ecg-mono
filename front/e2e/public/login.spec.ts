import { expect, test } from '@playwright/test'

import { prScreenshot } from '../support/pr-screenshot'

test('a logged-out visitor is sent to the login page and keeps where they were going', async ({
  page,
}) => {
  await page.goto('/patients')
  await expect(page).toHaveURL(/\/login\?from=%2Fpatients$/)
  await expect(page.getByRole('heading', { name: 'Te damos la bienvenida' })).toBeVisible()
})

test('the login form validates before calling the API', async ({ page }) => {
  let loginCalls = 0
  await page.route('**/api/auth/login', (route) => {
    loginCalls += 1
    return route.abort()
  })

  await page.goto('/login')
  await page.getByRole('button', { name: 'Ingresar' }).click()

  await expect(page.getByText('Ingresá tu email')).toBeVisible()
  await expect(page.getByText('Ingresá tu contraseña')).toBeVisible()
  expect(loginCalls).toBe(0)
})

test('wrong credentials show an error and keep the visitor on the login page', async ({ page }) => {
  // Stubbed: a real attempt would count against the account's login rate limit.
  await page.route('**/api/auth/login', (route) =>
    route.fulfill({
      status: 401,
      json: { detail: { code: 'INVALID_CREDENTIALS', message: 'Credenciales inválidas.' } },
    }),
  )

  await page.goto('/login')
  await page.getByLabel('Email', { exact: true }).fill('nobody@example.com')
  await page.getByLabel('Contraseña', { exact: true }).fill('not-a-real-password')
  await page.getByRole('button', { name: 'Ingresar' }).click()

  await expect(page.getByRole('alert')).toHaveText('Email o contraseña incorrectos.')
  await expect(page).toHaveURL(/\/login/)
})

test('the eye button reveals and hides the typed password', async ({ page }) => {
  await page.goto('/login')
  const password = page.getByLabel('Contraseña', { exact: true })
  await password.fill('contraseña-de-ejemplo')
  await expect(password).toHaveAttribute('type', 'password')

  await page.getByRole('button', { name: 'Mostrar contraseña' }).click()
  await expect(password).toHaveAttribute('type', 'text')
  await expect(password).toHaveValue('contraseña-de-ejemplo')
  await prScreenshot(page.getByRole('main').locator('form'), 'password-visible')

  await page.getByRole('button', { name: 'Ocultar contraseña' }).click()
  await expect(password).toHaveAttribute('type', 'password')
  await expect(password).toHaveValue('contraseña-de-ejemplo')
})
