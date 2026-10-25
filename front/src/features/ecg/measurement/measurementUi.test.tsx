// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, renderHook, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ECGMeasurementOverlay, type ECGMeasurementOverlayProps } from './ECGMeasurementOverlay'
import { ECGMeasurementToolbar } from './ECGMeasurementToolbar'
import type { Measurement } from './measure'
import { useMeasurements } from './useMeasurements'

afterEach(cleanup)

// 1 px = 1 ms, y invertido: 1 mV = 100 px.
const trace = {
  xs: Array.from({ length: 301 }, (_, i) => 1_000 + i),
  ys: Array.from({ length: 301 }, (_, i) => Math.sin(i / 20)),
  gapIndices: [] as number[],
}

function overlayProps(
  overrides: Partial<ECGMeasurementOverlayProps> = {},
): ECGMeasurementOverlayProps {
  return {
    tool: 'time',
    enabled: true,
    plotArea: { left: 10, top: 5, width: 400, height: 200 },
    xToPx: (ms) => ms - 1_000,
    pxToX: (px) => px + 1_000,
    yToPx: (mv) => 100 - mv * 100,
    visibleRange: { startMs: 1_000, endMs: 1_300 },
    trace,
    measurements: [],
    onChange: vi.fn(),
    ...overrides,
  }
}

