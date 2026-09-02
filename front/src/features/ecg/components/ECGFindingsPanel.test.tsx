// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { ECGAnnotation } from '../types'
import { ECGFindingsPanel } from './ECGFindingsPanel'

describe('ECGFindingsPanel', () => {
  it('muestra el estado vacío', () => {
    render(
      <ECGFindingsPanel
        annotations={[]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={() => undefined}
      />,
    )

    expect(screen.getByText('Sin avisos detectados')).toBeTruthy()
  })

  it('ordena hallazgos, muestra severidad y permite seleccionarlos', () => {
    const onSelect = vi.fn()
    const later = annotation({
      id: 'later',
      kind: 'afib',
      category: 'clinical',
      severity: 'critical',
      startMs: 20_000,
      endMs: 25_000,
      confidenceScore: 0.96,
    })
    const earlier = annotation({ id: 'earlier', startMs: 5_000, endMs: 6_000 })
    render(
      <ECGFindingsPanel
        annotations={[later, earlier]}
        recordingStartMs={0}
        selectedAnnotationId="later"
        onAnnotationSelect={onSelect}
      />,
    )

    const buttons = screen.getAllByRole('button')
    expect(buttons[0].textContent).toContain('Fibrilación auricular')
    expect(buttons[0].textContent).toContain('Crítica')
    expect(buttons[0].textContent).toContain('Confianza 96%')
    expect(buttons[0].getAttribute('aria-pressed')).toBe('true')
    expect(buttons[0].className).toContain('cursor-pointer')
    expect(buttons[1].textContent).toContain('Electrodo desconectado')

    fireEvent.click(buttons[1])
    expect(onSelect).toHaveBeenCalledWith(earlier)
  })

  it('agrupa la respuesta del paciente dentro del hallazgo que contesta', () => {
    // No es un aviso más: si se listara al mismo nivel, la cuenta de "avisos
    // detectados" incluiría algo que el sistema no detectó y el médico tendría
    // que emparejar dos tarjetas sueltas.
    const onSelect = vi.fn()
    const finding = annotation({
      id: 'event-1',
      kind: 'tachycardia',
      category: 'clinical',
      severity: 'critical',
      startMs: 10_000,
      endMs: 20_000,
    })
    const response = annotation({
      id: 'report-1',
      kind: 'patient_report',
      category: 'patient_marker',
      severity: 'high',
      startMs: 15_000,
      endMs: 15_000,
      linkedAnnotationId: 'event-1',
      description: 'Palpitaciones · Mareo',
    })

    // Sin `globals` no corre el cleanup automático de testing-library: hay que
    // consultar dentro del container de este render y no en todo el document.
    const { container } = render(
      <ECGFindingsPanel
        annotations={[finding, response]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={onSelect}
      />,
    )

    expect(container.textContent).toContain('1 aviso detectado')
    const cards = Array.from(container.querySelectorAll('[data-annotation-card]'))
    expect(cards).toHaveLength(1)
    expect(cards[0].textContent).toContain('Taquicardia')
    expect(cards[0].textContent).toContain('Respuesta del paciente')
    expect(cards[0].textContent).toContain('Palpitaciones · Mareo')

    // Sigue siendo su propio botón: sobre el ECG tiene marca propia dentro de
    // la banda y el médico tiene que poder saltar ahí.
    const [, respuesta] = Array.from(container.querySelectorAll('button'))
    fireEvent.click(respuesta)
    expect(onSelect).toHaveBeenCalledWith(response)
  })

  it('resalta la tarjeta entera cuando lo seleccionado es la respuesta', () => {
    const finding = annotation({
      id: 'event-1',
      kind: 'tachycardia',
      startMs: 10_000,
      endMs: 20_000,
    })
    const response = annotation({
      id: 'report-1',
      kind: 'patient_report',
      category: 'patient_marker',
      startMs: 15_000,
      endMs: 15_000,
      linkedAnnotationId: 'event-1',
      description: 'Palpitaciones',
    })

    const { container } = render(
      <ECGFindingsPanel
        annotations={[finding, response]}
        recordingStartMs={0}
        selectedAnnotationId="report-1"
        onAnnotationSelect={() => undefined}
      />,
    )

    const card = container.querySelector('[data-annotation-card]')
    expect(card?.className).toContain('border-primary-300')
  })
})

function annotation(overrides: Partial<ECGAnnotation>): ECGAnnotation {
  return {
    id: 'event-1',
    kind: 'lead_off',
    category: 'signal_quality',
    severity: 'medium',
    startMs: 0,
    endMs: 0,
    confidenceScore: null,
    linkedAnnotationId: null,
    description: null,
    ...overrides,
  }
}
