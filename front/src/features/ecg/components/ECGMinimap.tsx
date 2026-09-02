import { useEffect, useMemo, useRef } from 'react'

import { cn } from '@/lib/utils'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

import {
  ANNOTATION_SEVERITY,
  annotationChartIcon,
  annotationChartLabel,
  buildAnnotationLinks,
  compareAnnotationsForPainting,
  isAnnotationHighlighted,
} from '../annotationMeta'
import type { ECGAnnotation, ECGSignal, ECGViewportChange } from '../types'

interface ECGMinimapProps {
  signal: ECGSignal
  /** Viewport actual del viewer principal (timestamps absolutos en ms). */
  viewport: ECGViewportChange | null
  /** Disparado cuando el usuario arrastra/click la ventana visible. */
  onViewportChange: (viewport: ECGViewportChange) => void
  /** Alto del mini-mapa en píxeles. Default 64. */
  height?: number
  selectedAnnotationId?: string | null
  onAnnotationSelect?: (annotation: ECGAnnotation) => void
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
export function ECGMinimap({
  signal,
  viewport,
  onViewportChange,
  height = 64,
  selectedAnnotationId,
  onAnnotationSelect,
}: ECGMinimapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)

  const durationSec = signal.durationMs / 1000
  const endTimestamp = signal.startTimestamp + durationSec * 1000

  const tokens = useMemo(() => readTokens(), [])
  const links = useMemo(() => buildAnnotationLinks(signal.annotations), [signal.annotations])
  const annotationDrawOrder = useMemo(() => {
    const sorted = [...signal.annotations].sort(compareAnnotationsForPainting)
    const selectedIndex = sorted.findIndex((annotation) => annotation.id === selectedAnnotationId)
    if (selectedIndex < 0) return sorted
    const [selected] = sorted.splice(selectedIndex, 1)
    sorted.push(selected)
    return sorted
  }, [selectedAnnotationId, signal.annotations])

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
    <div className="flex w-full flex-col gap-1.5">
      {signal.annotations.length > 0 && (
        <TooltipProvider delayDuration={200}>
          <div className="relative h-8 rounded-sm bg-gray-50" aria-label="Avisos del estudio">
            {annotationDrawOrder.map((annotation) => {
              const startPct = annotationPercent(annotation.startMs)
              const endPct = annotationPercent(annotation.endMs)
              const Icon = annotationChartIcon(annotation, links)
              const severity = ANNOTATION_SEVERITY[annotation.severity]
              const label = annotationChartLabel(annotation, links)
              const isSelected = isAnnotationHighlighted(
                annotation,
                selectedAnnotationId ?? null,
                links,
              )
              return (
                <div key={annotation.id}>
                  <span
                    data-testid={`timeline-annotation-range-${annotation.id}`}
                    className={cn(
                      'pointer-events-none absolute inset-y-0 rounded-sm border-x',
                      isSelected && 'border-2',
                    )}
                    style={{
                      left: `${startPct}%`,
                      width: `${Math.max(endPct - startPct, 0.35)}%`,
                      minWidth: annotation.endMs <= annotation.startMs ? 32 : 12,
                      borderColor: `var(--ecg-alert-${annotation.severity})`,
                      backgroundColor: `var(--ecg-alert-${annotation.severity}-timeline-bg)`,
                      zIndex: isSelected ? 2 : 1,
                    }}
                  />
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        aria-label={`${label}, severidad ${severity.label.toLowerCase()}`}
                        aria-pressed={isSelected}
                        onClick={() => onAnnotationSelect?.(annotation)}
                        className={cn(
                          'absolute top-1 flex size-6 -translate-x-1/2 cursor-pointer items-center justify-center rounded-full border shadow-sm',
                          'focus-visible:z-20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                          isSelected ? 'z-20 scale-110' : 'z-10 hover:z-20 hover:scale-105',
                        )}
                        style={{
                          left: `${startPct}%`,
                          color: `var(--ecg-alert-${annotation.severity})`,
                          borderColor: `var(--ecg-alert-${annotation.severity})`,
                          backgroundColor: `var(--ecg-alert-${annotation.severity}-marker-bg)`,
                        }}
                      >
                        <Icon className="size-3.5" aria-hidden />
                      </button>
                    </TooltipTrigger>
                    <TooltipContent>
                      {label} · {annotation.description ?? severity.label}
                    </TooltipContent>
                  </Tooltip>
                </div>
              )
            })}
          </div>
        </TooltipProvider>
      )}

      <div
        ref={containerRef}
        className={cn(
          'relative w-full select-none overflow-hidden rounded-md border border-border bg-card',
          'cursor-pointer',
        )}
        style={{ height }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        <canvas ref={canvasRef} className="absolute inset-0 block" />
        {annotationDrawOrder.map((annotation) => {
          const startPct = annotationPercent(annotation.startMs)
          const endPct = annotationPercent(annotation.endMs)
          return (
            <span
              key={annotation.id}
              className="pointer-events-none absolute top-0 bottom-0 border-x"
              style={{
                left: `${startPct}%`,
                width: `${Math.max(endPct - startPct, 0.2)}%`,
                minWidth: annotation.endMs <= annotation.startMs ? 8 : 4,
                borderColor: `var(--ecg-alert-${annotation.severity})`,
                backgroundColor: `var(--ecg-alert-${annotation.severity}-timeline-bg)`,
              }}
            />
          )
        })}
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
    </div>
  )

  function annotationPercent(timestampMs: number): number {
    if (durationSec <= 0) return 0
    return Math.max(
      0,
      Math.min(100, ((timestampMs - signal.startTimestamp) / 1000 / durationSec) * 100),
    )
  }
}

function readTokens(): { trace: string; bg: string } {
  const style = getComputedStyle(document.documentElement)
  return {
    trace: style.getPropertyValue('--ecg-trace').trim() || '#0b2185',
    bg: style.getPropertyValue('--ecg-bg').trim() || '#ffffff',
  }
}
