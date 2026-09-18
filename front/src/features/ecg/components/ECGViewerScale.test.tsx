// @vitest-environment jsdom

import { act, cleanup, render } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGSignal, ECGViewerHandle } from '../types'

const uPlotMock = vi.hoisted(() => ({ instances: [] as unknown[], widthAtConstruction: 500 }))

/** 2 px/mm contra los 500 px del mock: 25 mm/s dan exactamente 10 s. */
vi.mock('../paperScale', async () => {
  const actual = await vi.importActual<typeof import('../paperScale')>('../paperScale')
  return { ...actual, measurePxPerMm: () => 2 }
})

vi.mock('uplot', () => {
  type Hook = (plot: MockUPlot, scaleKey: string) => void
  interface MockOptions {
    hooks?: { setScale?: Hook[] }
    scales?: { y?: { range?: (u: MockUPlot, min: number, max: number) => [number, number] } }
  }

  class MockUPlot {
    readonly over = document.createElement('div')
    readonly scales = {
      x: { min: null as number | null, max: null as number | null },
      y: { min: null as number | null, max: null as number | null },
    }
    readonly options: MockOptions
    width = 500

    constructor(options: MockOptions, _data: unknown, container: HTMLElement) {
      this.options = options
      this.applyWidth(uPlotMock.widthAtConstruction)
      container.appendChild(this.over)
      uPlotMock.instances.push(this)
    }

    applyWidth(width: number) {
      this.width = width
      Object.defineProperties(this.over, {
        offsetLeft: { configurable: true, value: 60 },
        offsetTop: { configurable: true, value: 40 },
        clientWidth: { configurable: true, value: width },
        clientHeight: { configurable: true, value: 300 },
      })
      this.over.getBoundingClientRect = () =>
        ({ left: 0, right: width, top: 0, bottom: 300, width, height: 300 }) as DOMRect
    }

    setScale(scaleKey: string, limits: { min: number; max: number }) {
      if (scaleKey === 'x') this.scales.x = limits
      if (scaleKey === 'y') this.scales.y = limits
      for (const hook of this.options.hooks?.setScale ?? []) hook(this, scaleKey)
    }

    setSize({ width }: { width: number }) {
      this.applyWidth(width)
    }
    redraw() {}
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
  setSize: (size: { width: number; height: number }) => void
  over: HTMLElement
}

let resizeCallback: ResizeObserverCallback | null = null

function signal(durationSec = 60): ECGSignal {
  const sampleRate = 1
  const startTimestamp = 1_700_000_000_000
  return {
    sampleRate,
    durationMs: durationSec * 1000,
    samples: new Float32Array(durationSec).fill(0.5),
    startTimestamp,
    timestampsMs: Float64Array.from({ length: durationSec }, (_, i) => startTimestamp + i * 1000),
    gapIndices: [],
    timeline: [],
    annotations: [],
  }
}

beforeEach(() => {
  uPlotMock.instances.length = 0
  uPlotMock.widthAtConstruction = 500
  resizeCallback = null
  // La spec garantiza un callback inicial por elemento observado, y de eso
  // depende el encuadre del visor.
  globalThis.ResizeObserver = class {
    constructor(callback: ResizeObserverCallback) {
      resizeCallback = callback
    }
    observe(target: Element) {
      resizeCallback?.(
        [
          {
            target,
            contentRect: { width: uPlotMock.widthAtConstruction },
          } as unknown as ResizeObserverEntry,
        ],
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

afterEach(cleanup)

const plot = () => uPlotMock.instances.at(-1) as Plot

describe('ECGViewer — escala clínica', () => {
  it('la ventana inicial sale de la escala y no de una constante', () => {
    // 500 px de ancho ÷ (25 mm/s × 2 px/mm) = 10 s.
    render(<ECGViewer signal={signal()} />)
    const { min, max } = plot().scales.x
    expect(max - min).toBeCloseTo(10, 6)
  })

  it('a 50 mm/s entra la mitad de los segundos', () => {
    render(<ECGViewer signal={signal()} paperSpeed={50} />)
    const { min, max } = plot().scales.x
    expect(max - min).toBeCloseTo(5, 6)
  })

  it('agrandar el contenedor muestra más señal, no la misma estirada', () => {
    render(<ECGViewer signal={signal()} />)
    const before = plot().scales.x
    expect(before.max - before.min).toBeCloseTo(10, 6)

    act(() => {
      resizeCallback?.(
        [{ contentRect: { width: 1000 } } as unknown as ResizeObserverEntry],
        {} as ResizeObserver,
      )
    })

    const after = plot().scales.x
    expect(after.max - after.min).toBeCloseTo(20, 6)
  })

  it('cambiar la ganancia no reencuadra el eje de tiempo', () => {
    // La misma instancia de señal en los dos renders: el efecto de creación
    // depende de su identidad, y acá lo que se prueba es el cambio de escala.
    const same = signal()
    const { rerender } = render(<ECGViewer signal={same} amplitude={10} />)
    const before = { ...plot().scales.x }

    rerender(<ECGViewer signal={same} amplitude={20} />)

    expect(plot().scales.x.max - plot().scales.x.min).toBeCloseTo(before.max - before.min, 6)
  })

  it('cambiar el barrido reencuadra sin recrear la instancia', () => {
    // Recrearla costaría el canvas entero y perdería el tramo que el médico
    // está mirando, que es justo lo que no puede pasar al tocar la calibración.
    const same = signal()
    const { rerender } = render(<ECGViewer signal={same} paperSpeed={25} />)
    const instanceCount = uPlotMock.instances.length

    rerender(<ECGViewer signal={same} paperSpeed={50} />)

    expect(uPlotMock.instances.length).toBe(instanceCount)
    expect(plot().scales.x.max - plot().scales.x.min).toBeCloseTo(5, 6)
  })

  it('el rango vertical lo fija la ganancia, no los datos', () => {
    // 300 px de alto ÷ (10 mm/mV × 2 px/mm) = 15 mV.
    const quiet = signal()
    render(<ECGViewer signal={quiet} amplitude={10} />)
    const quietSpan = plot().scales.y.max - plot().scales.y.min
    expect(quietSpan).toBeCloseTo(15, 6)

    cleanup()

    // La misma señal con un artefacto de saturación enorme en el medio: el
    // rango no se mueve. Es lo que `auto: true` no podía garantizar — un
    // artefacto aplastaba el trazado y ningún milímetro medía igual que el de
    // al lado.
    const noisy = signal()
    noisy.samples.fill(90, 20, 25)
    render(<ECGViewer signal={noisy} amplitude={10} />)
    expect(plot().scales.y.max - plot().scales.y.min).toBeCloseTo(quietSpan, 6)
  })

  it('duplicar la ganancia muestra la mitad de los milivoltios', () => {
    const same = signal()
    const { rerender } = render(<ECGViewer signal={same} amplitude={10} />)
    const before = plot().scales.y.max - plot().scales.y.min

    rerender(<ECGViewer signal={same} amplitude={20} />)

    expect(plot().scales.y.max - plot().scales.y.min).toBeCloseTo(before / 2, 6)
  })

  it('el rango vertical sigue la línea de base, no el cero', () => {
    // El front-end es DC-acoplado: el offset de media celda de los electrodos
    // puede correr el trazado decenas de mV sin que sea una falla
    // (`INTEGRACION.md` §3.2). Anclar en 0 dejaría la pantalla en blanco.
    const offset = signal()
    offset.samples.fill(56)
    render(<ECGViewer signal={offset} amplitude={10} />)

    const { min, max } = plot().scales.y
    expect((min + max) / 2).toBeCloseTo(56, 3)
  })

  it('encuadra en el primer layout con ancho, no en el primero que llegue', () => {
    // Un contenedor todavía sin maquetar reporta 0 y no se puede encuadrar
    // contra eso. Sin esta espera el eje quedaba con el rango automático de
    // uPlot —el estudio entero comprimido en la pantalla— con el cartel
    // diciendo "25 mm/s" arriba. Medido en el navegador: 7,2 mm/s efectivos
    // contra los 25 declarados, y 22,9 mm/mV contra 10.
    uPlotMock.widthAtConstruction = 0
    render(<ECGViewer signal={signal()} />)
    expect(plot().scales.x.min).toBeNull()

    act(() => {
      resizeCallback?.(
        [{ contentRect: { width: 500 } } as unknown as ResizeObserverEntry],
        {} as ResizeObserver,
      )
    })

    const { min, max } = plot().scales.x
    expect(max - min).toBeCloseTo(10, 6)
    // Ancla al final de la señal, igual que el encuadre normal.
    expect(max).toBeCloseTo(60, 6)
  })

  it('avisa cuando el zoom libre sacó el trazado de la escala, y cuando vuelve', () => {
    const onScaleMatchChange = vi.fn()
    const ref = createRef<ECGViewerHandle>()
    render(<ECGViewer ref={ref} signal={signal()} onScaleMatchChange={onScaleMatchChange} />)
    onScaleMatchChange.mockClear()

    act(() => ref.current?.zoomToRange(1_700_000_020_000, 1_700_000_022_000))
    expect(onScaleMatchChange).toHaveBeenLastCalledWith(false)

    act(() => ref.current?.resetScale())
    expect(onScaleMatchChange).toHaveBeenLastCalledWith(true)
    expect(plot().scales.x.max - plot().scales.x.min).toBeCloseTo(10, 6)
  })
})
