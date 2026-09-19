// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { forwardRef, useState, type ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGSignal } from '../types'

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({
    children,
    onOpenChange,
  }: {
    children: ReactNode
    onOpenChange: (open: boolean) => void
  }) => (
    <div>
      {children}
      <button onClick={() => onOpenChange(false)}>Cerrar externamente</button>
    </div>
  ),
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

vi.mock('./ECGPaperControls', () => ({
  ECGPaperControls: ({
    paperSpeed,
    amplitude,
    onScale,
    onPaperSpeedChange,
    onAmplitudeChange,
  }: {
    paperSpeed: number
    amplitude: number
    onScale: boolean
    onPaperSpeedChange: (value: 25 | 50) => void
    onAmplitudeChange: (value: 5 | 10 | 20) => void
  }) => (
    <div>
      <output data-testid="controls-scale">{`${paperSpeed}/${amplitude}`}</output>
      <output data-testid="controls-mode">{onScale ? 'clinical' : 'free'}</output>
      <button onClick={() => onPaperSpeedChange(50)}>50 mm/s</button>
      <button onClick={() => onAmplitudeChange(20)}>20 mm/mV</button>
    </div>
  ),
}))

vi.mock('./ECGViewer', () => ({
  ECGViewer: forwardRef(function ECGViewer(
    {
      paperSpeed,
      amplitude,
      initialViewport,
      initialCursorMs,
      onViewportChange,
      onCursorChange,
    }: {
      paperSpeed: number
      amplitude: number
      initialViewport?: { startMs: number; endMs: number }
      initialCursorMs?: number
      onViewportChange: (viewport: { startMs: number; endMs: number }) => void
      onCursorChange: (cursorMs: number) => void
    },
    ref,
  ) {
    void ref
    return (
      <div>
        <output data-testid="viewer-scale">{`${paperSpeed}/${amplitude}`}</output>
        <output data-testid="viewer-initial-viewport">{JSON.stringify(initialViewport)}</output>
        <output data-testid="viewer-initial-cursor">{initialCursorMs}</output>
        <button onClick={() => onViewportChange({ startMs: 30, endMs: 40 })}>Mover viewport</button>
        <button onClick={() => onCursorChange(35)}>Mover cursor</button>
      </div>
    )
  }),
}))

vi.mock('./ECGFindingsPanel', () => ({ ECGFindingsPanel: () => null }))
vi.mock('./ECGMinimap', () => ({ ECGMinimap: () => null }))
vi.mock('./ECGZoomControls', () => ({
  ECGZoomControls: ({ onMinimize }: { onMinimize: () => void }) => (
    <button onClick={onMinimize}>Minimizar</button>
  ),
}))

import { ECGFullscreenDialog } from './ECGFullscreenDialog'

beforeEach(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  } as unknown as typeof ResizeObserver
})

afterEach(cleanup)

const signal: ECGSignal = {
  sampleRate: 500,
  durationMs: 10_000,
  samples: new Float32Array(5_000),
  startTimestamp: 1_700_000_000_000,
  timestampsMs: new Float64Array(5_000),
  gapIndices: [],
  timeline: [],
  annotations: [],
}

function ControlledDialog() {
  const [paperSpeed, setPaperSpeed] = useState<25 | 50>(25)
  const [amplitude, setAmplitude] = useState<5 | 10 | 20>(10)
  return (
    <>
      <output data-testid="parent-scale">{`${paperSpeed}/${amplitude}`}</output>
      <ECGFullscreenDialog
        signal={signal}
        initialViewport={null}
        open
        onOpenChange={() => undefined}
        paperSpeed={paperSpeed}
        amplitude={amplitude}
        onPaperSpeedChange={setPaperSpeed}
        onAmplitudeChange={setAmplitude}
      />
    </>
  )
}

describe('ECGFullscreenDialog — calibración compartida', () => {
  it('usa y actualiza la escala controlada por el visor principal', () => {
    render(<ControlledDialog />)

    expect(screen.getByTestId('parent-scale').textContent).toBe('25/10')
    expect(screen.getByTestId('controls-scale').textContent).toBe('25/10')
    expect(screen.getByTestId('viewer-scale').textContent).toBe('25/10')

    fireEvent.click(screen.getByRole('button', { name: '50 mm/s' }))
    fireEvent.click(screen.getByRole('button', { name: '20 mm/mV' }))

    expect(screen.getByTestId('parent-scale').textContent).toBe('50/20')
    expect(screen.getByTestId('controls-scale').textContent).toBe('50/20')
    expect(screen.getByTestId('viewer-scale').textContent).toBe('50/20')
  })

  it('restaura cursor, viewport y modo de escala, y devuelve los cambios al cerrar', () => {
    const onClose = vi.fn()
    const initialViewport = {
      startMs: 10,
      endMs: 20,
      millisecondsPerPixel: 2,
      isClinicalScale: false,
    }
    render(
      <ECGFullscreenDialog
        signal={signal}
        initialViewport={initialViewport}
        initialCursorMs={15}
        open
        onOpenChange={vi.fn()}
        onClose={onClose}
        paperSpeed={25}
        amplitude={20}
        onPaperSpeedChange={vi.fn()}
        onAmplitudeChange={vi.fn()}
      />,
    )

    expect(screen.getByTestId('viewer-initial-viewport').textContent).toBe(
      JSON.stringify(initialViewport),
    )
    expect(screen.getByTestId('viewer-initial-cursor').textContent).toBe('15')
    expect(screen.getByTestId('controls-mode').textContent).toBe('free')

    fireEvent.click(screen.getByRole('button', { name: 'Mover viewport' }))
    fireEvent.click(screen.getByRole('button', { name: 'Mover cursor' }))
    fireEvent.click(screen.getByRole('button', { name: 'Minimizar' }))

    expect(onClose).toHaveBeenCalledWith({ startMs: 30, endMs: 40 }, 35)

    onClose.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar externamente' }))
    expect(onClose).toHaveBeenCalledWith({ startMs: 30, endMs: 40 }, 35)
  })
})
