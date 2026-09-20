// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGAnnotationSeverity, ECGSignal } from '../types'
import { ECGMinimap } from './ECGMinimap'

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

beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(HTMLCanvasElement.prototype, 'getContext', {
    configurable: true,
    value: () => canvasContext,
  })
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

describe('ECGMinimap annotations', () => {
  it('deja vacías las columnas del eje temporal en las que no hubo señal', () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(10)
    const withGap = signal()
    withGap.samples = Float32Array.from([0, 1, 0.5, 0])
    withGap.timestampsMs = Float64Array.from([
      withGap.startTimestamp,
      withGap.startTimestamp + 1_000,
      withGap.startTimestamp + 9_000,
      withGap.startTimestamp + 10_000,
    ])
    withGap.gapIndices = [2]

    render(<ECGMinimap signal={withGap} viewport={null} onViewportChange={() => undefined} />)

    expect(canvasContext.moveTo.mock.calls.map(([x]) => x)).toEqual([0.5, 1.5, 9.5])
  })

  it('renderiza un marcador accesible y notifica su selección', () => {
    const onSelect = vi.fn()
    render(
      <ECGMinimap
        signal={signal()}
        viewport={null}
        onViewportChange={() => undefined}
        selectedAnnotationId="event-1"
        onAnnotationSelect={onSelect}
      />,
    )

    const marker = screen.getByRole('button', {
      name: 'Electrodo desconectado, severidad media',
    })
    expect(marker.getAttribute('aria-pressed')).toBe('true')
    expect(marker.getAttribute('style')).toContain('--ecg-alert-medium')
    expect(marker.className).toContain('cursor-pointer')
    expect(screen.getByTestId('timeline-annotation-range-event-1').className).toContain('inset-y-0')
    expect(screen.getByTestId('timeline-annotation-range-event-1').getAttribute('style')).toContain(
      '--ecg-alert-medium-timeline-bg',
    )

    fireEvent.click(marker)
    expect(onSelect).toHaveBeenCalledWith(signal().annotations[0])
  })

  it('asigna un fondo visible a todas las severidades y ancho mínimo a eventos puntuales', () => {
    const severities: ECGAnnotationSeverity[] = ['low', 'medium', 'high', 'critical']
    const withAllSeverities = signal()
    withAllSeverities.annotations = severities.map((severity, index) => ({
      ...withAllSeverities.annotations[0],
      id: `event-${severity}`,
      severity,
      startMs: withAllSeverities.startTimestamp + index * 2_000,
      endMs: withAllSeverities.startTimestamp + index * 2_000,
    }))

    render(
      <ECGMinimap signal={withAllSeverities} viewport={null} onViewportChange={() => undefined} />,
    )

    for (const severity of severities) {
      const range = screen.getByTestId(`timeline-annotation-range-event-${severity}`)
      expect(range.getAttribute('style')).toContain(`--ecg-alert-${severity}-timeline-bg`)
      expect(range.getAttribute('style')).toContain('min-width: 32px')
      const marker = screen.getByRole('button', {
        name: `Electrodo desconectado, severidad ${severityLabel(severity)}`,
      })
      expect(marker.getAttribute('style')).toContain(`--ecg-alert-${severity}-marker-bg`)
    }
  })

  it('conserva el ancho lógico de un viewport más angosto que el selector visual', () => {
    const onViewportChange = vi.fn()
    const longSignal = signal()
    longSignal.durationMs = 1_000_000
    render(
      <ECGMinimap
        signal={longSignal}
        viewport={{
          startMs: longSignal.startTimestamp,
          endMs: longSignal.startTimestamp + 1_000,
        }}
        onViewportChange={onViewportChange}
      />,
    )

    const minimap = screen.getByLabelText('Navegación general del ECG')
    minimap.getBoundingClientRect = () =>
      ({ left: 0, right: 1_000, top: 0, bottom: 64, width: 1_000, height: 64 }) as DOMRect
    Object.defineProperty(minimap, 'setPointerCapture', { value: vi.fn() })

    fireEvent.pointerDown(minimap, { clientX: 500, pointerId: 1 })

    expect(onViewportChange).toHaveBeenCalledOnce()
    const next = onViewportChange.mock.calls[0][0]
    expect(next.endMs - next.startMs).toBe(1_000)
  })
})

function severityLabel(severity: ECGAnnotationSeverity): string {
  return { low: 'baja', medium: 'media', high: 'alta', critical: 'crítica' }[severity]
}

function signal(): ECGSignal {
  return {
    sampleRate: 250,
    durationMs: 10_000,
    samples: new Float32Array([0, 1, 0]),
    startTimestamp: 1_700_000_000_000,
    // Grabación sin cortes: un eje uniforme y ningún hueco que dibujar.
    timestampsMs: Float64Array.from([0, 1, 2], (i) => 1_700_000_000_000 + i * 5_000),
    gapIndices: [],
    timeline: [],
    annotations: [
      {
        id: 'event-1',
        kind: 'lead_off',
        category: 'signal_quality',
        severity: 'medium',
        startMs: 1_700_000_002_000,
        endMs: 1_700_000_004_000,
        confidenceScore: null,
        linkedAnnotationId: null,
        description: null,
      },
    ],
  }
}
