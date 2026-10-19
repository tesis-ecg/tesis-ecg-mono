import { X } from 'lucide-react'
import { useEffect, useState, type PointerEvent as ReactPointerEvent } from 'react'

import { cn } from '@/lib/utils'

import {
  formatMeasurement,
  nearestSampleIndex,
  rrCompassMarks,
  spansGap,
  type MeasureTrace,
  type Measurement,
  type MeasurementTool,
} from './measure'

export interface ECGMeasurementOverlayProps {
  /** `null` = herramientas apagadas: el overlay no captura el puntero. */
  tool: MeasurementTool | null
  /** `false` cuando el visor no dibuja muestras reales. */
  enabled: boolean
  plotArea: { left: number; top: number; width: number; height: number } | null
  /** Relativo al borde izquierdo del plot. */
  xToPx: (epochMs: number) => number
  pxToX: (px: number) => number
  /** Relativo al borde superior del plot. */
  yToPx: (mv: number) => number
  visibleRange: { startMs: number; endMs: number }
  trace: MeasureTrace | null
  measurements: Measurement[]
  onChange: (next: Measurement[]) => void
}

/** Movimiento mínimo (px) para que un arrastre cuente como medición y no como un clic. */
const MIN_DRAG_PX = 3
/** Cuántos rótulos se escalonan en vertical antes de repetir altura. */
const LABEL_ROWS = 4
const LABEL_HALF_WIDTH = 70

let nextId = 0

interface Draft {
  aMs: number
  bMs: number
  aPx: number
  bPx: number
}

function sampleMv(trace: MeasureTrace | null, epochMs: number): number | undefined {
  if (!trace) return undefined
  const i = nearestSampleIndex(trace, epochMs)
  if (i < 0) return undefined
  const y = trace.ys[i]
  return y != null && Number.isFinite(y) ? y : undefined
}

function toMeasurement(tool: MeasurementTool, draft: Draft, trace: MeasureTrace | null) {
  const m: Measurement = {
    id: `m${++nextId}`,
    tool,
    aMs: draft.aMs,
    bMs: draft.bMs,
  }
  if (tool === 'amplitude') {
    m.aMv = sampleMv(trace, draft.aMs)
    m.bMv = sampleMv(trace, draft.bMs)
  }
  return m
}

/**
 * Capa de medición sobre el área del plot. SVG para las figuras y HTML para los
 * rótulos (texto legible y botón de quitar accesible).
 *
 * Las mediciones guardan instantes de la señal (epoch ms), no píxeles, así que
 * acompañan el paneo y el zoom: todo se reproyecta con `xToPx` en cada render.
 * Los valores salen de las muestras más cercanas (ver `measure.ts`), nunca de la
 * posición del cursor.
 */
