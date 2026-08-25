import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

import { cn } from '@/lib/utils'

import {
  ANNOTATION_SEVERITY,
  annotationIcon,
  annotationLabel,
  compareAnnotationsForPainting,
} from '../annotationMeta'
import { centerRangeAt, layoutVisibleAnnotationLabels } from '../annotationLayout'
import { drawAnnotationBands, type ECGAnnotationColors } from '../annotationPlugin'
import type {
  ECGAnnotationSeverity,
  ECGSignal,
  ECGViewerHandle,
  ECGViewerProps,
  ECGViewportChange,
} from '../types'
import { formatTimestampMs, formatTimestampShort } from '../utils/formatEcgTimestamp'

/**
 * Lee los tokens CSS del ECG desde `document.documentElement`. uPlot pinta sobre
 * canvas (no consume utilities Tailwind), así que necesitamos pasarle los
 * colores como strings — no como `bg-*` classes.
 *
 * Nota: uPlot lee los colores una sola vez al crearse. Si el usuario cambia
 * `[data-theme]` después de montar, el trace NO se repinta automáticamente.
 * Limitation aceptada en TES-22 (el toggle de dark theme tampoco existe en la
 * app todavía).
 */
interface EcgTokens {
  trace: string
  grid: string
  bg: string
  fg: string
  alerts: ECGAnnotationColors
}

function readEcgTokens(): EcgTokens {
  const root = document.documentElement
  const style = getComputedStyle(root)
  return {
    trace: style.getPropertyValue('--ecg-trace').trim() || '#0b2185',
    grid: style.getPropertyValue('--ecg-grid').trim() || '#e0e1e3',
    bg: style.getPropertyValue('--ecg-bg').trim() || '#ffffff',
    fg: style.getPropertyValue('--color-fg-muted').trim() || '#727f87',
    alerts: {
      low: readAlertToken(style, 'low', '#727f87', 'rgba(114, 127, 135, 0.16)'),
      medium: readAlertToken(style, 'medium', '#294dec', 'rgba(41, 77, 236, 0.14)'),
      high: readAlertToken(style, 'high', '#b86a16', 'rgba(239, 196, 130, 0.24)'),
      critical: readAlertToken(style, 'critical', '#c53f34', 'rgba(236, 127, 116, 0.22)'),
    },
  }
}

function readAlertToken(
  style: CSSStyleDeclaration,
  severity: ECGAnnotationSeverity,
  fallbackStroke: string,
  fallbackFill: string,
): { stroke: string; fill: string } {
  return {
    stroke: style.getPropertyValue(`--ecg-alert-${severity}`).trim() || fallbackStroke,
    fill: style.getPropertyValue(`--ecg-alert-${severity}-bg`).trim() || fallbackFill,
  }
}

/**
 * `<ECGViewer />` — renderiza una traza ECG de canal único con uPlot.
 *
 * TES-22 estableció la base estática. TES-23 agrega interacción (zoom, pan,
 * teclado) y la API imperativa (`jumpTo`, `zoomToRange`, `resetZoom`) sin
 * romper la firma pública.
 *
 * Implementación:
 * - Una instancia uPlot por viewer, mantenida en `useRef`. Se crea en mount,
 *   se destruye en unmount.
 * - X axis en **segundos** desde el inicio del estudio (no timestamps absolutos
 *   reales — el formatter lo deriva de `startTimestamp`).
 * - Zoom con `Ctrl/Cmd + wheel`. Pan con drag o flechas izq/der cuando tiene
 *   focus.
 * - Tooltip mostrado vía la legend nativa de uPlot, con formatter custom para
 *   timestamp en `HH:MM:SS.mmm`.
 */
