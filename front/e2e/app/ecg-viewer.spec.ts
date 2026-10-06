import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'

test('hiding the findings keeps the ECG zoom where it was', async ({ page }) => {
  const failures = watchForFailures(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()

  const chart = page.getByLabel('Gráfico ECG interactivo')
  await expect(chart.locator('.uplot')).toBeVisible()
  await page.getByRole('button', { name: 'Zoom in' }).click()
  // Tag the uPlot root: if the chart is rebuilt the tag is gone, and with it
  // the zoom and the cursor the doctor had.
  await chart.locator('.uplot').evaluate((el) => el.setAttribute('data-e2e-instance', 'before'))
  const legendBefore = await chart.locator('.u-legend').innerText()

  const findings = page.getByRole('region', { name: 'Hallazgos ECG' })
  await findings.getByRole('button', { name: 'Ocultar del gráfico' }).click()
  await findings.getByRole('button', { name: 'Mostrar en gráfico' }).click()

  await expect(chart.locator('.uplot[data-e2e-instance="before"]')).toHaveCount(1)
  expect(await chart.locator('.u-legend').innerText()).toBe(legendBefore)
  expect(failures).toEqual([])
})