export function ECGMeasurementOverlay({
  tool,
  enabled,
  plotArea,
  xToPx,
  pxToX,
  yToPx,
  visibleRange,
  trace,
  measurements,
  onChange,
}: ECGMeasurementOverlayProps) {
  const [draft, setDraft] = useState<Draft | null>(null)
  const active = tool != null && enabled

  // Una medición en curso no sobrevive a apagar la herramienta ni al zoom fuera
  // del detalle. Derivado en render para no pasar por un efecto.
  const liveDraft = active ? draft : null
  if (!active && draft) setDraft(null)

  // Esc cancela la medición en curso. En fase de captura y con `preventDefault`
  // para que el hook no apague además la herramienta.
  useEffect(() => {
    if (!liveDraft) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setDraft(null)
    }
    window.addEventListener('keydown', onKeyDown, true)
    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [liveDraft])

  const localX = (event: ReactPointerEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect()
    return event.clientX - rect.left
  }

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!active || event.button !== 0) return
    const px = localX(event)
    event.currentTarget.setPointerCapture?.(event.pointerId)
    const ms = pxToX(px)
    setDraft({ aMs: ms, bMs: ms, aPx: px, bPx: px })
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draft) return
    const px = localX(event)
    setDraft({ ...draft, bMs: pxToX(px), bPx: px })
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!draft || !tool) return
    event.currentTarget.releasePointerCapture?.(event.pointerId)
    const px = localX(event)
    const final: Draft = { ...draft, bMs: pxToX(px), bPx: px }
    setDraft(null)
    if (Math.abs(final.bPx - final.aPx) < MIN_DRAG_PX) return
    onChange([...measurements, toMeasurement(tool, final, trace)])
  }

  const shapes = plotArea && enabled ? measurements : []

  if (!plotArea) return null

  const { width, height } = plotArea
  const draftMeasurement: Measurement | null =
    liveDraft && tool ? { id: 'draft', tool, aMs: liveDraft.aMs, bMs: liveDraft.bMs } : null

  const renderFigure = (m: Measurement, isDraft: boolean) => {
    const ax = xToPx(m.aMs)
    const bx = xToPx(m.bMs)
    const gap =
      trace != null && m.tool !== 'time' && m.tool !== 'rr' && spansGap(trace, m.aMs, m.bMs)
    const color = gap ? 'var(--ecg-alert-critical)' : 'var(--ecg-measure)'
    const fill = gap ? 'var(--ecg-alert-critical-bg)' : 'var(--ecg-measure-bg)'
    const common = {
      stroke: color,
      strokeWidth: 1.5,
      strokeDasharray: isDraft ? '4 3' : undefined,
    }
    const vline = (x: number, key: string, opacity = 1) => (
      <line key={key} x1={x} x2={x} y1={0} y2={height} opacity={opacity} {...common} />
    )

    if (m.tool === 'time') {
      return (
        <g key={m.id}>
          <rect x={Math.min(ax, bx)} y={0} width={Math.abs(bx - ax)} height={height} fill={fill} />
          {vline(ax, 'a')}
          {vline(bx, 'b')}
        </g>
      )
    }

    if (m.tool === 'amplitude') {
      const aMv = m.aMv ?? sampleMv(trace, m.aMs)
      const bMv = m.bMv ?? sampleMv(trace, m.bMs)
      return (
        <g key={m.id}>
          {vline(ax, 'a', 0.4)}
          {vline(bx, 'b', 0.4)}
          {aMv != null ? (
            <line
              x1={Math.min(ax, bx)}
              x2={Math.max(ax, bx)}
              y1={yToPx(aMv)}
              y2={yToPx(aMv)}
              {...common}
            />
          ) : null}
          {bMv != null ? (
            <line
              x1={Math.min(ax, bx)}
              x2={Math.max(ax, bx)}
              y1={yToPx(bMv)}
              y2={yToPx(bMv)}
              {...common}
            />
          ) : null}
        </g>
      )
    }

    if (m.tool === 'area' && trace) {
      const lo = Math.min(m.aMs, m.bMs)
      const hi = Math.max(m.aMs, m.bMs)
      const ia = nearestSampleIndex(trace, lo)
      const ib = nearestSampleIndex(trace, hi)
      const points: string[] = []
      for (let i = Math.max(ia, 0); i <= ib; i++) {
        const y = trace.ys[i]
        if (y == null || !Number.isFinite(y)) continue
        points.push(`${xToPx(trace.xs[i])},${yToPx(y)}`)
      }
      return (
        <g key={m.id}>
          {points.length >= 2 ? (
            // El polígono se cierra solo del último al primer punto: esa es la cuerda.
            <polygon points={points.join(' ')} fill={fill} {...common} />
          ) : null}
          {vline(ax, 'a', 0.4)}
          {vline(bx, 'b', 0.4)}
        </g>
      )
    }

    if (m.tool === 'rr') {
      const { marksMs } = rrCompassMarks(m.aMs, m.bMs, visibleRange.startMs, visibleRange.endMs)
      return (
        <g key={m.id}>
          {marksMs.map((ms) => vline(xToPx(ms), `k${ms}`, 0.55))}
          {vline(ax, 'a')}
          {vline(bx, 'b')}
        </g>
      )
    }

    return null
  }

  const renderLabel = (m: Measurement, index: number) => {
    const center = (xToPx(m.aMs) + xToPx(m.bMs)) / 2
    const left = Math.min(Math.max(center, LABEL_HALF_WIDTH), Math.max(width - LABEL_HALF_WIDTH, 0))
    return (
      <div
        key={m.id}
        data-testid="measurement-label"
        className="pointer-events-none absolute flex -translate-x-1/2 items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs tabular-nums shadow-sm"
        style={{
          left,
          // Abajo: arriba están los rótulos de los avisos y la barra de herramientas.
          bottom: 4 + (index % LABEL_ROWS) * 22,
          background: 'var(--ecg-bg)',
          color: 'var(--ecg-measure)',
          borderColor: 'var(--ecg-measure)',
        }}
      >
        <span>{formatMeasurement(m, trace)}</span>
        <button
          type="button"
          className="pointer-events-auto inline-flex size-4 cursor-pointer items-center justify-center rounded hover:bg-black/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Quitar medición: ${formatMeasurement(m, trace)}`}
          onClick={() => onChange(measurements.filter((x) => x.id !== m.id))}
        >
          <X className="size-3" />
        </button>
      </div>
    )
  }

  return (
    <div
      className="pointer-events-none absolute overflow-hidden"
      style={{ left: plotArea.left, top: plotArea.top, width, height }}
    >
      <svg width={width} height={height} className="absolute inset-0" aria-hidden="true">
        {shapes.map((m) => renderFigure(m, false))}
        {draftMeasurement ? renderFigure(draftMeasurement, true) : null}
      </svg>
      <div
        data-testid="measurement-capture"
        className={cn(
          'absolute inset-0 touch-none',
          active ? 'pointer-events-auto cursor-crosshair' : 'pointer-events-none',
        )}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => setDraft(null)}
      />
      {shapes.map((m, i) => renderLabel(m, i))}
      {draftMeasurement ? (
        <div
          className="pointer-events-none absolute -translate-x-1/2 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs tabular-nums"
          style={{
            left: Math.min(
              Math.max(
                (xToPx(draftMeasurement.aMs) + xToPx(draftMeasurement.bMs)) / 2,
                LABEL_HALF_WIDTH,
              ),
              Math.max(width - LABEL_HALF_WIDTH, 0),
            ),
            bottom: 4 + (shapes.length % LABEL_ROWS) * 22,
            background: 'var(--ecg-bg)',
            color: 'var(--ecg-measure)',
            borderColor: 'var(--ecg-measure)',
          }}
        >
          {formatMeasurement(draftMeasurement, trace)}
        </div>
      ) : null}
    </div>
  )
}
