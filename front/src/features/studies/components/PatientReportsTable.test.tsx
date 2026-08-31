// @vitest-environment jsdom

import { render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { PatientReportsTable } from './PatientReportsTable'
import type { StudyPatientReport } from '../types'

/**
 * Lo que se prueba acá es la distinción que evita un malentendido clínico: un
 * registro sin señal debajo **existe** y el médico tiene que verlo, aunque
 * todavía no haya banda en el ECG. Si la tabla lo escondiera o lo mostrara
 * igual que a los demás, un síntoma marcado hace veinte minutos se perdería
 * hasta el próximo envío del chaleco.
 */
describe('PatientReportsTable', () => {
  it('avisa cuántos registros todavía no se pueden ubicar en el gráfico', () => {
    render(
      <PatientReportsTable
        reports={[report({ id: 'a' }), report({ id: 'b', offsetMs: null, visibleInChart: false })]}
        pendingSignalTotal={1}
        onLocate={vi.fn()}
      />,
    )

    expect(screen.getByText(/todavía no se puede ubicar en el gráfico/i)).toBeTruthy()
  })

  it('ofrece saltar al ECG solo cuando ya hay señal debajo', () => {
    const onLocate = vi.fn()
    render(
      <PatientReportsTable
        reports={[
          report({ id: 'con-senal', offsetMs: 12_000, visibleInChart: true }),
          report({ id: 'sin-senal', offsetMs: null, visibleInChart: false }),
        ]}
        pendingSignalTotal={1}
        onLocate={onLocate}
      />,
    )

    const rows = screen.getAllByRole('row').slice(1)
    expect(within(rows[0]).getByRole('button', { name: /ver en el ecg/i })).toBeTruthy()
    expect(within(rows[1]).queryByRole('button', { name: /ver en el ecg/i })).toBeNull()
    expect(within(rows[1]).getByText(/aún sin señal/i)).toBeTruthy()
  })

  it('muestra el texto libre del paciente en lugar de la etiqueta genérica "Otro"', () => {
    render(
      <PatientReportsTable
        reports={[
          report({
            symptoms: ['otro'],
            symptomLabels: ['Otro'],
            symptomsOther: 'Un pinchazo en el brazo',
            activity: 'otro',
            activityLabel: 'Otra cosa',
            activityOther: 'Cortando el pasto',
          }),
        ]}
        pendingSignalTotal={0}
        onLocate={vi.fn()}
      />,
    )

    expect(screen.getByText(/Un pinchazo en el brazo/)).toBeTruthy()
    expect(screen.getByText('Cortando el pasto')).toBeTruthy()
  })

  it('sin registros explica de dónde salen, en vez de mostrar una tabla vacía', () => {
    render(<PatientReportsTable reports={[]} pendingSignalTotal={0} onLocate={vi.fn()} />)

    expect(screen.getByText('Sin registros del paciente')).toBeTruthy()
  })
})

function report(overrides: Partial<StudyPatientReport> = {}): StudyPatientReport {
  return {
    id: 'report-1',
    occurredAt: '2026-08-30T14:30:00Z',
    source: 'manual',
    symptoms: ['palpitaciones'],
    symptomLabels: ['Palpitaciones'],
    symptomsOther: null,
    activity: 'caminando',
    activityLabel: 'Caminando',
    activityOther: null,
    notes: null,
    alertId: null,
    createdAt: '2026-08-30T14:31:00Z',
    offsetMs: 8_000,
    visibleInChart: true,
    ...overrides,
  }
}
