// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGSignal } from '../types'
import { MAX_TIMELINE_MS } from '../timeline'
import { ECGMinimap } from './ECGMinimap'

const HOUR = 3_600_000
const T0 = 1_700_000_000_000
const canvasContext = {
  beginPath: vi.fn(),
  fillRect: vi.fn(),
  lineTo: vi.fn(),
  moveTo: vi.fn(),
  scale: vi.fn(),
  stroke: vi.fn(),
  fillStyle: '',
  strokeStyle: '',
  lineWidth: 1,
}
let resize: () => void
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('PointerEvent', MouseEvent)
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(1_000)
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    value: () => canvasContext,
  })
  globalThis.ResizeObserver = class {
    constructor(callback: ResizeObserverCallback) {
      resize = () => callback([], this)
    }
    observe() {}
    disconnect() {}
    unobserve() {}
  }
  HTMLElement.prototype.setPointerCapture = vi.fn()
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true)
  HTMLElement.prototype.releasePointerCapture = vi.fn()
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function windowRange() {
  const timeline = screen.getByTestId('ecg-timeline')
  return [Number(timeline.dataset.startMs), Number(timeline.dataset.endMs)]
}
function navigator() {
  const node = screen.getByLabelText('Navegación general del ECG')
  node.getBoundingClientRect = () => ({ left: 0, width: 1_000 }) as DOMRect
  return node
}

