// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { forwardRef, useImperativeHandle } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useEcgSession } from '../hooks/useEcgSession'
import type { ECGAnnotation, ECGSignal, ECGViewerHandle } from '../types'
import { ECGExpandedView } from './ECGExpandedView'
import { EcgSessionProvider } from './EcgSessionRoute'

const jumpTo = vi.hoisted(() => vi.fn())
vi.mock('./ECGViewer', () => ({
  ECGViewer: forwardRef(function MockViewer(
    {
      onAnnotationSelect,
      signal,
    }: {
      onAnnotationSelect: (annotation: ECGAnnotation) => void
      signal: ECGSignal
    },
    ref,
  ) {
    useImperativeHandle(ref, () => ({ jumpTo }) as unknown as ECGViewerHandle)
    return (
      <button onClick={() => onAnnotationSelect(signal.annotations[2])}>
        Hallazgo externo del trazado
      </button>
    )
  }),
}))

const T0 = 1_700_000_000_000
const annotations: ECGAnnotation[] = ['a', 'b', 'external', 'reply'].map((id, i) => ({
  id,
  kind: i === 2 ? 'noise' : i === 3 ? 'patient_report' : 'lead_off',
  category: 'signal_quality',
  severity: 'medium',
  startMs: T0 + (i + 1) * 1_000,
  endMs: T0 + (i + 1) * 1_000,
  confidenceScore: null,
  linkedAnnotationId: i === 3 ? 'a' : null,
  description: null,
}))
const signal: ECGSignal = {
  sampleRate: 500,
  durationMs: 30 * 3_600_000,
  startTimestamp: T0,
  samples: new Float32Array([0, 1, 0]),
  timestampsMs: Float64Array.from([T0, T0 + 1_000, T0 + 2_000]),
  gapIndices: [],
  timeline: [],
  annotations,
}

function ConnectedViewer() {
  const session = useEcgSession()
  return (
    <ECGExpandedView
      signal={signal}
      initialViewport={session.viewport}
      initialCursorMs={null}
      onViewportChange={session.setViewport}
      onCursorChange={session.setCursorMs}
      onBack={vi.fn()}
      initialSelectedAnnotationId={session.selectedAnnotationId}
      onAnnotationSelect={session.setSelectedAnnotationId}
      paperSpeed={25}
      amplitude={20}
      onPaperSpeedChange={vi.fn()}
      onAmplitudeChange={vi.fn()}
      selectedGroup={session.selectedGroup}
      onGroupChange={session.setSelectedGroup}
      timelineWindow={session.timelineWindow}
      onTimelineWindowChange={session.setTimelineWindow}
    />
  )
}

beforeEach(() => {
  jumpTo.mockClear()
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1_000)
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  }
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('timeline y sidebar conectadas', () => {
  it('abre el grupo sin saltar, selecciona integrantes y respuestas y quita el filtro al elegir un evento externo', () => {
    render(
      <EcgSessionProvider>
        <ConnectedViewer />
      </EcgSessionProvider>,
    )
    const sidebar = within(screen.getByRole('region', { name: 'Hallazgos ECG' }))
    fireEvent.click(screen.getByRole('button', { name: /Electrodo desconectado, 2 hallazgos/ }))
    expect(jumpTo).not.toHaveBeenCalled()
    expect(sidebar.getByText('2 avisos detectados')).toBeTruthy()
    expect(sidebar.queryByText('Ruido / artefacto')).toBeNull()
    fireEvent.click(sidebar.getAllByRole('button', { name: /Electrodo desconectado/ })[0])
    expect(jumpTo).toHaveBeenLastCalledWith(annotations[0].startMs)
    expect(sidebar.getByRole('button', { name: 'Ver todos' })).toBeTruthy()
    fireEvent.click(sidebar.getByRole('button', { name: /Respuesta del paciente/ }))
    expect(jumpTo).toHaveBeenLastCalledWith(annotations[3].startMs)
    expect(sidebar.getByRole('button', { name: 'Ver todos' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Tramo siguiente de la timeline' }))
    expect(sidebar.getByRole('button', { name: 'Ver todos' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Hallazgo externo del trazado' }))
    expect(sidebar.queryByRole('button', { name: 'Ver todos' })).toBeNull()
    expect(sidebar.getByText('3 avisos detectados')).toBeTruthy()
    expect(jumpTo).toHaveBeenLastCalledWith(annotations[2].startMs)
  })
})
