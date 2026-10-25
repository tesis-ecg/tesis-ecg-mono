// @vitest-environment jsdom

import { fireEvent, render, screen, within, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { StudyFinding, StudyFindings } from '@/features/studies/types'
import { api } from '@/lib/api'

HTMLElement.prototype.scrollIntoView = vi.fn()

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
    expect(buttons[0].closest('[data-annotation-card]')?.textContent).toContain('Crítica')
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

  it('alterna si los avisos se dibujan sobre el gráfico', () => {
    const onToggle = vi.fn()
    const { container, rerender } = render(
      <ECGFindingsPanel
        annotations={[annotation({ id: 'a' })]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={() => undefined}
        showOnChart
        onShowOnChartChange={onToggle}
      />,
    )

    const view = within(container)
    fireEvent.click(view.getByRole('button', { name: 'Ocultar del gráfico' }))
    expect(onToggle).toHaveBeenCalledWith(false)

    rerender(
      <ECGFindingsPanel
        annotations={[annotation({ id: 'a' })]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={() => undefined}
        showOnChart={false}
        onShowOnChartChange={onToggle}
      />,
    )
    // Ocultarlos del gráfico no los saca de la lista.
    expect(view.getByText('1 aviso detectado')).toBeTruthy()
    fireEvent.click(view.getByRole('button', { name: 'Mostrar en gráfico' }))
    expect(onToggle).toHaveBeenLastCalledWith(true)
  })

  it('mantiene fija la cabecera y limita el scroll a la lista', () => {
    const { container } = render(
      <ECGFindingsPanel
        annotations={[annotation({})]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={() => undefined}
        className="h-full"
      />,
    )

    const panel = container.querySelector('[aria-label="Hallazgos ECG"]')
    expect(panel?.className).toContain('h-full')
    expect(panel?.querySelector('.overflow-y-auto')).not.toBeNull()
    expect(panel?.querySelector('h2')?.closest('.overflow-y-auto')).toBeNull()
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

describe('ECGFindingsPanel — filtro de timeline', () => {
  it('filtra por IDs en orden cronológico, conserva respuestas y permite ver todos', () => {
    const a = annotation({ id: 'a', startMs: 1_000 })
    const b = annotation({ id: 'b', startMs: 2_000, severity: 'critical' })
    const response = annotation({
      id: 'reply',
      kind: 'patient_report',
      startMs: 1_500,
      linkedAnnotationId: 'a',
    })
    const onGroupChange = vi.fn()
    const onSelect = vi.fn()
    const { container, rerender } = render(
      <ECGFindingsPanel
        annotations={[b, a, response, annotation({ id: 'external' })]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={onSelect}
        selectedGroup={{
          kind: 'lead_off',
          startMs: 0,
          endMs: 5_000,
          severity: 'critical',
          annotationIds: ['a', 'b'],
        }}
        onGroupChange={onGroupChange}
      />,
    )
    const view = within(container)
    expect(view.getByText('2 avisos detectados')).toBeTruthy()
    const cards = container.querySelectorAll('[data-annotation-card]')
    expect([...cards].map((card) => card.getAttribute('data-annotation-card'))).toEqual(['a', 'b'])
    expect(within(cards[0] as HTMLElement).getByText('Respuesta del paciente')).toBeTruthy()
    fireEvent.click(cards[0].querySelector('button')!)
    expect(onSelect).toHaveBeenCalledWith(a)
    fireEvent.click(view.getByRole('button', { name: 'Ver todos' }))
    expect(onGroupChange).toHaveBeenCalledWith(null)
    rerender(
      <ECGFindingsPanel
        annotations={[b, a, response, annotation({ id: 'external' })]}
        recordingStartMs={0}
        selectedAnnotationId="a"
        onAnnotationSelect={onSelect}
      />,
    )
    expect(view.getByText('3 avisos detectados')).toBeTruthy()
    expect(
      container.querySelector('[data-annotation-card]')?.getAttribute('data-annotation-card'),
    ).toBe('b')
  })

  it('actualiza los datos, elimina integrantes desaparecidos y vuelve a todos si el grupo queda vacío', () => {
    const group = {
      kind: 'lead_off',
      startMs: 0,
      endMs: 5_000,
      severity: 'medium' as const,
      annotationIds: ['a', 'b'],
    }
    const onGroupChange = vi.fn()
    const { container, rerender } = render(
      <ECGFindingsPanel
        annotations={[annotation({ id: 'a', description: 'Actualizado' })]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={vi.fn()}
        selectedGroup={group}
        onGroupChange={onGroupChange}
      />,
    )
    expect(within(container).getByText('Actualizado')).toBeTruthy()
    expect(onGroupChange).toHaveBeenLastCalledWith({ ...group, annotationIds: ['a'] })
    rerender(
      <ECGFindingsPanel
        annotations={[annotation({ id: 'external' })]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={vi.fn()}
        selectedGroup={group}
        onGroupChange={onGroupChange}
      />,
    )
    expect(onGroupChange).toHaveBeenLastCalledWith(null)
    expect(within(container).queryByRole('button', { name: 'Ver todos' })).toBeNull()
    expect(container.querySelector('[data-annotation-card="external"]')).toBeTruthy()
  })
})

it('reclasifica desde el lápiz sin navegar y refresca los hallazgos', async () => {
  const finding: StudyFinding = {
    id: 'finding-edit',
    kind: 'ventricular_ectopy',
    category: 'clinical',
    severity: 'medium',
    startEpochMs: 10_000,
    endEpochMs: 20_000,
    startOffsetMs: 10_000,
    endOffsetMs: 20_000,
    confidenceScore: 0.9,
    modelVersion: 'ml-1',
    validationStatus: 'pending',
    reclassifiable: true,
  }
  const findings: StudyFindings = {
    studyId: 'study-edit',
    sampleRate: 500,
    sampleCount: 10_000,
    durationMs: 20_000,
    modelVersion: 'ml-1',
    quality: {
      analyzableRatio: 1,
      goodRatio: 1,
      marginalRatio: 0,
      badRatio: 0,
      evaluatedMs: 20_000,
    },
    groups: [
      {
        key: 'v',
        kind: finding.kind,
        category: finding.category,
        severity: finding.severity,
        occurrences: 1,
        firstOffsetMs: 10_000,
        lastOffsetMs: 20_000,
        firstEpochMs: 10_000,
        lastEpochMs: 20_000,
        items: [finding],
      },
    ],
    ungrouped: [{ ...finding, id: 'manual', reclassifiable: false }],
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  const post = vi
    .spyOn(api, 'post')
    .mockResolvedValue({ data: { finding, holterMetrics: { status: 'pending' } } })
  const onSelect = vi.fn()
  const { container, unmount } = render(
    <QueryClientProvider client={client}>
      <ECGFindingsPanel
        annotations={[
          annotation({
            id: finding.id,
            kind: finding.kind,
            category: 'clinical',
            startMs: 10_000,
            endMs: 20_000,
          }),
          annotation({ id: 'manual' }),
          annotation({ id: 'patient', kind: 'patient_report', category: 'patient_marker' }),
        ]}
        recordingStartMs={0}
        selectedAnnotationId={null}
        onAnnotationSelect={onSelect}
        reclassification={{ studyId: 'study-edit', findings }}
      />
    </QueryClientProvider>,
  )
  try {
    const buttons = within(container).getAllByRole('button', { name: /^Reclasificar/ })
    expect(buttons).toHaveLength(1)
    fireEvent.click(buttons[0])
    expect(onSelect).not.toHaveBeenCalled()
    const dialog = within(document.body).getByRole('dialog', { name: 'Reclasificar hallazgo' })
    fireEvent.click(within(dialog).getByRole('radio', { name: /Marcar como otro hallazgo/ }))
    fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Tipo de hallazgo' }), {
      key: 'ArrowDown',
    })
    fireEvent.click(screen.getByRole('option', { name: 'Extrasístoles supraventriculares (S)' }))
    fireEvent.change(within(dialog).getByLabelText('Nota (opcional)'), {
      target: { value: 'Revisado en la señal' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Guardar corrección' }))
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        '/studies/study-edit/findings/finding-edit/reclassification',
        {
          action: 'relabel',
          kind: 'supraventricular_ectopy',
          severity: 'medium',
          note: 'Revisado en la señal',
        },
      ),
    )
    await waitFor(() => expect(within(document.body).queryByRole('dialog')).toBeNull())
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['studies', 'study-edit', 'findings'] })
  } finally {
    unmount()
    post.mockRestore()
    client.clear()
  }
})
