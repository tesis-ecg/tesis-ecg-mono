import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'
import { prScreenshot } from '../support/pr-screenshot'

// No writes: creation is intercepted before opening the study.
test('select a manual finding, cancel, retry an error and save', async ({ page }) => {
  const failures = watchForFailures(page)
  let saved: Record<string, unknown> | null = null
  let submissions = 0
  const requestIds: string[] = []
  await page.route('**/studies/*/findings', async (route) => {
    if (route.request().method() !== 'POST') {
      if (!saved) return route.fallback()
      const response = await route.fetch()
      const data = await response.json()
      return route.fulfill({ response, json: { ...data, ungrouped: [...data.ungrouped, saved] } })
    }
    const body = route.request().postDataJSON()
    requestIds.push(body.clientRequestId)
    submissions++
    if (submissions === 1) {
      return route.fulfill({
        status: 422,
        json: { detail: { code: 'RETRY', message: 'Reintentá guardar el hallazgo.' } },
      })
    }
    saved = {
      id: '00000000-0000-4000-8000-000000000099',
      kind: body.kind,
      category: 'clinical',
      severity: body.severity,
      startEpochMs: body.startEpochMs,
      endEpochMs: body.endEpochMs,
      startOffsetMs: 0,
      endOffsetMs: body.endEpochMs - body.startEpochMs,
      source: 'manual',
      createdByName: 'Médico de prueba',
      createdAt: '2026-10-28T12:00:00Z',
      modelVersion: null,
      confidenceScore: null,
      validationStatus: 'reclassified',
      reclassifiable: true,
    }
    await route.fulfill({
      status: 201,
      json: {
        finding: saved,
        holterMetrics: { status: 'pending', unavailableReason: 'RECALCULATION_FAILED' },
      },
    })
  })
  await page.route('**/studies/*/ecg/manifest', async (route) => {
    const response = await route.fetch()
    if (!saved) return route.fulfill({ response })
    const data = await response.json()
    await route.fulfill({ response, json: { ...data, annotations: [...data.annotations, saved] } })
  })
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  await expect(page.getByLabel('Gráfico ECG interactivo')).toHaveAttribute('data-trace', 'samples')
  await page.getByRole('button', { name: 'Marcar hallazgo', exact: true }).click()
  const capture = page.getByTestId('measurement-capture')
  const selectRange = async () => {
    const box = await capture.boundingBox()
    expect(box).not.toBeNull()
    await page.mouse.move(box!.x + box!.width * 0.3, box!.y + box!.height / 2)
    await page.mouse.down()
    await page.mouse.move(box!.x + box!.width * 0.6, box!.y + box!.height / 2, { steps: 8 })
    await page.mouse.up()
  }
  await selectRange()
  const dialog = page.getByRole('dialog', { name: 'Agregar hallazgo manual' })
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Guardar hallazgo' })).toBeDisabled()
  await dialog.getByRole('button', { name: 'Cancelar' }).click()
  expect(submissions).toBe(0)
  await expect(page.getByRole('button', { name: 'Marcar hallazgo', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await selectRange()
  await dialog.getByRole('combobox', { name: 'Tipo de hallazgo' }).click()
  await page.getByRole('option', { name: 'Taquicardia', exact: true }).click()
  await dialog.getByRole('combobox', { name: 'Prioridad' }).click()
  await page.getByRole('option', { name: 'Alta', exact: true }).click()
  await prScreenshot(dialog, 'hallazgo-manual-seleccionado')
  await dialog.getByRole('button', { name: 'Guardar hallazgo' }).click()
  await expect(page.getByText('Reintentá guardar el hallazgo.')).toBeVisible()
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Guardar hallazgo' }).click()
  await expect(dialog).not.toBeVisible()
  await expect(
    page
      .getByRole('region', { name: 'Hallazgos ECG' })
      .getByText('Taquicardia · Manual', { exact: true }),
  ).toBeVisible()
  expect(submissions).toBe(2)
  expect(requestIds[0]).toBe(requestIds[1])
  expect(failures).toEqual([])
})
