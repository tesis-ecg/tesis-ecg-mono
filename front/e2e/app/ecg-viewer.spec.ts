import { expect, test, type Page } from '@playwright/test'
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

interface TimelineSegment {
  ordinal: number
  startSampleIndex: number
  sampleCount: number
  startEpochMs: number
  endEpochMs: number
  bootId: number | null
  anchorSource: string
  anchorUncertaintyMs: number | null
  anchorMatchesBoot: boolean | null
}

/**
 * Deja la línea de tiempo del manifest con tres tramos contiguos, el primero sin
 * hora verificada y los otros dos verificados. Intercepta la respuesta real y
 * solo le cambia la línea de tiempo: la señal sigue siendo la del seed de demo.
 */
async function stubOneUnverifiedSegment(page: Page) {
  await page.route('**/studies/*/ecg/manifest', async (route) => {
    const response = await route.fetch()
    const manifest = await response.json()
    const real: TimelineSegment[] = manifest.timeline ?? []
    let timeline: TimelineSegment[]
    if (real.length >= 2) {
      timeline = real.map((segment, i) => ({
        ...segment,
        anchorMatchesBoot: i !== 0,
        anchorUncertaintyMs: i === 0 ? 23_500 : segment.anchorUncertaintyMs,
      }))
    } else {
      // Estudio de demo sin tramos: se lo parte en tres, sin huecos.
      const third = Math.floor(manifest.sampleCount / 3)
      const durationMs = (third * 1000) / manifest.sampleRate
      timeline = [0, 1, 2].map((i) => ({
        ordinal: i,
        startSampleIndex: i * third,
        sampleCount: i === 2 ? manifest.sampleCount - 2 * third : third,
        startEpochMs: manifest.startTimestamp + i * durationMs,
        endEpochMs:
          i === 2
            ? manifest.startTimestamp + (manifest.sampleCount * 1000) / manifest.sampleRate
            : manifest.startTimestamp + (i + 1) * durationMs,
        bootId: i + 1,
        anchorSource: 'ntp',
        anchorUncertaintyMs: i === 0 ? 23_500 : 50,
        anchorMatchesBoot: i !== 0,
      }))
    }
    await route.fulfill({ response, json: { ...manifest, timeline, startTimeVerified: false } })
  })
}

test('only the unverified segment is marked: the time of day stays and the banner counts it', async ({
  page,
}) => {
  const failures = watchForFailures(page)
  await stubOneUnverifiedSegment(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()

  // The banner says how many segments and how much time, not "the whole study".
  const banner = page.getByText(/tramos? sin hora verificada/)
  await expect(banner).toHaveText(
    /^1 de 3 tramos sin hora verificada \(.+\)\. Su hora es estimada \(±23,5 s\) y se marca en el eje\.$/,
  )
  await expect(page.getByText(/Hora de pared no verificada/)).toHaveCount(0)

  const chart = page.getByLabel('Gráfico ECG interactivo')
  await expect(chart.locator('.uplot')).toBeVisible()
  await expect(chart).toHaveAttribute('data-trace', 'samples')
  const over = chart.locator('.u-over')
  // The plot starts below the fold: mouse coordinates only hit what is on screen.
  await chart.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  const box = await over.boundingBox()
  if (!box) throw new Error('the plot has no box')
  const legend = chart.locator('.u-legend')
  const zoomOut = page.getByRole('button', { name: 'Zoom out' })

  // Whole study: the first third is the unverified segment, the last is verified.
  // The cursor reads the time of day in both; only the first gets "≈" (and
  // neither is an elapsed "+hh:mm:ss").
  for (let i = 0; i < 7; i++) await zoomOut.click()
  await expect(chart).toHaveAttribute('data-trace', 'overview')
  await chart.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  await page.mouse.move(box.x + 6, box.y + box.height / 2)
  await expect(legend).toContainText(/≈\d{1,2}:\d{2}:\d{2}/)
  await expect(legend).not.toContainText('+00:')
  await prScreenshot(chart, 'ECG with the estimated-time band on the unverified segment')
  await page.mouse.move(box.x + box.width - 6, box.y + box.height / 2)
  await expect(legend).toContainText(/\d{1,2}:\d{2}:\d{2}/)
  await expect(legend).not.toContainText('≈')
  expect(failures).toEqual([])
})

test('measurement tools: off from afar, on over the samples, a caliper reads a duration', async ({
  page,
}) => {
  const failures = watchForFailures(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()

  const chart = page.getByLabel('Gráfico ECG interactivo')
  await expect(chart.locator('.uplot')).toBeVisible()
  await expect(chart).toHaveAttribute('data-trace', 'samples')

  const timeCaliper = page.getByRole('button', { name: 'Calibre de tiempo' })
  await expect(timeCaliper).toBeEnabled()
  await timeCaliper.click()
  await expect(timeCaliper).toHaveAttribute('aria-pressed', 'true')

  const capture = page.getByTestId('measurement-capture')
  // The plot starts below the fold: mouse coordinates only hit what is on screen.
  await chart.evaluate((el) => el.scrollIntoView({ block: 'center' }))
  const box = await capture.boundingBox()
  if (!box) throw new Error('the measurement layer has no box')
  const y = box.y + box.height / 2
  await page.mouse.move(box.x + box.width * 0.3, y)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width * 0.45, y, { steps: 5 })
  await page.mouse.move(box.x + box.width * 0.6, y, { steps: 5 })
  await page.mouse.up()

  const label = page.getByTestId('measurement-label')
  await expect(label).toHaveCount(1)
  await expect(label).toContainText(/\d+(,\d+)?\s*(ms|s)\b/)
  await prScreenshot(chart, 'Time caliper placed on the ECG with its duration')

  // Clear removes it.
  await page.getByRole('button', { name: /^Borrar mediciones/ }).click()
  await expect(label).toHaveCount(0)

  // From afar the viewer draws the min/max overview: not signal samples, so no tools.
  await timeCaliper.click()
  const zoomOut = page.getByRole('button', { name: 'Zoom out' })
  for (let i = 0; i < 7; i++) await zoomOut.click()
  await expect(chart).toHaveAttribute('data-trace', 'overview')
  await expect(timeCaliper).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Calibre de amplitud' })).toBeDisabled()
  await expect(page.getByText('Acercá el gráfico hasta ver las muestras reales')).toBeVisible()

  // Back over the samples they come back.
  const zoomIn = page.getByRole('button', { name: 'Zoom in' })
  for (let i = 0; i < 7; i++) await zoomIn.click()
  await expect(chart).toHaveAttribute('data-trace', 'samples')
  await expect(timeCaliper).toBeEnabled()
  expect(failures).toEqual([])
})
