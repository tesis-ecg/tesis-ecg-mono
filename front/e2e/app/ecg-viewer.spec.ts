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

  const hand = page.getByRole('button', { name: 'Desplazar gráfico' })
  await expect(hand).toHaveAttribute('aria-pressed', 'true')
  const timeCaliper = page.getByRole('button', { name: 'Calibre de tiempo' })
  await expect(timeCaliper).toBeEnabled()
  await timeCaliper.click()
  await expect(timeCaliper).toHaveAttribute('aria-pressed', 'true')
  await expect(hand).toHaveAttribute('aria-pressed', 'false')

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

  await hand.click()
  await expect(hand).toHaveAttribute('aria-pressed', 'true')
  await expect(label).toHaveCount(1)

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
  await expect(timeCaliper).toHaveAttribute(
    'title',
    /Acercá el gráfico hasta ver las muestras reales/,
  )

  // Back over the samples they come back.
  const zoomIn = page.getByRole('button', { name: 'Zoom in' })
  for (let i = 0; i < 7; i++) await zoomIn.click()
  await expect(chart).toHaveAttribute('data-trace', 'samples')
  await expect(timeCaliper).toBeEnabled()
  expect(failures).toEqual([])
})

test('expanded ECG is a page with findings and a way back to the study', async ({ page }) => {
  const failures = watchForFailures(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  const studyUrl = page.url()
  await expect(page.getByLabel('Gráfico ECG interactivo').locator('.uplot')).toBeVisible()
  await page.getByRole('button', { name: 'Abrir en pantalla completa' }).click()
  await expect(page).toHaveURL(/\/studies\/[^/]+\/ecg$/)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByRole('tab', { name: 'Señal ECG' })).toHaveCount(0)
  await expect(page.getByLabel('Hallazgos ECG')).toBeVisible()
  await expect(page.getByLabel('Gráfico ECG interactivo').locator('.uplot')).toBeVisible()
  await prScreenshot(page.getByRole('main'), 'Expanded ECG page with findings')
  await page.getByRole('button', { name: 'Volver al estudio' }).click()
  await expect(page).toHaveURL(studyUrl)
  await expect(page.getByRole('tab', { name: 'Señal ECG' })).toBeVisible()
  expect(failures).toEqual([])
})

/** Resumen sintético de 30 h; todos los objetos se sirven desde el test. */
async function stubLongTimeline(page: Page) {
  const durationMs = 30 * 3_600_000
  const sampleRate = 500
  const samplesPerBucket = 50_000
  const sampleCount = (durationMs / 1000) * sampleRate
  const pointCount = (sampleCount / samplesPerBucket) * 2
  const overview = Buffer.alloc(pointCount * 4)
  for (let i = 0; i < pointCount; i++) overview.writeFloatLE(i % 2 === 0 ? -0.4 : 1.2, i * 4)
  await page.route('**/__e2e/timeline-overview', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/octet-stream',
      body: overview,
    }),
  )
  await page.route('**/studies/*/ecg/manifest', async (route) => {
    const response = await route.fetch()
    const original = await response.json()
    const startTimestamp = original.startTimestamp
    const annotations = Array.from({ length: 80 }, (_, i) => ({
      id: `synthetic-morphology-${i}`,
      kind: 'morphology_anomaly',
      category: 'clinical',
      severity: 'medium',
      startOffsetMs: 3_600_000 + i * 1_000,
      endOffsetMs: 3_600_000 + i * 1_000 + 500,
      confidenceScore: 0.9,
    }))
    // Tipos simultáneos: la franja conserva su altura y ofrece el resto en +N.
    annotations.push(
      ...['lead_off', 'tachycardia'].map((kind, i) => ({
        id: `synthetic-overlap-${kind}`,
        kind,
        category: 'clinical',
        severity: 'low',
        // Exact same anchor guarantees they land in the same timeline slot.
        // A few hundred milliseconds of drift can straddle a slot boundary
        // depending on the rendered width in CI.
        startOffsetMs: 3_600_000,
        endOffsetMs: 3_600_500,
        confidenceScore: 0.9,
      })),
    )
    annotations.push({
      id: 'synthetic-noise',
      kind: 'noise',
      category: 'signal_quality',
      severity: 'low',
      startOffsetMs: 13 * 3_600_000,
      endOffsetMs: 13 * 3_600_000 + 10_000,
      confidenceScore: 0.9,
    })
    await route.fulfill({
      response,
      json: {
        ...original,
        formatVersion: 3,
        sampleRate,
        sampleCount,
        startTimestamp,
        durationMs,
        isSimulated: true,
        viewKind: 'raw',
        raw: null,
        segments: [],
        timeline: [
          {
            ordinal: 0,
            startSampleIndex: 0,
            sampleCount,
            startEpochMs: startTimestamp,
            endEpochMs: startTimestamp + durationMs,
            bootId: 1,
            anchorSource: 'ntp',
            anchorUncertaintyMs: 10,
            anchorMatchesBoot: true,
          },
        ],
        levels: [
          {
            samplesPerBucket,
            pointCount,
            encoding: 'minmax-float32-le',
            chunks: [
              {
                url: new URL('/__e2e/timeline-overview', route.request().url()).href,
                expiresAt: new Date(Date.now() + 3_600_000).toISOString(),
                byteLength: overview.length,
                sha256: null,
                pointCount,
              },
            ],
          },
        ],
        annotations,
      },
    })
  })
}

