// @vitest-environment jsdom

import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGDetailSource, ECGDetailWindow, ECGSignal } from '../types'

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
    /** Con lo que se creó: lo primero que se pinta, antes de cualquier `setData`. */
    readonly initialData: unknown[]
    data: unknown[]
    redraws = 0

    constructor(options: MockOptions, data: unknown[], container: HTMLElement) {
      this.options = options
      this.initialData = data
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
  scales: { x: { min: number; max: number }; y: { min: number; max: number } }
  initialData: [ArrayLike<number>, ArrayLike<number | null>]
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
function signal(detail?: ECGDetailSource, sampleCount = SAMPLES): ECGSignal {
  const points = Math.floor(sampleCount / BUCKET) * 2
  const msPerPoint = ((BUCKET / 2) * 1000) / RATE
  return {
    sampleRate: RATE,
    durationMs: (sampleCount / RATE) * 1000,
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
      sampleCount,
      isSimulated: false,
      overviewSamplesPerBucket: BUCKET,
    },
  }
}

function samplesWindow(startSample: number, endSample: number, value = 1): ECGDetailWindow {
  const count = endSample - startSample
  return {
    startSample,
    endSample,
    samples: new Float32Array(count).fill(value),
    timestampsMs: Float64Array.from(
      { length: count },
      (_, i) => START + ((startSample + i) * 1000) / RATE,
    ),
    gapIndices: [],
  }
}

function detailSource(sampleCount = SAMPLES): ECGDetailSource & {
  load: ReturnType<typeof vi.fn>
} {
  return {
    sampleCount,
    load: vi.fn(async (startSample: number, endSample: number) =>
      samplesWindow(startSample, endSample),
    ),
  }
}

/** El valor dibujado en el primer punto en o después de `second`. */
function drawnAt([xs, ys]: Plot['data'], second: number): number | null {
  return ys[Array.from(xs).findIndex((x) => x >= second)]
}

async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200)
  })
}

/** Deja resolver lo pendiente sin llegar a la espera de `updateDetail`. */
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1)
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

  it('volver dentro de lo ya cargado cancela la descarga pendiente y no la empalma', async () => {
    const detail = detailSource()
    render(<ECGViewer signal={signal(detail)} />)
    await settle()
    const shown = plot().data

    let pending: { signal?: AbortSignal; resolve: () => void } | null = null
    detail.load.mockImplementationOnce(
      (startSample: number, endSample: number, abort?: AbortSignal) =>
        new Promise<ECGDetailWindow>((resolve) => {
          pending = {
            signal: abort,
            resolve: () => resolve(samplesWindow(startSample, endSample, 2)),
          }
        }),
    )
    // Fuera de lo cargado: pide otro tramo, que tarda.
    act(() => plot().setScale('x', { min: 570, max: 580 }))
    await settle()
    expect(detail.load).toHaveBeenCalledTimes(2)

    // De vuelta adentro antes de que llegue.
    act(() => plot().setScale('x', { min: 590, max: 600 }))
    await settle()
    expect(pending!.signal?.aborted).toBe(true)

    await act(async () => pending!.resolve())
    expect(plot().data).toBe(shown)
    expect(drawnAt(plot().data, 590)).toBe(1)
  })

  it('el rango vertical sale de las muestras empalmadas, no del resumen', async () => {
    // Un resumen lejos de la señal: en un estudio largo el punto que queda en
    // pantalla es el máximo de un balde de 33 s, y centrar ahí cortaba la traza.
    const far = signal(detailSource())
    far.samples.fill(5)
    render(<ECGViewer signal={far} amplitude={10} />)
    expect((plot().scales.y.min + plot().scales.y.max) / 2).toBeCloseTo(5, 3)

    await settle()

    expect(chart().dataset.trace).toBe('samples')
    expect((plot().scales.y.min + plot().scales.y.max) / 2).toBeCloseTo(1, 3)
  })

  it('en amplitud automática el rango abarca las muestras, no los picos del resumen', async () => {
    const spiky = signal(detailSource())
    spiky.samples.forEach((_, i) => (spiky.samples[i] = i % 2 === 0 ? -5 : 5))
    render(<ECGViewer signal={spiky} amplitude="auto" />)
    await settle()

    const { min, max } = plot().scales.y
    expect(min).toBeLessThan(1)
    expect(max).toBeGreaterThan(1)
    expect(max - min).toBeLessThan(2)
  })

  it('un lote nuevo no vuelve al resumen: el gráfico nuevo arranca con las muestras', async () => {
    const { rerender } = render(<ECGViewer signal={signal(detailSource())} />)
    await settle()
    expect(chart().dataset.trace).toBe('samples')
    const instances = uPlotMock.instances.length

    // Otra señal del mismo estudio, como la que trae el polling.
    rerender(<ECGViewer signal={signal(detailSource())} />)
    // Mientras bajan sus muestras sigue dibujada la anterior.
    expect(uPlotMock.instances.length).toBe(instances)
    expect(chart().dataset.trace).toBe('samples')

    await flush()

    expect(uPlotMock.instances.length).toBe(instances + 1)
    expect(chart().dataset.trace).toBe('samples')
    expect(drawnAt(plot().initialData, 595)).toBe(1)
  })

  it('siguiendo lo último, el gráfico salta al lote nuevo ya con sus muestras', async () => {
    const { rerender } = render(<ECGViewer signal={signal(detailSource())} followLatest />)
    await settle()

    const longer = SAMPLES + 30 * RATE
    const nextDetail = detailSource(longer)
    rerender(<ECGViewer signal={signal(nextDetail, longer)} followLatest />)
    await flush()

    // Pidió el tramo del nuevo final, no el que se estaba mirando.
    expect(nextDetail.load).toHaveBeenCalledOnce()
    expect(nextDetail.load.mock.calls[0][1]).toBe(longer)
    expect(chart().dataset.trace).toBe('samples')
    expect(drawnAt(plot().initialData, longer / RATE - 5)).toBe(1)
    expect(plot().scales.x.max).toBeCloseTo(longer / RATE, 0)
  })

  it('si las muestras de la señal nueva no llegan, la muestra igual con el resumen', async () => {
    const { rerender } = render(<ECGViewer signal={signal(detailSource())} />)
    await settle()
    const instances = uPlotMock.instances.length

    const stuck = detailSource()
    stuck.load.mockImplementation(() => new Promise(() => {}))
    rerender(<ECGViewer signal={signal(stuck)} />)
    await flush()
    expect(uPlotMock.instances.length).toBe(instances)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3_000)
    })

    expect(uPlotMock.instances.length).toBe(instances + 1)
    expect(chart().dataset.trace).toBe('overview')
  })

  it('sin fuente de detalle no pide nada y dibuja lo que hay', async () => {
    const original = signal()
    render(<ECGViewer signal={original} />)
    await settle()

    expect(chart().dataset.trace).toBe('overview')
    expect(plot().data[1]).toBe(original.samples)
  })
})