describe('ECGMinimap', () => {
  it('deja vacías las columnas del eje temporal en las que no hubo señal', () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(10)
    const withGap = signal()
    withGap.samples = Float32Array.from([0, 1, 0.5, 0])
    withGap.timestampsMs = Float64Array.from([T0, T0 + 1_000, T0 + 9_000, T0 + 10_000])
    withGap.gapIndices = [2]
    render(<ECGMinimap signal={withGap} viewport={null} onViewportChange={vi.fn()} />)
    expect(canvasContext.moveTo.mock.calls.map(([x]) => x)).toEqual([0.5, 1.5, 9.5])
  })

  it('selecciona un evento aislado y dibuja solamente su banda en la vista previa', () => {
    const onSelect = vi.fn()
    const data = signal()
    data.annotations.push({
      ...data.annotations[0],
      id: 'other',
      startMs: T0 + 8_000,
      endMs: T0 + 9_000,
    })
    render(
      <ECGMinimap
        signal={data}
        viewport={null}
        onViewportChange={vi.fn()}
        selectedAnnotationId="event-1"
        onAnnotationSelect={onSelect}
      />,
    )
    const marker = screen.getAllByRole('button', { name: /Electrodo desconectado, 1 hallazgo/ })[0]
    expect(marker.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(marker)
    expect(onSelect).toHaveBeenCalledWith(data.annotations[0])
    expect(screen.getByTestId('timeline-annotation-range-event-1')).toBeTruthy()
    expect(screen.queryByTestId('timeline-annotation-range-other')).toBeNull()
  })

  it('abre grupos con contador sin mover el ECG y se adapta al ancho', async () => {
    const onGroupSelect = vi.fn()
    const onViewportChange = vi.fn()
    const onSelect = vi.fn()
    const data = signal()
    data.annotations = Array.from({ length: 100 }, (_, i) => ({
      ...data.annotations[0],
      id: String(i),
      startMs: T0 + i,
      endMs: T0 + i,
    }))
    const { rerender } = render(
      <ECGMinimap
        signal={data}
        viewport={null}
        onViewportChange={onViewportChange}
        onGroupSelect={onGroupSelect}
        onAnnotationSelect={onSelect}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /100 hallazgos/ }))
    expect(onGroupSelect.mock.calls[0][0].annotationIds).toHaveLength(100)
    expect(onViewportChange).not.toHaveBeenCalled()
    expect(onSelect).not.toHaveBeenCalled()
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(64)
    const { act } = await import('@testing-library/react')
    act(() => resize())
    rerender(<ECGMinimap signal={data} viewport={null} onViewportChange={onViewportChange} />)
    expect(screen.getByRole('button', { name: /100 hallazgos/ })).toBeTruthy()
  })

  it('usa una franja compacta y un menú por tipo cuando coinciden muchos hallazgos', () => {
    const onGroupSelect = vi.fn()
    const onViewportChange = vi.fn()
    const data = signal()
    const kinds = [
      'pause',
      'lead_off',
      'noise',
      'morphology_anomaly',
      'tachycardia',
      'patient_report',
    ]
    data.annotations = kinds.flatMap((kind, index) =>
      [0, 1].map((offset) => ({
        ...data.annotations[0],
        id: `${kind}-${offset}`,
        kind,
        severity: index === 0 ? ('critical' as const) : ('medium' as const),
        startMs: T0 + 300 + index * 10 + offset,
        endMs: T0 + 400,
      })),
    )
    render(
      <ECGMinimap
        signal={data}
        viewport={null}
        onViewportChange={onViewportChange}
        selectedAnnotationId="lead_off-0"
        onGroupSelect={onGroupSelect}
      />,
    )
    const strip = screen.getByLabelText('Avisos del estudio')
    expect(strip.className).toContain('h-14')
    expect(strip.querySelectorAll('button')).toHaveLength(2)
    expect(within(strip).queryByText('Electrodo desconectado')).toBeNull()
    const overflow = within(strip).getByRole('button', {
      name: 'Ver otros 5 tipos de hallazgo en este tramo',
    })
    expect(overflow.className).toContain('ring-2')
    fireEvent.keyDown(overflow, { key: 'ArrowDown' })
    const menu = screen.getByRole('menu')
    expect(within(menu).getAllByRole('menuitem')).toHaveLength(5)
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Electrodo desconectado/ }))
    expect(onGroupSelect.mock.calls[0][0].annotationIds).toEqual(['lead_off-0', 'lead_off-1'])
    expect(onViewportChange).not.toHaveBeenCalled()
  })

  it('ocultar avisos elimina filas y banda seleccionada', () => {
    const { container } = render(
      <ECGMinimap
        signal={signal()}
        viewport={null}
        onViewportChange={vi.fn()}
        selectedAnnotationId="event-1"
        showAnnotations={false}
      />,
    )
    expect(screen.queryByLabelText('Avisos del estudio')).toBeNull()
    expect(container.innerHTML).not.toContain('--ecg-alert-')
  })

  it('limita el contexto a 12 horas y permite explorar sin modificar el trazado', () => {
    const onViewportChange = vi.fn()
    const data = signal(30 * HOUR)
    const viewport = { startMs: T0, endMs: T0 + 1_000 }
    const { rerender } = render(
      <ECGMinimap signal={data} viewport={viewport} onViewportChange={onViewportChange} />,
    )
    expect(windowRange()).toEqual([T0, T0 + MAX_TIMELINE_MS])
    fireEvent.click(screen.getByRole('button', { name: 'Tramo siguiente de la timeline' }))
    expect(windowRange()).toEqual([T0 + 6 * HOUR, T0 + 18 * HOUR])
    expect(onViewportChange).not.toHaveBeenCalled()
    expect(screen.queryByTestId('timeline-viewport')).toBeNull()
    rerender(
      <ECGMinimap signal={{ ...data }} viewport={viewport} onViewportChange={onViewportChange} />,
    )
    expect(windowRange()).toEqual([T0 + 6 * HOUR, T0 + 18 * HOUR])
    rerender(
      <ECGMinimap
        signal={data}
        viewport={{ startMs: T0 + 25 * HOUR, endMs: T0 + 25 * HOUR + 1_000 }}
        onViewportChange={onViewportChange}
      />,
    )
    expect(windowRange()).toEqual([T0 + 18 * HOUR, T0 + 30 * HOUR])
  })

  it('sigue el centro de un ECG más ancho que la timeline sin ampliar el rango', () => {
    const data = signal(48 * HOUR)
    const { rerender } = render(
      <ECGMinimap
        signal={data}
        viewport={{ startMs: T0, endMs: T0 + 24 * HOUR }}
        onViewportChange={vi.fn()}
      />,
    )
    rerender(
      <ECGMinimap
        signal={data}
        viewport={{ startMs: T0 + 12 * HOUR, endMs: T0 + 36 * HOUR }}
        onViewportChange={vi.fn()}
      />,
    )
    const [start, end] = windowRange()
    expect(end - start).toBe(MAX_TIMELINE_MS)
    expect((start + end) / 2).toBe(T0 + 24 * HOUR)
    expect(screen.getByTestId('timeline-viewport').style.width).toBe('100%')
  })

  it('mantiene la duración lógica de un viewport más angosto que el selector visual', () => {
    const onViewportChange = vi.fn()
    render(
      <ECGMinimap
        signal={signal(30 * HOUR)}
        viewport={{ startMs: T0, endMs: T0 + 1_000 }}
        onViewportChange={onViewportChange}
      />,
    )
    fireEvent.pointerDown(navigator(), { clientX: 500, button: 0 })
    const next = onViewportChange.mock.calls[0][0]
    expect(next.endMs - next.startMs).toBe(1_000)
    expect((next.endMs + next.startMs) / 2).toBe(T0 + 6 * HOUR)
  })

  it('navega con teclado y respeta los límites del estudio', () => {
    const onViewportChange = vi.fn()
    render(
      <ECGMinimap
        signal={signal()}
        viewport={{ startMs: T0, endMs: T0 + 1_000 }}
        onViewportChange={onViewportChange}
      />,
    )
    fireEvent.keyDown(navigator(), { key: 'ArrowLeft' })
    expect(onViewportChange).toHaveBeenLastCalledWith({ startMs: T0, endMs: T0 + 1_000 })
    fireEvent.keyDown(navigator(), { key: 'ArrowRight' })
    expect(onViewportChange).toHaveBeenLastCalledWith({ startMs: T0 + 500, endMs: T0 + 1_500 })
    expect(
      screen
        .getByRole('button', { name: 'Tramo siguiente de la timeline' })
        .hasAttribute('disabled'),
    ).toBe(true)
  })

  it('arrastra la timeline sin mover el ECG y cancela el clic posterior al arrastre', () => {
    const onViewportChange = vi.fn()
    const onGroupSelect = vi.fn()
    const data = signal(30 * HOUR)
    data.annotations.push({ ...data.annotations[0], id: 'second' })
    render(
      <ECGMinimap
        signal={data}
        viewport={{ startMs: T0, endMs: T0 + 1_000 }}
        onViewportChange={onViewportChange}
        onGroupSelect={onGroupSelect}
      />,
    )
    navigator()
    const row = screen.getByLabelText('Avisos del estudio')
    fireEvent.pointerDown(row, { clientX: 900, button: 0 })
    fireEvent.pointerMove(row, { clientX: 400 })
    expect(windowRange()).toEqual([T0 + 6 * HOUR, T0 + 18 * HOUR])
    expect(row.isConnected).toBe(true)
    fireEvent.pointerMove(row, { clientX: 0 })
    expect(windowRange()).toEqual([T0 + 10.8 * HOUR, T0 + 22.8 * HOUR])
    fireEvent.pointerMove(row, { clientX: 900 })
    fireEvent.pointerUp(row)
    const currentMarker = screen.getByRole('button', { name: /2 hallazgos/ })
    fireEvent.click(currentMarker, { detail: 1 })
    expect(onViewportChange).not.toHaveBeenCalled()
    expect(onGroupSelect).not.toHaveBeenCalled()
    // La activación por teclado no hereda la supresión del clic de un drag.
    fireEvent.click(currentMarker, { detail: 0 })
    expect(onGroupSelect).toHaveBeenCalledOnce()
  })
})

function signal(durationMs = 10_000): ECGSignal {
  return {
    sampleRate: 250,
    durationMs,
    samples: new Float32Array([0, 1, 0]),
    startTimestamp: T0,
    timestampsMs: Float64Array.from([T0, T0 + durationMs / 2, T0 + durationMs]),
    gapIndices: [],
    timeline: [],
    annotations: [
      {
        id: 'event-1',
        kind: 'lead_off',
        category: 'signal_quality',
        severity: 'medium',
        startMs: T0 + 2_000,
        endMs: T0 + 4_000,
        confidenceScore: null,
        linkedAnnotationId: null,
        description: null,
      },
    ],
  }
}
