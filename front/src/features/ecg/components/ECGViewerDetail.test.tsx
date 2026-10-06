// @vitest-environment jsdom

import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGDetailSource, ECGSignal } from '../types'

const uPlotMock = vi.hoisted(() => ({ instances: [] as unknown[] }))

/** 2 px/mm contra los 500 px del mock: 25 mm/s dan exactamente 10 s. */
vi.mock('../paperScale', async () => {
  const actual = await vi.importActual<typeof import('../paperScale')>('../paperScale')
  return { ...actual, measurePxPerMm: () => 2 }
})

vi.mock('uplot', () => {
  type Hook = (plot: MockUPlot, scaleKey: string) => void
  interface MockOptions {
    hooks?: { setScale?: Hook[] }
  }

  class MockUPlot {
    readonly over = document.createElement('div')
    readonly scales = {
      x: { min: null as number | null, max: null as number | null },
      y: { min: null as number | null, max: null as number | null },
    }
    readonly cursor = { left: -1, top: -1 }
    readonly options: MockOptions
    data: unknown[]
    redraws = 0

    constructor(options: MockOptions, data: unknown[], container: HTMLElement) {
      this.options = options
      this.data = data
      Object.defineProperties(this.over, {
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
    setData(data: unknown[]) {
      this.data = data
    }
    setSize() {}
    setCursor() {}
    redraw() {
      this.redraws++
    }
    destroy() {
      this.over.remove()
    }
    valToPos(value: number) {
      return value
    }
    posToVal(value: number) {
      return value
    }
  }

  return { default: MockUPlot }
})

import { ECGViewer } from './ECGViewer'

interface Plot {
  scales: { x: { min: number; max: number } }
  data: [ArrayLike<number>, ArrayLike<number | null>]
  redraws: number
  setScale: (key: string, limits: { min: number; max: number }) => void
}

const plot = () => uPlotMock.instances.at(-1) as Plot
const chart = () => document.querySelector<HTMLElement>('[aria-label="Gráfico ECG interactivo"]')!

const START = 1_700_000_000_000
const RATE = 500
const BUCKET = 256
/** Diez minutos: de lejos el resumen alcanza, a 25 mm/s no. */
const SAMPLES = 10 * 60 * RATE

/** Un resumen de `BUCKET` muestras por balde, en 0, y muestras de verdad en 1. */
function signal(detail?: ECGDetailSource): ECGSignal {
  const points = Math.floor(SAMPLES / BUCKET) * 2
  const msPerPoint = ((BUCKET / 2) * 1000) / RATE
  return {
    sampleRate: RATE,
    durationMs: (SAMPLES / RATE) * 1000,
    samples: new Float32Array(points),
    startTimestamp: START,
    timestampsMs: Float64Array.from({ length: points }, (_, i) => START + i * msPerPoint),
    gapIndices: [],
    timeline: [],
    annotations: [],
    detail,
    metadata: {
      formatVersion: 3,
      encoding: 'float32-le',
      sampleCount: SAMPLES,
      isSimulated: false,
      overviewSamplesPerBucket: BUCKET,
    },
  }
}

function detailSource(): ECGDetailSource & { load: ReturnType<typeof vi.fn> } {
  return {
    sampleCount: SAMPLES,
    load: vi.fn(async (startSample: number, endSample: number) => {
      const count = endSample - startSample
      return {
        startSample,
        endSample,
        samples: new Float32Array(count).fill(1),
        timestampsMs: Float64Array.from(
          { length: count },
          (_, i) => START + ((startSample + i) * 1000) / RATE,
        ),
        gapIndices: [],
      }
    }),
  }
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200)
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  uPlotMock.instances.length = 0
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
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('ECGViewer — muestras de cerca', () => {
  it('a escala clínica cambia el resumen por las muestras del tramo visible', async () => {
    const detail = detailSource()
    render(<ECGViewer signal={signal(detail)} />)
    expect(chart().dataset.trace).toBe('overview')

    await settle()

    // 10 s visibles al final del estudio, más media ventana a la izquierda.
    expect(detail.load).toHaveBeenCalledOnce()
    const [start, end] = detail.load.mock.calls[0]
    expect(end).toBe(SAMPLES)
    expect(start).toBeLessThanOrEqual(SAMPLES - 10 * RATE - 5 * RATE + BUCKET)
    expect(chart().dataset.trace).toBe('samples')
    const [xs, ys] = plot().data
    // Muestras en 1 donde se ven, el resumen en 0 fuera del tramo.
    const visible = Array.from(xs).findIndex((x) => x >= 590)
    expect(ys[visible]).toBe(1)
    expect(ys[0]).toBe(0)
    expect(plot().redraws).toBeGreaterThan(0)
  })

  it('el eje queda ordenado al empalmar', async () => {
    render(<ECGViewer signal={signal(detailSource())} />)
    await settle()

    const [xs] = plot().data
    for (let i = 1; i < xs.length; i++) expect(xs[i]).toBeGreaterThanOrEqual(xs[i - 1])
  })

  it('al alejarse vuelve al resumen', async () => {
    const original = signal(detailSource())
    render(<ECGViewer signal={original} />)
    await settle()
    expect(chart().dataset.trace).toBe('samples')

    act(() => plot().setScale('x', { min: 0, max: 600 }))
    await settle()

    expect(chart().dataset.trace).toBe('overview')
    expect(plot().data[1]).toBe(original.samples)
  })

  it('desplazarse dentro de lo ya cargado no vuelve a descargar', async () => {
    const detail = detailSource()
    render(<ECGViewer signal={signal(detail)} />)
    await settle()

    act(() => plot().setScale('x', { min: 588, max: 598 }))
    await settle()

    expect(detail.load).toHaveBeenCalledOnce()
  })

  it('si la descarga falla se queda con el resumen', async () => {
    const detail = detailSource()
    detail.load.mockRejectedValueOnce(new Error('403'))
    const original = signal(detail)
    render(<ECGViewer signal={original} />)
    await settle()

    expect(chart().dataset.trace).toBe('overview')
    expect(plot().data[1]).toBe(original.samples)
  })

  it('sin fuente de detalle no pide nada y dibuja lo que hay', async () => {
    const original = signal()
    render(<ECGViewer signal={original} />)
    await settle()

    expect(chart().dataset.trace).toBe('overview')
    expect(plot().data[1]).toBe(original.samples)
  })
})
