import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'
import { prScreenshot } from '../support/pr-screenshot'

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
  // Up close the viewer splices in the samples when their download lands, and
  // that moves the legend to a sample under the cursor. Wait for them, so what
  // gets compared is the settled chart and not the download.
  await expect(chart).toHaveAttribute('data-trace', 'samples')
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

test('at 25 mm/s the ECG draws the samples, and the min/max overview only from afar', async ({
  page,
}) => {
  const failures = watchForFailures(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()

  // A demo study runs 9 to 14 min, so what the viewer downloads first is one
  // min/max pair per 64 samples (128 ms). The study opens at 25 mm/s, where a
  // bucket is wider than a pixel: the viewer has to fetch the samples.
  const chart = page.getByLabel('Gráfico ECG interactivo')
  await expect(chart.locator('.uplot')).toBeVisible()
  await expect(chart).toHaveAttribute('data-trace', 'samples')
  await prScreenshot(chart, 'ECG at 25 mm/s drawn from the samples')

  // Zoomed out to the whole study a bucket is narrower than a pixel and the
  // overview is what the samples would look like anyway.
  const zoomOut = page.getByRole('button', { name: 'Zoom out' })
  for (let i = 0; i < 7; i++) await zoomOut.click()
  await expect(chart).toHaveAttribute('data-trace', 'overview')
  expect(failures).toEqual([])
})
