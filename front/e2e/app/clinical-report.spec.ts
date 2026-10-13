import { expect, test } from '@playwright/test'
import { watchForFailures } from '../support/failures'
import { prScreenshot } from '../support/pr-screenshot'

test('the draft report opens with its preview beside the actions', async ({ page }) => {
  const failures = watchForFailures(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  await page.getByRole('tab', { name: 'Informe clínico' }).click()
  await page.getByRole('button', { name: 'Previsualizar borrador' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'Borrador del informe' })).toBeVisible()
  // Only the action that opened the dialog is offered.
  await expect(dialog.getByRole('button', { name: 'Generar informe final' })).toHaveCount(0)

  // The draft generates by itself; download and print appear only with a PDF.
  const pdf = dialog.getByTestId('clinical-report-pdf-preview')
  await expect(pdf).toBeVisible({ timeout: 30_000 })
  const download = dialog.getByRole('button', { name: 'Descargar PDF' })
  await expect(download).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Imprimir' })).toBeVisible()

  // The preview is the right-hand column and its actions sit under it.
  const pdfBox = (await pdf.boundingBox())!
  const actionsBox = (await dialog
    .getByRole('button', { name: 'Regenerar borrador' })
    .boundingBox())!
  const downloadBox = (await download.boundingBox())!
  expect(pdfBox.x).toBeGreaterThan(actionsBox.x + actionsBox.width)
  expect(downloadBox.y).toBeGreaterThan(pdfBox.y + pdfBox.height - 1)

  await prScreenshot(dialog, 'Draft report dialog with the PDF preview on the right')
  expect(failures).toEqual([])
})

test('the final report waits for an explicit click', async ({ page }) => {
  const failures = watchForFailures(page)
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  await page.getByRole('tab', { name: 'Informe clínico' }).click()
  const openFinal = page.getByRole('button', { name: 'Generar informe final' })
  test.skip(await openFinal.isDisabled(), 'the demo study is missing final-report requirements')
  await openFinal.click()

  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: 'Informe final' })).toBeVisible()
  await expect(dialog.getByText('La vista previa del PDF aparecerá acá')).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Generar borrador' })).toHaveCount(0)
  await expect(dialog.getByRole('button', { name: 'Descargar PDF' })).toHaveCount(0)
  expect(failures).toEqual([])
})

test('a segment with an estimated clock is a warning, not a missing requirement', async ({
  page,
}) => {
  const failures = watchForFailures(page)
  const message =
    '1 tramo con la hora estimada por recepción. El informe marca esas horas como estimadas.'
  // The demo seed has every segment verified: the preview gets the issue the backend sends
  // when the bridge uploaded the backlog of a boot it never saw.
  await page.route('**/clinical-report/preview', async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    body.issues = [
      ...body.issues.filter((issue: { code: string }) => issue.code !== 'TIME_NOT_VERIFIED'),
      { code: 'TIME_NOT_VERIFIED', message, severity: 'warning' },
    ]
    await route.fulfill({ response, json: body })
  })
  await page.goto('/studies?status=completed')
  await page
    .getByRole('button', { name: /^Abrir estudio de / })
    .first()
    .click()
  await page.getByRole('tab', { name: 'Informe clínico' }).click()

  const requirements = page
    .getByRole('heading', { name: 'Requisitos para finalizar' })
    .locator('xpath=ancestor::div[contains(@class, "rounded-xl")][1]')
  await expect(requirements.getByText(message)).toBeVisible()
  await expect(requirements.getByText('Hora de las muestras verificada')).toHaveCount(0)

  await prScreenshot(requirements, 'Estimated clock listed as a warning, not a requirement')
  expect(failures).toEqual([])
})
