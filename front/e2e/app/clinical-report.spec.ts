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