describe('ECGMeasurementToolbar', () => {
  it('deshabilita las herramientas y muestra el motivo', () => {
    const onToolChange = vi.fn()
    render(
      <ECGMeasurementToolbar
        tool={null}
        onToolChange={onToolChange}
        enabled={false}
        disabledReason="Acercá el zoom"
        measurementCount={0}
        onClear={vi.fn()}
      />,
    )
    const time = screen.getByRole('button', { name: 'Calibre de tiempo' })
    expect(time.hasAttribute('disabled')).toBe(true)
    expect(time.getAttribute('title')).toContain('Acercá el zoom')
    fireEvent.click(time)
    expect(onToolChange).not.toHaveBeenCalled()
  })

  it('activa, desactiva y limpia; avisa del filtro de visualización', () => {
    const onToolChange = vi.fn()
    const onClear = vi.fn()
    const { rerender } = render(
      <ECGMeasurementToolbar
        tool={null}
        onToolChange={onToolChange}
        enabled
        measurementCount={2}
        onClear={onClear}
        viewKind="filtered_visualization"
      />,
    )
    expect(screen.getByRole('button', { name: 'Compás R-R' }).getAttribute('title')).toContain(
      '0,05–40 Hz',
    )
    fireEvent.click(screen.getByRole('button', { name: 'Compás R-R' }))
    expect(onToolChange).toHaveBeenCalledWith('rr')
    fireEvent.click(screen.getByRole('button', { name: /Borrar mediciones/ }))
    expect(onClear).toHaveBeenCalled()

    rerender(
      <ECGMeasurementToolbar
        tool="rr"
        onToolChange={onToolChange}
        enabled
        measurementCount={0}
        onClear={onClear}
        viewKind="raw"
      />,
    )
    expect(screen.getByRole('button', { name: 'Compás R-R' }).getAttribute('title')).not.toContain(
      '0,05–40 Hz',
    )
    const rr = screen.getByRole('button', { name: 'Compás R-R' })
    expect(rr.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(rr)
    expect(onToolChange).toHaveBeenLastCalledWith(null)
  })
  it('muestra todas las herramientas con la mano activa por defecto', () => {
    const onToolChange = vi.fn()
    render(
      <ECGMeasurementToolbar
        tool={null}
        onToolChange={onToolChange}
        enabled
        measurementCount={0}
        onClear={vi.fn()}
      />,
    )
    expect(
      screen.getByRole('button', { name: 'Desplazar gráfico' }).getAttribute('aria-pressed'),
    ).toBe('true')
    for (const name of ['Calibre de tiempo', 'Calibre de amplitud', 'Área', 'Compás R-R']) {
      expect(screen.getByRole('button', { name }).getAttribute('aria-pressed')).toBe('false')
    }
    expect(screen.queryByRole('button', { name: 'Herramientas de medición' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Calibre de tiempo' }))
    expect(onToolChange).toHaveBeenCalledWith('time')
  })

  it('vuelve a desplazar con la mano sin borrar las mediciones', () => {
    const onToolChange = vi.fn()
    const onClear = vi.fn()
    render(
      <ECGMeasurementToolbar
        tool="time"
        onToolChange={onToolChange}
        enabled={false}
        measurementCount={2}
        onClear={onClear}
      />,
    )
    const hand = screen.getByRole('button', { name: 'Desplazar gráfico' })
    expect(hand.getAttribute('aria-pressed')).toBe('false')
    expect(hand.hasAttribute('disabled')).toBe(false)
    fireEvent.click(hand)
    expect(onToolChange).toHaveBeenLastCalledWith(null)
    expect(onClear).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Borrar mediciones (2)' })).toBeTruthy()
  })
})

describe('ECGMeasurementOverlay', () => {
  it('crea una medición a partir de eventos de puntero', () => {
    const onChange = vi.fn()
    render(<ECGMeasurementOverlay {...overlayProps({ onChange })} />)
    const capture = screen.getByTestId('measurement-capture')
    fireEvent.pointerDown(capture, { clientX: 50, clientY: 20, button: 0 })
    fireEvent.pointerMove(capture, { clientX: 120, clientY: 20 })
    fireEvent.pointerUp(capture, { clientX: 150, clientY: 20 })
    expect(onChange).toHaveBeenCalledTimes(1)
    const [next] = onChange.mock.calls[0] as [Measurement[]]
    expect(next).toHaveLength(1)
    expect(next[0]).toMatchObject({ tool: 'time', aMs: 1_050, bMs: 1_150 })
  })

  it('ignora un clic sin arrastre y cancela con Escape', () => {
    const onChange = vi.fn()
    render(<ECGMeasurementOverlay {...overlayProps({ onChange })} />)
    const capture = screen.getByTestId('measurement-capture')
    fireEvent.pointerDown(capture, { clientX: 50, button: 0 })
    fireEvent.pointerUp(capture, { clientX: 51 })
    expect(onChange).not.toHaveBeenCalled()

    fireEvent.pointerDown(capture, { clientX: 50, button: 0 })
    fireEvent.pointerMove(capture, { clientX: 150 })
    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' })
    })
    fireEvent.pointerUp(capture, { clientX: 150 })
    expect(onChange).not.toHaveBeenCalled()
  })

  it('no captura el puntero sin herramienta ni fuera del detalle', () => {
    const { rerender } = render(<ECGMeasurementOverlay {...overlayProps({ tool: null })} />)
    expect(screen.getByTestId('measurement-capture').className).toContain('pointer-events-none')
    rerender(<ECGMeasurementOverlay {...overlayProps({ enabled: false })} />)
    expect(screen.getByTestId('measurement-capture').className).toContain('pointer-events-none')
    rerender(<ECGMeasurementOverlay {...overlayProps()} />)
    expect(screen.getByTestId('measurement-capture').className).toContain('pointer-events-auto')
  })

  it('quita una medición con su botón', () => {
    const onChange = vi.fn()
    const measurements: Measurement[] = [
      { id: 'a', tool: 'time', aMs: 1_050, bMs: 1_150 },
      { id: 'b', tool: 'rr', aMs: 1_020, bMs: 1_100 },
    ]
    render(<ECGMeasurementOverlay {...overlayProps({ tool: null, measurements, onChange })} />)
    expect(screen.getAllByTestId('measurement-label')).toHaveLength(2)
    fireEvent.click(screen.getAllByRole('button', { name: /Quitar medición/ })[0])
    expect(onChange).toHaveBeenCalledWith([measurements[1]])
  })
  it('descarta el arrastre pendiente al volver al modo de desplazamiento y conserva las mediciones terminadas', () => {
    const onChange = vi.fn()
    const measurements: Measurement[] = [{ id: 'existing', tool: 'time', aMs: 1_020, bMs: 1_080 }]
    const { rerender } = render(
      <ECGMeasurementOverlay {...overlayProps({ measurements, onChange })} />,
    )
    const capture = screen.getByTestId('measurement-capture')
    fireEvent.pointerDown(capture, { clientX: 50, clientY: 20, button: 0 })
    fireEvent.pointerMove(capture, { clientX: 150, clientY: 20 })
    rerender(<ECGMeasurementOverlay {...overlayProps({ tool: null, measurements, onChange })} />)
    expect(screen.getByTestId('measurement-capture').className).toContain('pointer-events-none')
    fireEvent.pointerUp(screen.getByTestId('measurement-capture'), { clientX: 150, clientY: 20 })
    expect(screen.getAllByTestId('measurement-label')).toHaveLength(1)
    expect(onChange).not.toHaveBeenCalled()
  })
})

describe('useMeasurements', () => {
  it('se reinicia al cambiar el resetKey y Esc apaga la herramienta', () => {
    const { result, rerender } = renderHook(({ k }) => useMeasurements(k), {
      initialProps: { k: 'study-1' },
    })
    act(() => {
      result.current.setTool('area')
      result.current.setMeasurements([{ id: 'x', tool: 'area', aMs: 1, bMs: 2 }])
    })
    expect(result.current.measurements).toHaveLength(1)

    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' })
    })
    expect(result.current.tool).toBeNull()
    expect(result.current.measurements).toHaveLength(1)

    act(() => result.current.setTool('time'))
    rerender({ k: 'study-2' })
    expect(result.current.tool).toBeNull()
    expect(result.current.measurements).toHaveLength(0)

    act(() => result.current.setMeasurements([{ id: 'y', tool: 'time', aMs: 1, bMs: 2 }]))
    act(() => result.current.clear())
    expect(result.current.measurements).toHaveLength(0)
  })
})