export const ECGViewer = forwardRef<ECGViewerHandle, ECGViewerProps>(function ECGViewer(
  {
    signal,
    height = 400,
    paperSpeed: _paperSpeed = 25,
    amplitude: _amplitude = 10,
    initialWindowSec = 10,
    initialViewport,
    onViewportChange,
    selectedAnnotationId = null,
    onAnnotationSelect,
  },
  ref,
) {
  void _paperSpeed
  void _amplitude

  const containerRef = useRef<HTMLDivElement | null>(null)
  const labelsOverlayRef = useRef<HTMLDivElement | null>(null)
  const uplotRef = useRef<uPlot | null>(null)
  const [overlayViewport, setOverlayViewport] = useState<ECGViewportChange | null>(null)
  const [plotArea, setPlotArea] = useState<{
    left: number
    top: number
    width: number
    height: number
  } | null>(null)
  const [labelWidths, setLabelWidths] = useState<ReadonlyMap<string, number>>(() => new Map())
  const selectedAnnotationIdRef = useRef<string | null>(selectedAnnotationId)
  const onAnnotationSelectRef = useRef(onAnnotationSelect)
  // El último viewport notificado (en segundos), para no disparar el callback
  // con valores idénticos durante interacciones continuas.
  const lastViewportRef = useRef<{ min: number; max: number } | null>(null)
  // Callback estable — guardarlo en ref para que los handlers de eventos no se
  // re-creen en cada render cuando el padre pasa un closure nuevo.
  const onViewportChangeRef = useRef(onViewportChange)
  useEffect(() => {
    onViewportChangeRef.current = onViewportChange
  }, [onViewportChange])
  useEffect(() => {
    onAnnotationSelectRef.current = onAnnotationSelect
  }, [onAnnotationSelect])
  useEffect(() => {
    selectedAnnotationIdRef.current = selectedAnnotationId
    uplotRef.current?.redraw()
  }, [selectedAnnotationId])

  // Eje X precalculado en segundos desde el inicio. Memoizado por largo y
  // sample rate para evitar reallocar 900k floats en cada render.
  const xs = useMemo(() => buildXAxis(signal), [signal])
  const annotationDrawOrder = useMemo(
    () => [...signal.annotations].sort(compareAnnotationsForPainting),
    [signal.annotations],
  )

  const annotationLabelLayouts = useMemo(() => {
    if (!overlayViewport || !plotArea) return []
    return layoutVisibleAnnotationLabels({
      annotations: signal.annotations,
      viewportStartMs: overlayViewport.startMs,
      viewportEndMs: overlayViewport.endMs,
      plotWidthPx: plotArea.width,
      labelWidths,
      selectedAnnotationId,
    })
  }, [labelWidths, overlayViewport, plotArea, selectedAnnotationId, signal.annotations])

  useLayoutEffect(() => {
    const overlay = labelsOverlayRef.current
    if (!overlay) return
    const nextWidths = new Map(labelWidths)
    let changed = false
    for (const element of overlay.querySelectorAll<HTMLElement>('[data-annotation-label-id]')) {
      const id = element.dataset.annotationLabelId
      if (!id) continue
      const width = Math.ceil(element.getBoundingClientRect().width)
      if (width > 0 && nextWidths.get(id) !== width) {
        nextWidths.set(id, width)
        changed = true
      }
    }
    if (changed) setLabelWidths(nextWidths)
  }, [annotationLabelLayouts, labelWidths])

  // Rango completo del estudio, en segundos.
  const durationSec = signal.durationMs / 1000

  // Crea la instancia uPlot al montar. En Strict Mode el efecto corre dos
  // veces en dev — el cleanup destruye la primera instancia correctamente.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const tokens = readEcgTokens()
    const initialWidth = Math.max(container.clientWidth, 600)
    const startTimestamp = signal.startTimestamp

    const syncPlotArea = (inst: uPlot) => {
      const next = {
        left: inst.over.offsetLeft,
        top: inst.over.offsetTop,
        width: inst.over.clientWidth,
        height: inst.over.clientHeight,
      }
      setPlotArea((current) =>
        current &&
        current.left === next.left &&
        current.top === next.top &&
        current.width === next.width &&
        current.height === next.height
          ? current
          : next,
      )
    }

    const opts: uPlot.Options = {
      width: initialWidth,
      height,
      pxAlign: 0,
      legend: {
        show: true,
        markers: { show: false },
      },
      cursor: {
        show: true,
        x: true,
        y: true,
        points: { show: false },
        drag: { x: true, y: false, uni: 50 },
      },
      scales: {
        x: { time: false },
        y: { auto: true },
      },
      axes: [
        {
          stroke: tokens.fg,
          ticks: { stroke: tokens.grid, width: 1 },
          grid: { stroke: tokens.grid, width: 1 },
          values: (_self, splits) => splits.map((v) => formatTimestampShort(v * 1000)),
          size: 30,
        },
        {
          stroke: tokens.fg,
          ticks: { stroke: tokens.grid, width: 1 },
          grid: { stroke: tokens.grid, width: 1 },
          values: (_self, splits) => splits.map((v) => `${v.toFixed(1)} mV`),
          size: 60,
        },
      ],
      series: [
        {
          label: 'Tiempo',
          value: (_self, v) => (v == null ? '—' : formatTimestampMs(v * 1000)),
        },
        {
          label: 'ECG',
          stroke: tokens.trace,
          width: 1,
          points: { show: false },
          spanGaps: false,
          value: (_self, v) => (v == null ? '—' : `${v.toFixed(3)} mV`),
        },
      ],
      hooks: {
        drawClear: [
          (u) => {
            drawAnnotationBands(
              u,
              annotationDrawOrder,
              startTimestamp,
              tokens.alerts,
              selectedAnnotationIdRef.current,
            )
          },
        ],
        setScale: [
          (u, scaleKey) => {
            if (scaleKey !== 'x') return
            const { min, max } = u.scales.x
            if (min == null || max == null) return
            const last = lastViewportRef.current
            if (last && last.min === min && last.max === max) return
            lastViewportRef.current = { min, max }
            const nextViewport = {
              startMs: startTimestamp + min * 1000,
              endMs: startTimestamp + max * 1000,
            }
            setOverlayViewport((current) =>
              current?.startMs === nextViewport.startMs && current.endMs === nextViewport.endMs
                ? current
                : nextViewport,
            )
            onViewportChangeRef.current?.(nextViewport)
          },
        ],
      },
    }

    const data: uPlot.AlignedData = [
      xs as unknown as number[],
      signal.samples as unknown as number[],
    ]

    const u = new uPlot(opts, data, container)
    uplotRef.current = u

    // Viewport inicial: `initialViewport` gana si está; si no, últimos
    // `initialWindowSec` segundos. Si la señal es más corta que la ventana
    // pedida, mostramos el rango completo.
    if (initialViewport) {
      const startSec = Math.max(0, (initialViewport.startMs - signal.startTimestamp) / 1000)
      const endSec = Math.min(durationSec, (initialViewport.endMs - signal.startTimestamp) / 1000)
      if (endSec > startSec) {
        u.setScale('x', { min: startSec, max: endSec })
      } else {
        u.setScale('x', { min: 0, max: durationSec })
      }
    } else {
      const initialSpan = Math.min(initialWindowSec, durationSec)
      u.setScale('x', { min: durationSec - initialSpan, max: durationSec })
    }
    syncPlotArea(u)

    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry || !uplotRef.current) return
      const w = Math.floor(entry.contentRect.width)
      if (w > 0) {
        uplotRef.current.setSize({ width: w, height })
        syncPlotArea(uplotRef.current)
      }
    })
    ro.observe(container)

    // Wheel zoom (Ctrl/Cmd + scroll). Mantiene el punto bajo el cursor en su
    // posición — la convención clínica esperada.
    const handleWheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return
      e.preventDefault()
      const inst = uplotRef.current
      if (!inst) return
      const { min, max } = inst.scales.x
      if (min == null || max == null) return
      const rect = inst.over.getBoundingClientRect()
      const px = e.clientX - rect.left
      if (px < 0 || px > rect.width) return
      const cursorVal = inst.posToVal(px, 'x')
      const factor = e.deltaY > 0 ? 1.2 : 1 / 1.2
      const newMin = cursorVal - (cursorVal - min) * factor
      const newMax = cursorVal + (max - cursorVal) * factor
      const [clampedMin, clampedMax] = clampRange(newMin, newMax, 0, durationSec)
      inst.setScale('x', { min: clampedMin, max: clampedMax })
    }
    container.addEventListener('wheel', handleWheel, { passive: false })

    // Pan con drag — botón izquierdo, sin Ctrl. (Ctrl+drag mantiene el zoom
    // selection nativo de uPlot.)
    let panStart: { px: number; min: number; max: number } | null = null
    let suppressClick = false
    let suppressClickTimeout: number | null = null
    const handlePointerDown = (e: PointerEvent) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return
      const inst = uplotRef.current
      if (!inst) return
      const { min, max } = inst.scales.x
      if (min == null || max == null) return
      panStart = { px: e.clientX, min, max }
      container.setPointerCapture(e.pointerId)
      container.style.cursor = 'grabbing'
    }
    const handlePointerMove = (e: PointerEvent) => {
      if (!panStart) return
      const inst = uplotRef.current
      if (!inst) return
      const rect = inst.over.getBoundingClientRect()
      const dxPx = e.clientX - panStart.px
      if (Math.abs(dxPx) > 3) suppressClick = true
      const viewWidthSec = panStart.max - panStart.min
      const dSec = -(dxPx / rect.width) * viewWidthSec
      const [clampedMin, clampedMax] = clampRange(
        panStart.min + dSec,
        panStart.max + dSec,
        0,
        durationSec,
      )
      inst.setScale('x', { min: clampedMin, max: clampedMax })
    }
    const handlePointerUp = (e: PointerEvent) => {
      if (!panStart) return
      panStart = null
      container.releasePointerCapture(e.pointerId)
      container.style.cursor = ''
      if (suppressClick) {
        // Algunos navegadores no emiten `click` al finalizar un drag. Sin este
        // reset, la próxima selección real quedaba consumida y parecía exigir
        // doble click.
        suppressClickTimeout = window.setTimeout(() => {
          suppressClick = false
          suppressClickTimeout = null
        }, 0)
      }
    }
    container.addEventListener('pointerdown', handlePointerDown)
    container.addEventListener('pointermove', handlePointerMove)
    container.addEventListener('pointerup', handlePointerUp)
    container.addEventListener('pointercancel', handlePointerUp)

    const handleClick = (e: MouseEvent) => {
      if (suppressClick) {
        suppressClick = false
        if (suppressClickTimeout != null) {
          window.clearTimeout(suppressClickTimeout)
          suppressClickTimeout = null
        }
        return
      }
      const inst = uplotRef.current
      if (!inst || annotationDrawOrder.length === 0) return
      const rect = inst.over.getBoundingClientRect()
      const x = e.clientX - rect.left
      if (x < 0 || x > rect.width) return
      const clickedMs = startTimestamp + inst.posToVal(x, 'x') * 1000
      const { min, max } = inst.scales.x
      const toleranceMs =
        min != null && max != null ? ((max - min) * 1000 * Math.max(8, 1)) / rect.width : 0
      const hit = [...annotationDrawOrder]
        .reverse()
        .find(
          (annotation) =>
            clickedMs >= annotation.startMs - toleranceMs &&
            clickedMs <= Math.max(annotation.endMs, annotation.startMs) + toleranceMs,
        )
      if (hit) onAnnotationSelectRef.current?.(hit)
    }
    container.addEventListener('click', handleClick)

    // Teclado: flechas mueven el viewport en 10% del ancho actual.
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
      const inst = uplotRef.current
      if (!inst) return
      const { min, max } = inst.scales.x
      if (min == null || max == null) return
      const span = max - min
      const dir = e.key === 'ArrowLeft' ? -1 : 1
      const step = span * 0.1 * dir
      const [clampedMin, clampedMax] = clampRange(min + step, max + step, 0, durationSec)
      inst.setScale('x', { min: clampedMin, max: clampedMax })
      e.preventDefault()
    }
    container.addEventListener('keydown', handleKeyDown)

    return () => {
      container.removeEventListener('wheel', handleWheel)
      container.removeEventListener('pointerdown', handlePointerDown)
      container.removeEventListener('pointermove', handlePointerMove)
      container.removeEventListener('pointerup', handlePointerUp)
      container.removeEventListener('pointercancel', handlePointerUp)
      container.removeEventListener('click', handleClick)
      container.removeEventListener('keydown', handleKeyDown)
      if (suppressClickTimeout != null) window.clearTimeout(suppressClickTimeout)
      ro.disconnect()
      uplotRef.current?.destroy()
      uplotRef.current = null
      lastViewportRef.current = null
    }
  }, [signal, height, xs, annotationDrawOrder, durationSec, initialWindowSec, initialViewport])

  // API imperativa — convierte timestamps absolutos a segundos desde el inicio.
  useImperativeHandle(
    ref,
    () => ({
      jumpTo(timestampMs: number) {
        const inst = uplotRef.current
        if (!inst) return
        const { min, max } = inst.scales.x
        if (min == null || max == null) return
        const targetSec = (timestampMs - signal.startTimestamp) / 1000
        const [newMin, newMax] = centerRangeAt(targetSec, min, max, 0, durationSec)
        inst.setScale('x', { min: newMin, max: newMax })
      },
      zoomToRange(startMs: number, endMs: number) {
        const inst = uplotRef.current
        if (!inst) return
        const startSec = (startMs - signal.startTimestamp) / 1000
        const endSec = (endMs - signal.startTimestamp) / 1000
        const [newMin, newMax] = clampRange(startSec, endSec, 0, durationSec)
        inst.setScale('x', { min: newMin, max: newMax })
      },
      resetZoom() {
        const inst = uplotRef.current
        if (!inst) return
        inst.setScale('x', { min: 0, max: durationSec })
      },
    }),
    [signal.startTimestamp, durationSec],
  )

  return (
    <div className="relative w-full" style={{ height }}>
      <div
        ref={containerRef}
        className="h-full w-full cursor-grab rounded-md outline-none focus:ring-2 focus:ring-primary/40"
        aria-label="Gráfico ECG interactivo"
        tabIndex={0}
      />
      {plotArea && annotationLabelLayouts.length > 0 ? (
        <div
          ref={labelsOverlayRef}
          className="pointer-events-none absolute overflow-hidden"
          style={{
            left: plotArea.left,
            top: plotArea.top,
            width: plotArea.width,
            height: plotArea.height,
          }}
          aria-label="Avisos visibles en el gráfico"
        >
          {annotationLabelLayouts.map(({ annotation, lane, leftPx }) => {
            const Icon = annotationIcon(annotation.category)
            const severity = ANNOTATION_SEVERITY[annotation.severity]
            const isSelected = annotation.id === selectedAnnotationId
            const label = annotationLabel(annotation.kind)
            return (
              <button
                key={annotation.id}
                type="button"
                data-annotation-label-id={annotation.id}
                aria-label={`${label}, severidad ${severity.label.toLowerCase()}`}
                aria-pressed={isSelected}
                title={`${label} · ${severity.label}`}
                onClick={() => onAnnotationSelect?.(annotation)}
                className={cn(
                  'pointer-events-auto absolute flex h-6 max-w-full cursor-pointer items-center gap-1 rounded-full border px-2 text-xs font-medium shadow-sm backdrop-blur-sm',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                  isSelected && 'ring-2 ring-primary/50',
                )}
                style={{
                  left: leftPx,
                  top: 4 + lane * 28,
                  zIndex: isSelected ? 20 : 10,
                  color: `var(--ecg-alert-${annotation.severity})`,
                  borderColor: `var(--ecg-alert-${annotation.severity})`,
                  backgroundColor: `var(--ecg-alert-${annotation.severity}-bg)`,
                }}
              >
                <Icon className="size-3.5 shrink-0" aria-hidden />
                <span className="truncate whitespace-nowrap">{label}</span>
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
})

function buildXAxis(signal: ECGSignal): Float64Array {
  const n = signal.samples.length
  const xs = new Float64Array(n)
  const dt = n > 0 ? signal.durationMs / 1000 / n : 0
  for (let i = 0; i < n; i++) xs[i] = i * dt
  return xs
}

/**
 * Clampea el par `[min, max]` dentro de `[boundsMin, boundsMax]` preservando
 * el span — si el rango pedido es más ancho que las cotas, devuelve las cotas
 * completas.
 */
function clampRange(
  min: number,
  max: number,
  boundsMin: number,
  boundsMax: number,
): [number, number] {
  const span = max - min
  const totalSpan = boundsMax - boundsMin
  if (span >= totalSpan) return [boundsMin, boundsMax]
  if (min < boundsMin) return [boundsMin, boundsMin + span]
  if (max > boundsMax) return [boundsMax - span, boundsMax]
  return [min, max]
}
