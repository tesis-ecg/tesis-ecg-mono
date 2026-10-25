import { ChevronLeft, ChevronRight, Layers } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

import {
  ANNOTATION_SEVERITY,
  annotationChartIcon,
  annotationChartLabel,
  annotationLabel,
  buildAnnotationLinks,
  isAnnotationHighlighted,
} from '../annotationMeta'
import {
  followTimeline,
  formatTimelineTime,
  groupTimelineAnnotations,
  timelineRange,
} from '../timeline'
import type {
  ECGAnnotation,
  ECGAnnotationGroup,
  ECGSignal,
  ECGTimelineRange,
  ECGViewportChange,
} from '../types'

interface ECGMinimapProps {
  signal: ECGSignal
  viewport: ECGViewportChange | null
  onViewportChange: (viewport: ECGViewportChange) => void
  height?: number
  selectedAnnotationId?: string | null
  onAnnotationSelect?: (annotation: ECGAnnotation) => void
  showAnnotations?: boolean
  timelineWindow?: ECGTimelineRange | null
  onTimelineWindowChange?: (range: ECGTimelineRange) => void
  selectedGroup?: ECGAnnotationGroup | null
  onGroupSelect?: (group: ECGAnnotationGroup) => void
}

/** La ventana de contexto se desplaza independientemente del zoom clínico. */
export function ECGMinimap({
  signal,
  viewport,
  onViewportChange,
  height = 64,
  selectedAnnotationId,
  onAnnotationSelect,
  showAnnotations = true,
  timelineWindow,
  onTimelineWindowChange,
  selectedGroup,
  onGroupSelect,
}: ECGMinimapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const [widthPx, setWidthPx] = useState(0)
  const [localWindow, setLocalWindow] = useState(() => {
    const initial = timelineRange(signal.startTimestamp, signal.durationMs)
    return viewport
      ? followTimeline(initial, viewport, signal.startTimestamp, signal.durationMs)
      : initial
  })
  const requestedStart = (timelineWindow ?? localWindow).startMs
  const range = useMemo(
    () => timelineRange(signal.startTimestamp, signal.durationMs, requestedStart),
    [signal.startTimestamp, signal.durationMs, requestedStart],
  )
  const spanMs = range.endMs - range.startMs
  const previousViewport = useRef(viewport)

  // Solo un cambio del trazado provoca seguimiento: explorar la timeline no
  // vuelve inmediatamente al punto que el médico estaba leyendo.
  useEffect(() => {
    const previous = previousViewport.current
    previousViewport.current = viewport
    const moved =
      viewport && (viewport.startMs !== previous?.startMs || viewport.endMs !== previous?.endMs)
    const next = moved
      ? followTimeline(range, viewport, signal.startTimestamp, signal.durationMs)
      : range
    function synchronize() {
      setLocalWindow((current) =>
        current.startMs === next.startMs && current.endMs === next.endMs ? current : next,
      )
      if (timelineWindow?.startMs !== next.startMs || timelineWindow?.endMs !== next.endMs) {
        onTimelineWindowChange?.(next)
      }
    }
    synchronize()
  }, [
    viewport,
    range,
    signal.startTimestamp,
    signal.durationMs,
    timelineWindow?.startMs,
    timelineWindow?.endMs,
    onTimelineWindowChange,
  ])

  const tokens = useMemo(() => readTokens(), [])
  const links = useMemo(() => buildAnnotationLinks(signal.annotations), [signal.annotations])
  const byId = useMemo(
    () => new Map(signal.annotations.map((item) => [item.id, item])),
    [signal.annotations],
  )
  const groups = useMemo(
    () => groupTimelineAnnotations(signal.annotations, range, widthPx),
    [signal.annotations, range, widthPx],
  )
  const bins = useMemo(() => {
    const result = new Map<number, ECGAnnotationGroup[]>()
    for (const group of groups) {
      const bin = result.get(group.startMs)
      if (bin) bin.push(group)
      else result.set(group.startMs, [group])
    }
    return [...result.entries()]
      .sort(([a], [b]) => a - b)
      .map(([startMs, items]) => ({
        startMs,
        items: items.sort(
          (a, b) =>
            ANNOTATION_SEVERITY[b.severity].rank - ANNOTATION_SEVERITY[a.severity].rank ||
            a.kind.localeCompare(b.kind),
        ),
      }))
  }, [groups])
  const stacked = bins.some((bin) => bin.items.length > 1)
  const selectedGroupIds = useMemo(() => new Set(selectedGroup?.annotationIds), [selectedGroup])
  const selected = selectedAnnotationId ? byId.get(selectedAnnotationId) : undefined
  const percent = (time: number) =>
    spanMs > 0 ? Math.max(0, Math.min(100, ((time - range.startMs) / spanMs) * 100)) : 0
  const viewportSpan = viewport ? Math.max(0, viewport.endMs - viewport.startMs) : spanMs
  const intersects =
    !viewport || (viewport.endMs >= range.startMs && viewport.startMs <= range.endMs)
  const selectorWidthPct = Math.min(
    100,
    Math.max(0.5, viewport ? percent(viewport.endMs) - percent(viewport.startMs) : 100),
  )
  const selectorLeftPct = Math.min(100 - selectorWidthPct, viewport ? percent(viewport.startMs) : 0)

  function changeWindow(startMs: number) {
    const next = timelineRange(signal.startTimestamp, signal.durationMs, startMs)
    setLocalWindow(next)
    onTimelineWindowChange?.(next)
  }
  function moveWindow(direction: number) {
    changeWindow(range.startMs + (direction * spanMs) / 2)
  }
  function emitViewport(startMs: number) {
    const width = Math.min(viewportSpan, signal.durationMs)
    const start = Math.max(
      signal.startTimestamp,
      Math.min(signal.startTimestamp + signal.durationMs - width, startMs),
    )
    onViewportChange({ startMs: start, endMs: start + width })
  }

  const drag = useRef<{
    pointerId: number
    x: number
    startMs: number
    mode: 'timeline' | 'viewport'
  } | null>(null)
  const suppressClick = useRef(false)
  function pointerDown(e: React.PointerEvent<HTMLElement>, mode: 'timeline' | 'viewport') {
    if (e.button !== 0 || spanMs <= 0) return
    const width = containerRef.current?.getBoundingClientRect().width ?? 0
    if (width <= 0) return
    suppressClick.current = false
    let startMs = range.startMs
    if (mode === 'viewport') {
      const rect = e.currentTarget.getBoundingClientRect()
      const clickMs = range.startMs + ((e.clientX - rect.left) / rect.width) * spanMs
      const clickPct = ((e.clientX - rect.left) / rect.width) * 100
      const inside =
        intersects && clickPct >= selectorLeftPct && clickPct <= selectorLeftPct + selectorWidthPct
      startMs = inside && viewport ? viewport.startMs : clickMs - viewportSpan / 2
      if (!inside) emitViewport(startMs)
    }
    drag.current = { pointerId: e.pointerId, x: e.clientX, startMs, mode }
    // Los botones conservan el clic; el contenedor captura solo el arrastre.
    if (mode === 'viewport' || !(e.target as HTMLElement).closest('button'))
      e.currentTarget.setPointerCapture(e.pointerId)
  }
  function pointerMove(e: React.PointerEvent<HTMLElement>) {
    const active = drag.current
    if (!active || active.pointerId !== e.pointerId) return
    const width = containerRef.current?.getBoundingClientRect().width ?? 0
    if (width <= 0) return
    const dx = e.clientX - active.x
    if (Math.abs(dx) < 4 && !suppressClick.current) return
    suppressClick.current = true
    if (!e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.setPointerCapture(e.pointerId)
    const delta = (dx / width) * spanMs
    if (active.mode === 'timeline') changeWindow(active.startMs - delta)
    else emitViewport(active.startMs + delta)
  }
  function pointerUp(e: React.PointerEvent<HTMLElement>) {
    if (drag.current?.pointerId !== e.pointerId) return
    if (e.currentTarget.hasPointerCapture(e.pointerId))
      e.currentTarget.releasePointerCapture(e.pointerId)
    drag.current = null
  }
  const panHandlers = {
    onPointerDown: (e: React.PointerEvent<HTMLElement>) => pointerDown(e, 'timeline'),
    onPointerMove: pointerMove,
    onPointerUp: pointerUp,
    onPointerCancel: pointerUp,
  }
  // Dibuja la señal downsampleada (min/max por columna). Re-dibuja solo cuando
  // cambia la señal o el ancho del canvas (no cuando cambia el viewport).
  useEffect(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    const draw = () => {
      const cssWidth = Math.floor(container.clientWidth)
      setWidthPx(cssWidth)
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
      if (samples.length === 0 || cssWidth <= 0 || spanMs <= 0) return
      const columnMins = new Float64Array(cssWidth)
      const columnMaxs = new Float64Array(cssWidth)
      columnMins.fill(Infinity)
      columnMaxs.fill(-Infinity)
      let yMin = Infinity
      let yMax = -Infinity
      for (let i = 0; i < samples.length; i++) {
        const v = samples[i]
        if (!Number.isFinite(v)) continue
        const timestamp = signal.timestampsMs[i]
        const timestampMs =
          signal.timestampsMs.length === samples.length && Number.isFinite(timestamp)
            ? timestamp
            : signal.startTimestamp + (i / Math.max(samples.length - 1, 1)) * signal.durationMs
        if (timestampMs < range.startMs || timestampMs > range.endMs) continue
        const col = Math.min(
          cssWidth - 1,
          Math.floor(((timestampMs - range.startMs) / spanMs) * cssWidth),
        )
        columnMins[col] = Math.min(columnMins[col], v)
        columnMaxs[col] = Math.max(columnMaxs[col], v)
        if (v < yMin) yMin = v
        if (v > yMax) yMax = v
      }
      if (!Number.isFinite(yMin) || !Number.isFinite(yMax)) return
      if (yMin === yMax) {
        yMin -= 1
        yMax += 1
      }
      const yScale = (height - 4) / (yMax - yMin)

      ctx.strokeStyle = tokens.trace
      ctx.lineWidth = 1
      ctx.beginPath()
      for (let col = 0; col < cssWidth; col++) {
        const mn = columnMins[col]
        const mx = columnMaxs[col]
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
  }, [signal, height, tokens.bg, tokens.trace, range.startMs, range.endMs, spanMs])

  function selectGroup(group: ECGAnnotationGroup) {
    if (group.annotationIds.length > 1) onGroupSelect?.(group)
    else onAnnotationSelect?.(byId.get(group.annotationIds[0])!)
  }

  function groupIsSelected(group: ECGAnnotationGroup) {
    return group.annotationIds.some(
      (id) =>
        selectedGroupIds.has(id) ||
        isAnnotationHighlighted(byId.get(id)!, selectedAnnotationId ?? null, links),
    )
  }

  const crossesDate =
    new Date(range.startMs).toDateString() !== new Date(range.endMs).toDateString()
  const rangeLabel = `${formatTimelineTime(range.startMs, true)} – ${formatTimelineTime(range.endMs, true)}`
  const tickCount = widthPx < 480 ? 3 : 5
  const ticks = Array.from(
    { length: tickCount },
    (_, index) => range.startMs + (spanMs * index) / (tickCount - 1),
  )

  return (
    <div
      className="flex w-full min-w-0 shrink-0 flex-col gap-1.5"
      data-testid="ecg-timeline"
      data-start-ms={range.startMs}
      data-end-ms={range.endMs}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-body3 tabular-nums text-gray-600" aria-live="polite">
          {rangeLabel}
        </p>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label="Tramo anterior de la timeline"
            disabled={range.startMs <= signal.startTimestamp}
            onClick={() => moveWindow(-1)}
          >
            <ChevronLeft aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            aria-label="Tramo siguiente de la timeline"
            disabled={range.endMs >= signal.startTimestamp + signal.durationMs}
            onClick={() => moveWindow(1)}
          >
            <ChevronRight aria-hidden />
          </Button>
        </div>
      </div>
      {showAnnotations && signal.annotations.length > 0 && (
        <TooltipProvider delayDuration={200}>
          <div
            className={cn(
              'relative touch-pan-y select-none rounded-md bg-gray-50 cursor-grab active:cursor-grabbing',
              stacked ? 'h-14' : 'h-7',
            )}
            aria-label="Avisos del estudio"
            {...panHandlers}
          >
            {bins.map(({ startMs, items }) => (
              <div
                key={startMs}
                className="absolute inset-y-0"
                style={{
                  left: `${percent(startMs)}%`,
                  width: `${percent(items[0].endMs) - percent(startMs)}%`,
                }}
              >
                {(items.length > 2 ? items.slice(0, 1) : items).map((group, lane) => {
                  const annotation = byId.get(group.annotationIds[0])!
                  const Icon = annotationChartIcon(annotation, links)
                  const count = group.annotationIds.length
                  const isSelected = groupIsSelected(group)
                  const label =
                    count === 1
                      ? annotationChartLabel(annotation, links)
                      : annotationLabel(group.kind)
                  const accessible = `${label}, ${count} ${count === 1 ? 'hallazgo' : 'hallazgos'}, ${formatTimelineTime(group.startMs, crossesDate)} – ${formatTimelineTime(group.endMs, crossesDate)}, severidad ${ANNOTATION_SEVERITY[group.severity].label.toLowerCase()}`
                  return (
                    <Tooltip key={group.kind}>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          aria-label={accessible}
                          aria-pressed={isSelected}
                          data-timeline-group={group.kind}
                          data-group-count={count}
                          onClick={(e) => {
                            if (suppressClick.current && e.detail > 0) return
                            selectGroup(group)
                          }}
                          className={cn(
                            'absolute left-1/2 flex h-6 max-w-full -translate-x-1/2 cursor-pointer items-center justify-center gap-0.5 rounded-full border px-1',
                            'focus-visible:z-20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 hover:brightness-95',
                            isSelected && 'ring-2 ring-primary/40',
                          )}
                          style={{
                            top: lane * 28 + 2,
                            color: `var(--ecg-alert-${group.severity})`,
                            borderColor: `var(--ecg-alert-${group.severity})`,
                            backgroundColor: `var(--ecg-alert-${group.severity}-marker-bg)`,
                          }}
                        >
                          <Icon className="size-3.5 shrink-0" aria-hidden />
                          {count > 1 && (
                            <Badge
                              variant="outline"
                              className="border-current/20 px-1 py-0 text-current tabular-nums"
                              aria-hidden
                            >
                              {count}
                            </Badge>
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent>
                        {accessible}
                        {count === 1 && annotation.description
                          ? ` · ${annotation.description}`
                          : ''}
                      </TooltipContent>
                    </Tooltip>
                  )
                })}
                {items.length > 2 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <button
                        type="button"
                        className={cn(
                          'absolute top-[30px] left-1/2 flex h-6 -translate-x-1/2 cursor-pointer items-center gap-1 rounded-full border border-border bg-card px-1.5 text-xs text-gray-700 hover:bg-gray-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                          items.slice(1).some(groupIsSelected) && 'ring-2 ring-primary/40',
                        )}
                        aria-label={`Ver otros ${items.length - 1} tipos de hallazgo en este tramo`}
                        onPointerDown={(e) => e.stopPropagation()}
                        data-timeline-overflow={items.length - 1}
                      >
                        <Layers className="size-3.5" aria-hidden />
                        <span className="tabular-nums">+{items.length - 1}</span>
                      </button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="center" className="max-w-xs">
                      <DropdownMenuLabel className="text-xs font-normal tabular-nums text-gray-600">
                        {formatTimelineTime(startMs, crossesDate)} –{' '}
                        {formatTimelineTime(items[0].endMs, crossesDate)}
                      </DropdownMenuLabel>
                      {items.slice(1).map((group) => {
                        const annotation = byId.get(group.annotationIds[0])!
                        const Icon = annotationChartIcon(annotation, links)
                        return (
                          <DropdownMenuItem
                            key={group.kind}
                            onSelect={() => selectGroup(group)}
                            className={cn(groupIsSelected(group) && 'bg-primary-50')}
                          >
                            <Icon
                              className="size-4"
                              style={{ color: `var(--ecg-alert-${group.severity})` }}
                              aria-hidden
                            />
                            <span className="min-w-0 flex-1">{annotationLabel(group.kind)}</span>
                            <Badge
                              variant={ANNOTATION_SEVERITY[group.severity].badgeVariant}
                              className="tabular-nums"
                            >
                              {group.annotationIds.length}
                            </Badge>
                          </DropdownMenuItem>
                        )
                      })}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
              </div>
            ))}
          </div>
        </TooltipProvider>
      )}
      <div className="min-w-0">
        <div
          className="flex touch-pan-y cursor-grab select-none justify-between gap-1 text-xs tabular-nums text-gray-500 active:cursor-grabbing"
          {...panHandlers}
        >
          {ticks.map((time, index) => (
            <time
              key={index}
              dateTime={new Date(time).toISOString()}
              title={formatTimelineTime(time, true)}
            >
              {formatTimelineTime(time)}
            </time>
          ))}
        </div>
        <div
          ref={containerRef}
          role="group"
          tabIndex={0}
          aria-label="Navegación general del ECG"
          className="relative w-full touch-pan-y cursor-pointer select-none overflow-hidden rounded-md border border-border bg-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          style={{ height }}
          onPointerDown={(e) => pointerDown(e, 'viewport')}
          onPointerMove={pointerMove}
          onPointerUp={pointerUp}
          onPointerCancel={pointerUp}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
              e.preventDefault()
              const direction = e.key === 'ArrowLeft' ? -1 : 1
              emitViewport((viewport?.startMs ?? range.startMs) + (direction * viewportSpan) / 2)
            }
          }}
        >
          <canvas ref={canvasRef} className="absolute inset-0 block" />
          {showAnnotations &&
            selected &&
            selected.endMs >= range.startMs &&
            selected.startMs <= range.endMs && (
              <span
                data-testid={`timeline-annotation-range-${selected.id}`}
                className="pointer-events-none absolute inset-y-0 min-w-1 border-x"
                style={{
                  left: `${percent(selected.startMs)}%`,
                  width: `${percent(selected.endMs) - percent(selected.startMs)}%`,
                  borderColor: `var(--ecg-alert-${selected.severity})`,
                  backgroundColor: `var(--ecg-alert-${selected.severity}-timeline-bg)`,
                }}
              />
            )}
          {intersects && (
            <div
              data-testid="timeline-viewport"
              className="pointer-events-none absolute inset-y-0 border-2"
              style={{
                left: `${selectorLeftPct}%`,
                width: `${selectorWidthPct}%`,
                borderColor: 'var(--ecg-selector)',
                backgroundColor: 'var(--ecg-selector-bg)',
              }}
            />
          )}
        </div>
      </div>
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
