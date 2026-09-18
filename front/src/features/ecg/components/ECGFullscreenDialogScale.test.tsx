// @vitest-environment jsdom

import { fireEvent, render, screen } from '@testing-library/react'
import { forwardRef, useState, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ECGSignal } from '../types'

vi.mock('@/components/ui/dialog', () => ({
  Dialog: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogContent: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  DialogDescription: ({ children }: { children: ReactNode }) => <p>{children}</p>,
  DialogHeader: ({ children }: { children: ReactNode }) => <header>{children}</header>,
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

vi.mock('./ECGPaperControls', () => ({
  ECGPaperControls: ({
    paperSpeed,
    amplitude,
    onPaperSpeedChange,
    onAmplitudeChange,
  }: {
    paperSpeed: number
    amplitude: number
    onPaperSpeedChange: (value: 25 | 50) => void
    onAmplitudeChange: (value: 5 | 10 | 20) => void
  }) => (
    <div>
      <output data-testid="controls-scale">{`${paperSpeed}/${amplitude}`}</output>
      <button onClick={() => onPaperSpeedChange(50)}>50 mm/s</button>
      <button onClick={() => onAmplitudeChange(20)}>20 mm/mV</button>
    </div>
  ),
}))

vi.mock('./ECGViewer', () => ({
  ECGViewer: forwardRef(function ECGViewer(
    { paperSpeed, amplitude }: { paperSpeed: number; amplitude: number },
    ref,
  ) {
    void ref
    return <output data-testid="viewer-scale">{`${paperSpeed}/${amplitude}`}</output>
  }),
}))

vi.mock('./ECGFindingsPanel', () => ({ ECGFindingsPanel: () => null }))
vi.mock('./ECGMinimap', () => ({ ECGMinimap: () => null }))
vi.mock('./ECGZoomControls', () => ({ ECGZoomControls: () => null }))

import { ECGFullscreenDialog } from './ECGFullscreenDialog'

beforeEach(() => {
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  } as unknown as typeof ResizeObserver
})

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
})
