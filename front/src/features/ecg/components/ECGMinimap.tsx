import { useEffect, useMemo, useRef } from 'react'

import { cn } from '@/lib/utils'

import type { ECGSignal, ECGViewportChange } from '../types'

interface ECGMinimapProps {
  signal: ECGSignal
  /** Viewport actual del viewer principal (timestamps absolutos en ms). */
  viewport: ECGViewportChange | null
  /** Disparado cuando el usuario arrastra/click la ventana visible. */
  onViewportChange: (viewport: ECGViewportChange) => void
  /** Alto del mini-mapa en píxeles. Default 64. */
  height?: number
}

/**
 * Mini-mapa para navegar > 1 h de ECG sin perder contexto.
 *
 * Canvas downsampleado vía min/max bucket por columna de pixel — preserva los
 * picos QRS (el sampling uniforme los pierde) y es más rápido que crear una
 * segunda instancia uPlot.
 *
 * El overlay con la ventana visible es un `<div>` absoluto sobre el canvas con
 * `pointer*` handlers para drag.
 */
export function ECGMinimap({ signal, viewport, onViewportChange, height = 64 }: ECGMinimapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  const durationSec = signal.samples.length / signal.sampleRate
  const endTimestamp = signal.startTimestamp + durationSec * 1000

  const tokens = useMemo(() => readTokens(), [])

  // Dibuja la señal downsampleada (min/max por columna). Re-dibuja solo cuando
  // cambia la señal o el ancho del canvas (no cuando cambia el viewport).
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const draw = () => {
      const cssWidth = container.clientWidth
      const dpr = window.devicePixelRatio || 1
      canvas.width = Math.floor(cssWidth * dpr)
      canvas.height = Math.floor(height * dpr)
      canvas.style.width = `${cssWidth}px`
      canvas.style.height = `${height}px`
      const ctx = canvas.getContext('2d')
      if (!ctx) return
      ctx.scale(dpr, dpr)
      ctx.fillStyle = tokens.bg
      ctx.fillRect(0, 0, cssWidth, height)

      const samples = signal.samples
      if (samples.length === 0) return
      const samplesPerColumn = Math.max(1, Math.floor(samples.length / cssWidth))
      let yMin = Infinity
      let yMax = -Infinity
      for (let i = 0; i < samples.length; i++) {
        const v = samples[i]
        if (v < yMin) yMin = v
        if (v > yMax) yMax = v
      }
      if (yMin === yMax) {
        yMin -= 1
        yMax += 1
      }
      const yScale = (height - 4) / (yMax - yMin)

      ctx.strokeStyle = tokens.trace
      ctx.lineWidth = 1
      ctx.beginPath()
      for (let col = 0; col < cssWidth; col++) {
        const start = col * samplesPerColumn
        const end = Math.min(samples.length, start + samplesPerColumn)
        let mn = Infinity
        let mx = -Infinity
        for (let i = start; i < end; i++) {
          const v = samples[i]
          if (v < mn) mn = v
          if (v > mx) mx = v
        }
        if (mn === Infinity) continue
        const yTop = 2 + (yMax - mx) * yScale
        const yBot = 2 + (yMax - mn) * yScale
        ctx.moveTo(col + 0.5, yTop)
        ctx.lineTo(col + 0.5, Math.max(yBot, yTop + 0.5))
      }
      ctx.stroke()
    }

    draw()
    const ro = new ResizeObserver(() => draw())
    ro.observe(container)
    return () => ro.disconnect()
  }, [signal, height, tokens.bg, tokens.trace])

  // Convertir timestamps absolutos a porcentaje del eje X.
  const viewportLeftPct =
    viewport != null
      ? Math.max(
          0,
          Math.min(100, ((viewport.startMs - signal.startTimestamp) / 1000 / durationSec) * 100),
        )
      : 0
  const viewportWidthPct =
    viewport != null
      ? Math.max(
          0.5,
          Math.min(100, ((viewport.endMs - viewport.startMs) / 1000 / durationSec) * 100),
        )
      : 100

  // Drag handler para mover la ventana visible.
  const dragRef = useRef<{ pointerId: number; startX: number; startLeftPct: number } | null>(null)

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const container = containerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    const clickXPct = ((e.clientX - rect.left) / rect.width) * 100
    // Si el click cae fuera de la ventana, centrar la ventana en el click.
    const clickedInsideWindow =
      clickXPct >= viewportLeftPct && clickXPct <= viewportLeftPct + viewportWidthPct
    const startLeftPct = clickedInsideWindow
      ? viewportLeftPct
      : Math.max(0, Math.min(100 - viewportWidthPct, clickXPct - viewportWidthPct / 2))
    if (!clickedInsideWindow) {
      emitViewport(startLeftPct)
    }
    dragRef.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      startLeftPct,
    }
    container.setPointerCapture(e.pointerId)
  }

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    const container = containerRef.current
    if (!drag || !container || e.pointerId !== drag.pointerId) return
    const rect = container.getBoundingClientRect()
    const dxPct = ((e.clientX - drag.startX) / rect.width) * 100
    const nextLeftPct = Math.max(0, Math.min(100 - viewportWidthPct, drag.startLeftPct + dxPct))
    emitViewport(nextLeftPct)
  }

  const handlePointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    const drag = dragRef.current
    const container = containerRef.current
    if (!drag || !container || e.pointerId !== drag.pointerId) return
    container.releasePointerCapture(e.pointerId)
    dragRef.current = null
  }

  function emitViewport(leftPct: number) {
    const startMs = signal.startTimestamp + (leftPct / 100) * durationSec * 1000
    const widthMs = (viewportWidthPct / 100) * durationSec * 1000
    const clampedStart = Math.max(signal.startTimestamp, Math.min(endTimestamp - widthMs, startMs))
    onViewportChange({ startMs: clampedStart, endMs: clampedStart + widthMs })
  }

  return (
    <div
      ref={containerRef}
      className={cn(
        'relative w-full select-none rounded-md border border-border bg-card overflow-hidden',
        'cursor-pointer',
      )}
      style={{ height }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <canvas ref={canvasRef} className="absolute inset-0 block" />
      <div
        className="pointer-events-none absolute top-0 bottom-0 border-2"
        style={{
          left: `${viewportLeftPct}%`,
          width: `${viewportWidthPct}%`,
          borderColor: 'var(--ecg-selector)',
          backgroundColor: 'var(--ecg-selector-bg)',
        }}
      />
    </div>
  )
}

function readTokens(): { trace: string; bg: string } {
  const style = getComputedStyle(document.documentElement)
  return {
    trace: style.getPropertyValue('--ecg-trace').trim() || '#0b2185',
    bg: style.getPropertyValue('--ecg-bg').trim() || '#ffffff',
  }
}
