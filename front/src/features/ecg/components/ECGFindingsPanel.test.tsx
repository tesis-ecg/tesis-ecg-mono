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
    ...overrides,
  }
}
