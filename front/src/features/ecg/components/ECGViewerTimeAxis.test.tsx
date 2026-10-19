// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { formatWallClock, formatWallClockShort } from '../utils/formatEcgTimestamp'
import type { ECGSignal, ECGTimelineSegment } from '../types'

interface CapturedOptions {
  axes: { values?: (u: unknown, splits: number[]) => string[] }[]
  series: { value?: (u: unknown, v: number | null) => string }[]
}

const captured = vi.hoisted(() => ({ options: [] as unknown[] }))

vi.mock('uplot', () => {
  class MockUPlot {
    readonly over = document.createElement('div')
    readonly scales = { x: { min: 0 as number | null, max: 60 as number | null }, y: {} }
    constructor(options: unknown, _data: unknown, container: HTMLElement) {
      captured.options.push(options)
      Object.defineProperties(this.over, {
        offsetLeft: { configurable: true, value: 60 },
        offsetTop: { configurable: true, value: 40 },
        clientWidth: { configurable: true, value: 500 },
        clientHeight: { configurable: true, value: 300 },
      })
      container.appendChild(this.over)
    }
    setScale() {}
    setSize() {}
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

const START = 1_700_000_000_000

beforeEach(() => {
  captured.options.length = 0
  globalThis.ResizeObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  } as unknown as typeof ResizeObserver
})

afterEach(cleanup)

function segment(ordinal: number, fromSec: number, toSec: number, verified: boolean) {
  return {
    ordinal,
    startSampleIndex: fromSec,
    sampleCount: toSec - fromSec,
    startEpochMs: START + fromSec * 1000,
    endEpochMs: START + toSec * 1000,
    bootId: ordinal,
    anchorSource: 'ntp',
    anchorUncertaintyMs: verified ? 10 : 7_000,
    anchorMatchesBoot: verified,
  } satisfies ECGTimelineSegment
}

function signal(timeline: ECGTimelineSegment[], startTimeVerified: boolean | undefined): ECGSignal {
  return {
    sampleRate: 1,
    durationMs: 59_000,
    samples: new Float32Array(60),
    startTimestamp: START,
    timestampsMs: Float64Array.from({ length: 60 }, (_, i) => START + i * 1000),
    gapIndices: [],
    timeline,
    annotations: [],
    metadata: {
      formatVersion: 3,
      encoding: 'float32-le',
      sampleCount: 60,
      isSimulated: false,
      overviewSamplesPerBucket: null,
      startTimeVerified,
    },
  }
}

function axisAndCursor(sig: ECGSignal) {
  render(<ECGViewer signal={sig} />)
  const options = captured.options.at(-1) as CapturedOptions
  return {
    axis: (...seconds: number[]) => options.axes[0].values!(null, seconds),
    cursor: (second: number) => options.series[0].value!(null, second),
  }
}

describe('ECGViewer eje de tiempo por tramo', () => {
  it('con tramos mixtos muestra la hora en todos y marca con ≈ solo los sin verificar', () => {
    const { axis, cursor } = axisAndCursor(
      signal([segment(0, 0, 20, true), segment(1, 20, 40, false), segment(2, 40, 60, true)], false),
    )

    expect(axis(10, 30, 50)).toEqual([
      formatWallClockShort(START + 10_000),
      `≈${formatWallClockShort(START + 30_000)}`,
      formatWallClockShort(START + 50_000),
    ])
    expect(cursor(10)).toBe(formatWallClock(START + 10_000))
    expect(cursor(30)).toBe(`≈${formatWallClock(START + 30_000)}`)
  })

  it('con todos los tramos verificados no marca nada', () => {
    const { axis } = axisAndCursor(signal([segment(0, 0, 60, true)], true))
    expect(axis(5)).toEqual([formatWallClockShort(START + 5_000)])
  })

  it('un estudio legacy sin tramos y sin verificar cae a tiempo transcurrido', () => {
    const { axis, cursor } = axisAndCursor(signal([], false))
    expect(axis(102)).toEqual(['+00:01:42'])
    expect(cursor(102)).toBe('+00:01:42')
  })

  it('un estudio legacy sin tramos pero verificado muestra la hora sin marca', () => {
    const { axis } = axisAndCursor(signal([], true))
    expect(axis(5)).toEqual([formatWallClockShort(START + 5_000)])
  })
})
