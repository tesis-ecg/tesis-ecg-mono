// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGSignal, ECGViewerHandle } from '../types'

const uPlotMock = vi.hoisted(() => ({ instances: [] as unknown[] }))

vi.mock('uplot', () => {
  type ScaleHook = (plot: MockUPlot, scaleKey: string) => void
  interface MockOptions {
    hooks?: { setScale?: ScaleHook[] }
  }

  class MockUPlot {
    readonly over = document.createElement('div')
    readonly scales = { x: { min: null as number | null, max: null as number | null } }
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
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  }
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
    annotations: [
      {
        id: 'event-1',
        kind: 'afib',
        category: 'clinical',
        severity: 'critical',
        startMs: startTimestamp + 45_000,
        endMs: startTimestamp + 65_000,
        confidenceScore: 0.97,
      },
    ],
  }
}
