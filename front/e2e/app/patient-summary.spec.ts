import { expect, test } from '@playwright/test'

import { watchForFailures } from '../support/failures'
import { prScreenshot } from '../support/pr-screenshot'

const patientId = '00000000-0000-4000-8000-000000000001'

test('el resumen muestra el último registro cerrado sin inventar una comparación', async ({
  page,
}) => {
  const failures = watchForFailures(page)
  await page.route(`**/patients/${patientId}`, (route) =>
    route.fulfill({
      json: {
        id: patientId,
        fullName: 'Paciente de demostración',
        dni: '10000001',
        birthDate: '1970-01-01',
        sex: 'F',
        assignedDeviceId: null,
        assignedDeviceSerial: null,
        studyStatus: 'completed',
        lastDataReceivedAt: '2025-01-01T15:00:00Z',
        contactEmail: null,
        contactPhone: null,
        hasAppAccount: false,
      },
    }),
  )
  await page.route(`**/patients/${patientId}/summary`, (route) =>
    route.fulfill({
      json: {
        windowHours: 24,
        studyId: '00000000-0000-4000-8000-000000000002',
        windowStart: '2025-01-01T13:00:00Z',
        windowEnd: '2025-01-01T15:00:00Z',
        heartRate: { averageBpm: 75, deltaBpm: null, trend: 'flat' },
        eventsDetected: { count: 0, delta: null, trend: 'flat' },
        adherencePercent: { value: 92, deltaPp: null, trend: 'flat' },
      },
    }),
  )
  await page.goto(`/patients/${patientId}`)
  const summary = page.getByRole('tabpanel', { name: 'Resumen' })
  await expect(summary.getByText(/Último estudio/)).toBeVisible()
  await expect(summary.getByText('75', { exact: true })).toBeVisible()
  await expect(summary.getByText('0', { exact: true })).toBeVisible()
  await expect(summary.getByText('92', { exact: true })).toBeVisible()
  await expect(summary.getByText(/vs\. período anterior/)).toHaveCount(0)
  await prScreenshot(summary.locator('.grid'), 'resumen del último registro del paciente')
  expect(failures).toEqual([])
})
