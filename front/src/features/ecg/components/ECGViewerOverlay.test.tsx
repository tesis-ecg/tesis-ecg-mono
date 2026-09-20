// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGSignal, ECGViewerHandle } from '../types'

const uPlotMock = vi.hoisted(() => ({ instances: [] as unknown[] }))

/**
 * px por milímetro fijos, para que la aritmética de la escala sea exacta.
 *
 * jsdom no hace layout, así que `measurePxPerMm` caería en su fallback nominal
 * (96/25,4 = 3,7795…) y los segundos visibles saldrían con coma. Con 2 px/mm y
 * los 500 px de ancho del mock, 25 mm/s dan exactamente 10 s de ventana — que es
 * la tira de papel clínica y lo que estos tests venían asumiendo.
 */
vi.mock('../paperScale', async () => {
  const actual = await vi.importActual<typeof import('../paperScale')>('../paperScale')
  return { ...actual, measurePxPerMm: () => 2 }
})

vi.mock('uplot', () => {
  type ScaleHook = (plot: MockUPlot, scaleKey: string) => void
  interface MockOptions {
    hooks?: { setScale?: ScaleHook[] }
  }

  class MockUPlot {
    readonly over = document.createElement('div')
    readonly scales = {
      x: { min: null as number | null, max: null as number | null },
      y: { min: null as number | null, max: null as number | null },
    }
    private readonly options: MockOptions

    constructor(options: MockOptions, _data: unknown, container: HTMLElement) {
      this.options = options
      Object.defineProperties(this.over, {
        offsetLeft: { configurable: true, value: 60 },
        offsetTop: { configurable: true, value: 40 },
        clientWidth: { configurable: true, value: 500 },
        clientHeight: { configurable: true, value: 300 },
      })
      this.over.getBoundingClientRect = () =>
        ({ left: 0, right: 500, top: 0, bottom: 300, width: 500, height: 300 }) as DOMRect
      container.appendChild(this.over)
      uPlotMock.instances.push(this)
    }

    setScale(scaleKey: string, limits: { min: number; max: number }) {
      if (scaleKey === 'x') this.scales.x = limits
      if (scaleKey === 'y') this.scales.y = limits
      for (const hook of this.options.hooks?.setScale ?? []) hook(this, scaleKey)
    }

    setSize() {}
    redraw() {}
    destroy() {
      this.over.remove()
    }

    valToPos(value: number) {
      return value
    }

    posToVal(value: number) {
      const { min, max } = this.scales.x
      if (min == null || max == null) return value
      return min + (value / 500) * (max - min)
    }
  }

  return { default: MockUPlot }
})

import { ECGViewer } from './ECGViewer'

beforeEach(() => {
  uPlotMock.instances.length = 0
  // La spec garantiza un callback inicial por elemento observado, y de eso
  // depende el encuadre del visor: un doble que no lo dispare no modela un
  // `ResizeObserver`.
  globalThis.ResizeObserver = class {
    private readonly callback: ResizeObserverCallback
    constructor(callback: ResizeObserverCallback) {
      this.callback = callback
    }
    observe(target: Element) {
      this.callback(
        [{ target, contentRect: { width: 500 } } as unknown as ResizeObserverEntry],
        this as unknown as ResizeObserver,
      )
    }
    disconnect() {}
    unobserve() {}
  } as unknown as typeof ResizeObserver
  Object.defineProperties(HTMLElement.prototype, {
    setPointerCapture: { configurable: true, value: vi.fn() },
    releasePointerCapture: { configurable: true, value: vi.fn() },
  })
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('ECGViewer annotation overlay', () => {
  it('muestra icono y texto para un rango visible y permite seleccionarlo', async () => {
    const onSelect = vi.fn()
    render(
      <ECGViewer signal={signal()} selectedAnnotationId="event-1" onAnnotationSelect={onSelect} />,
    )

    const label = await screen.findByRole('button', {
      name: 'Fibrilación auricular, severidad crítica',
    })
    expect(label.getAttribute('aria-pressed')).toBe('true')
    expect(label.className).toContain('cursor-pointer')
    expect(label.getAttribute('style')).toContain('--ecg-alert-critical-bg')
    expect(label.getAttribute('style')).toContain('left: 170px')

    fireEvent.click(label)
    expect(onSelect).toHaveBeenCalledWith(signal().annotations[0])
  })

  it('jumpTo conserva el ancho del viewport al centrar y al recortar en un extremo', () => {
    const ref = createRef<ECGViewerHandle>()
    render(<ECGViewer ref={ref} signal={signal()} />)
    const plot = uPlotMock.instances.at(-1) as {
      scales: { x: { min: number; max: number } }
    }

    expect(plot.scales.x).toEqual({ min: 50, max: 60 })
    act(() => ref.current?.jumpTo(1_700_000_002_000))
    expect(plot.scales.x).toEqual({ min: 0, max: 10 })

    act(() => ref.current?.zoomToRange(1_700_000_020_000, 1_700_000_022_000))
    act(() => ref.current?.jumpTo(1_700_000_045_000))
    expect(plot.scales.x).toEqual({ min: 44, max: 46 })
  })

  it('conserva el viewport cuando la señal se actualiza', () => {
    const ref = createRef<ECGViewerHandle>()
    const { rerender } = render(<ECGViewer ref={ref} signal={signal()} />)

    act(() => ref.current?.zoomToRange(1_700_000_020_000, 1_700_000_022_000))
    const updatedSignal = signal()
    updatedSignal.durationMs = 70_000
    updatedSignal.samples = new Float32Array(70)
    updatedSignal.timestampsMs = Float64Array.from(
      { length: 70 },
      (_, i) => updatedSignal.startTimestamp + i * 1_000,
    )
    rerender(<ECGViewer ref={ref} signal={updatedSignal} />)

    const refreshedPlot = uPlotMock.instances.at(-1) as {
      scales: { x: { min: number; max: number } }
    }
    expect(refreshedPlot.scales.x).toEqual({ min: 20, max: 22 })
  })

  it('no consume el primer click real después de un pan sin click sintético', () => {
    vi.useFakeTimers()
    const onSelect = vi.fn()
    render(<ECGViewer signal={signal()} onAnnotationSelect={onSelect} />)
    const graph = screen.getByLabelText('Gráfico ECG interactivo')

    fireEvent.pointerDown(graph, { button: 0, pointerId: 1, clientX: 100 })
    fireEvent.pointerMove(graph, { pointerId: 1, clientX: 120 })
    fireEvent.pointerUp(graph, { pointerId: 1, clientX: 120 })
    act(() => vi.runAllTimers())
    fireEvent.click(graph, { clientX: 250 })

    expect(onSelect).toHaveBeenCalledTimes(1)
    expect(onSelect).toHaveBeenCalledWith(signal().annotations[0])
  })
})

function signal(): ECGSignal {
  const startTimestamp = 1_700_000_000_000
  return {
    sampleRate: 1,
    durationMs: 60_000,
    samples: new Float32Array(60),
    startTimestamp,
    // Una grabación sin cortes: un solo tramo y ningún hueco que dibujar.
    timestampsMs: Float64Array.from({ length: 60 }, (_, i) => startTimestamp + i * 1000),
    gapIndices: [],
    timeline: [],
    annotations: [
      {
        id: 'event-1',
        kind: 'afib',
        category: 'clinical',
        severity: 'critical',
        startMs: startTimestamp + 45_000,
        endMs: startTimestamp + 65_000,
        confidenceScore: 0.97,
        linkedAnnotationId: null,
        description: null,
      },
    ],
  }
}