test('long ECG groups findings, filters the sidebar and explores twelve hours independently', async ({
  page,
}) => {
  const failures = watchForFailures(page)
  await stubLongTimeline(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  const chart = page.getByLabel('Gráfico ECG interactivo')
  const timeline = page.getByTestId('ecg-timeline')
  const sidebar = page.getByRole('region', { name: 'Hallazgos ECG' })
  await expect(chart.locator('.uplot')).toBeVisible()
  const range = () =>
    timeline.evaluate((element) => ({
      start: Number(element.dataset.startMs),
      end: Number(element.dataset.endMs),
    }))
  const before = await range()
  expect(before.end - before.start).toBe(12 * 3_600_000)
  const strip = page.getByLabel('Avisos del estudio')
  expect((await strip.boundingBox())?.height).toBeLessThanOrEqual(56)
  await strip.getByRole('button', { name: 'Ver otros 2 tipos de hallazgo en este tramo' }).click()
  await expect(page.getByRole('menuitem', { name: /Electrodo desconectado/ })).toBeVisible()
  await expect(page.getByRole('menuitem', { name: /Taquicardia/ })).toBeVisible()
  await page.keyboard.press('Escape')
  const legend = await chart.locator('.u-legend').innerText()
  await timeline.locator('[data-timeline-group="morphology_anomaly"]').click()
  await expect(sidebar.getByText('80 avisos detectados')).toBeVisible()
  await expect(sidebar.getByRole('button', { name: 'Ver todos' })).toBeVisible()
  expect(await chart.locator('.u-legend').innerText()).toBe(legend)
  await prScreenshot(timeline, 'Twelve-hour ECG timeline with findings grouped by type')
  await prScreenshot(sidebar, 'Findings sidebar filtered to the selected timeline group')

  await page.getByRole('button', { name: 'Tramo siguiente de la timeline' }).click()
  expect((await range()).start).toBe(before.start + 6 * 3_600_000)
  expect(await chart.locator('.u-legend').innerText()).toBe(legend)
  await expect(sidebar.getByText('80 avisos detectados')).toBeVisible()
  await sidebar.locator('[data-annotation-card="synthetic-morphology-0"] button').first().click()
  await expect(timeline).toHaveAttribute('data-start-ms', String(before.start))
  await expect(
    sidebar.locator('[data-annotation-card="synthetic-morphology-0"] button').first(),
  ).toHaveAttribute('aria-pressed', 'true')
  await expect(timeline.locator('[data-testid^="timeline-annotation-range-"]')).toHaveCount(1)

  await page.getByRole('button', { name: 'Abrir en pantalla completa' }).click()
  await expect(page).toHaveURL(/\/studies\/[^/]+\/ecg$/)
  await expect(sidebar.getByText('80 avisos detectados')).toBeVisible()
  await page.getByRole('button', { name: 'Volver al estudio' }).click()
  await expect(sidebar.getByRole('button', { name: 'Ver todos' })).toBeVisible()
  await page.getByRole('button', { name: 'Tramo siguiente de la timeline' }).click()
  await timeline.locator('[data-timeline-group="noise"]').click()
  await expect(sidebar.getByRole('button', { name: 'Ver todos' })).toHaveCount(0)
  await expect(sidebar.getByText('83 avisos detectados')).toBeVisible()
  await expect(
    sidebar.locator('[data-annotation-card="synthetic-noise"] button').first(),
  ).toHaveAttribute('aria-pressed', 'true')
  expect(failures).toEqual([])
})

test('ECG sidebar opens reclassification in the signal tab and expanded view', async ({ page }) => {
  const failures = watchForFailures(page)
  const findingId = '00000000-0000-4000-8000-000000000123'
  let resolveFinding!: (finding: Record<string, unknown>) => void
  const findingReady = new Promise<Record<string, unknown>>((resolve) => {
    resolveFinding = resolve
  })
  await page.route('**/studies/*/ecg/manifest', async (route) => {
    const response = await route.fetch()
    const manifest = await response.json()
    const finding = {
      id: findingId,
      kind: 'ventricular_ectopy',
      category: 'clinical',
      severity: 'medium',
      startOffsetMs: 1_000,
      endOffsetMs: 2_000,
      startEpochMs: manifest.startTimestamp + 1_000,
      endEpochMs: manifest.startTimestamp + 2_000,
      confidenceScore: 0.95,
      modelVersion: 'e2e',
      validationStatus: 'pending',
      reclassifiable: true,
    }
    resolveFinding(finding)
    await route.fulfill({ response, json: { ...manifest, annotations: [finding] } })
  })
  await page.route('**/studies/*/findings', async (route) => {
    const finding = await findingReady
    await route.fulfill({
      json: {
        studyId: route.request().url().split('/').at(-2),
        sampleRate: 500,
        sampleCount: 1_500,
        durationMs: 3_000,
        modelVersion: 'e2e',
        quality: {
          analyzableRatio: 1,
          goodRatio: 1,
          marginalRatio: 0,
          badRatio: 0,
          evaluatedMs: 3_000,
        },
        groups: [],
        ungrouped: [finding],
      },
    })
  })
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  const panel = page.getByRole('region', { name: 'Hallazgos ECG' })
  await panel.getByRole('button', { name: /^Reclasificar/ }).click()
  const dialog = page.getByRole('dialog', { name: 'Reclasificar hallazgo' })
  await expect(dialog.getByRole('radio', { name: /Marcar como otro hallazgo/ })).toBeVisible()
  await prScreenshot(dialog, 'Reclassification from the ECG findings sidebar')
  await dialog.getByRole('button', { name: 'Volver', exact: true }).click()
  await page.getByRole('button', { name: 'Abrir en pantalla completa' }).click()
  await panel.getByRole('button', { name: /^Reclasificar/ }).click()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Volver', exact: true }).click()
  expect(failures).toEqual([])
})
