import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'
import { prScreenshot } from '../support/pr-screenshot'

test('the dashboard shows the four fleet summary cards', async ({ page }) => {
  const failures = watchForFailures(page)
  await page.goto('/')

  for (const label of [
    'Alertas pendientes',
    'Estudios en curso',
    'Pacientes activos',
    'Chalecos transmitiendo',
  ]) {
    await expect(page.getByRole('link', { name: label })).toBeVisible()
  }
  // The cards render a skeleton until the overview request answers.
  await page.waitForLoadState('networkidle')

  await prScreenshot(
    page.getByRole('link', { name: 'Pacientes activos' }),
    'Active patients card on the dashboard',
  )
  expect(failures).toEqual([])
})
