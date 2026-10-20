import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'
import { prScreenshot } from '../support/pr-screenshot'

// The findings and the Holter metrics are stubbed: the demo seed has no S/V
// classification, and the reclassification must not write anything for real.
const T0 = Date.UTC(2026, 8, 16, 12, 0, 0)

const ectopy = (total: number) => ({
  total,
  single: total - 4,
  pairs: { episodes: 2, beats: 4 },
  bigeminy: { episodes: 0, beats: 0 },
  trigeminy: { episodes: 0, beats: 0 },
  runs: { episodes: 1, beats: 3 },
  longestRun: { value: 3, sampleIndex: 900, epochMs: T0 + 300_000, durationMs: 2000 },
  perThousand: 8.1,
  maxPerMinute: { value: 6, sampleIndex: 800, epochMs: T0 + 240_000, durationMs: 30_000 },
})

const metrics = (ventricularTotal: number) => ({
  status: 'ok',
  unavailableReason: null,
  analysis: {
    algorithmVersion: 6,
    analyzedUntilSample: 1_800_000,
    analyzedMs: 3_600_000,
    excludedMs: 0,
    rrIntervals: 4000,
    nnIntervals: 3900,
    classifiedBeats: 4000,
    unclassifiedBeats: 20,
  },
  heartRate: null,
  pauses: null,
  supraventricular: ectopy(12),
  ventricular: ectopy(ventricularTotal),
  ectopyUnavailableReason: null,
  hrvTime: null,
  hrvFrequency: null,
  st: [],
  hourly: [],
  rrHistogram: null,
})

const finding = {
  id: '00000000-0000-4000-8000-000000000001',
  kind: 'ventricular_ectopy',
  category: 'clinical',
  severity: 'low',
  startOffsetMs: 240_000,
  endOffsetMs: 250_000,
  startEpochMs: T0 + 240_000,
  endEpochMs: T0 + 250_000,
  confidenceScore: null,
  modelVersion: 'ml-test',
  reclassifiable: true,
  validationStatus: 'pending',
}

test('the doctor reclassifies a finding and the rhythm metrics refresh', async ({ page }) => {
  const failures = watchForFailures(page)
  let corrected = false

  await page.route('**/studies/*/findings', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({
          json: {
            studyId: 'stub',
            sampleRate: 500,
            sampleCount: 1_800_000,
            durationMs: 3_600_000,
            modelVersion: 'ml-test',
            reclassifiable: true,
            quality: {
              analyzableRatio: 1,
              goodRatio: 1,
              marginalRatio: 0,
              badRatio: 0,
              evaluatedMs: 3_600_000,
              intervals: [],
            },
            groups: [],
            ungrouped: [
              corrected
                ? {
                    ...finding,
                    validationStatus: 'reclassified',
                    correction: {
                      action: 'relabel',
                      beatLabel: 'S',
                      effectiveKind: 'supraventricular_ectopy',
                      note: 'Prematuro y angosto',
                      correctedBy: 'u-1',
                      correctedByName: 'Dr. de Prueba',
                      correctedAt: '2026-09-17T12:00:00Z',
                    },
                  }
                : finding,
            ],
            totals: { ventricular_ectopy: 1 },
            truncated: false,
          },
        })
      : route.fallback(),
  )
  await page.route('**/studies/*/holter-metrics', (route) =>
    route.fulfill({ json: metrics(corrected ? 11 : 12) }),
  )
  await page.route('**/findings/*/reclassification', async (route) => {
    corrected = true
    await route.fulfill({
      status: 200,
      json: {
        finding: { ...finding, validationStatus: 'reclassified' },
        holterMetrics: metrics(11),
      },
    })
  })

  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  await page.getByRole('tab', { name: 'Análisis' }).click()

  const ventricular = page.getByRole('region', { name: 'Ventriculares (V)' })
  await expect(
    ventricular.getByText('V total').locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('12')
  await expect(
    ventricular.getByRole('button', { name: 'Ver Máximo de V en 1 min en el ECG' }),
  ).toBeVisible()

  await page.getByRole('button', { name: /^Reclasificar Extrasístoles ventriculares/ }).click()
  const dialog = page.getByRole('dialog', { name: 'Reclasificar hallazgo' })
  await dialog.getByLabel(/Reclasificar como latidos supraventriculares/).check()
  await dialog.getByLabel('Nota (opcional)').fill('Prematuro y angosto')
  await prScreenshot(dialog, 'Reclassify finding dialog with the relabel option chosen')
  await dialog.getByRole('button', { name: 'Guardar corrección' }).click()

  await expect(dialog).toBeHidden()
  await expect(
    ventricular.getByText('V total').locator('xpath=following-sibling::dd[1]'),
  ).toHaveText('11')
  const status = page.locator('[data-correction-status]')
  await expect(status).toContainText('Dr. de Prueba')
  await expect(status).toContainText('Original: Extrasístoles ventriculares (V)')
  await prScreenshot(
    page.getByRole('region', { name: 'Ventriculares (V)' }),
    'Ventricular metrics recalculated after the correction',
  )
  expect(failures).toEqual([])
})
