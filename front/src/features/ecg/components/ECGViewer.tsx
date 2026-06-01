import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'

import type { ECGSignal, ECGViewerHandle, ECGViewerProps } from '../types'
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
function readEcgTokens(): { trace: string; grid: string; bg: string; fg: string } {
  const root = document.documentElement
  const style = getComputedStyle(root)
  return {
    trace: style.getPropertyValue('--ecg-trace').trim() || '#0b2185',
    grid: style.getPropertyValue('--ecg-grid').trim() || '#e0e1e3',
    bg: style.getPropertyValue('--ecg-bg').trim() || '#ffffff',
    fg: style.getPropertyValue('--color-fg-muted').trim() || '#727f87',
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
    onViewportChange,
  },
  ref,
) {
  void _paperSpeed
  void _amplitude

  const containerRef = useRef<HTMLDivElement | null>(null)
  const uplotRef = useRef<uPlot | null>(null)
  // El último viewport notificado (en segundos), para no disparar el callback
  // con valores idénticos durante interacciones continuas.
  const lastViewportRef = useRef<{ min: number; max: number } | null>(null)
  // Callback estable — guardarlo en ref para que los handlers de eventos no se
  // re-creen en cada render cuando el padre pasa un closure nuevo.
  const onViewportChangeRef = useRef(onViewportChange)
  useEffect(() => {
    onViewportChangeRef.current = onViewportChange
  }, [onViewportChange])

  // Eje X precalculado en segundos desde el inicio. Memoizado por largo y
  // sample rate para evitar reallocar 900k floats en cada render.
  const xs = useMemo(() => buildXAxis(signal), [signal])

  // Rango completo del estudio, en segundos.
  const durationSec = signal.samples.length / signal.sampleRate

  // Crea la instancia uPlot al montar. En Strict Mode el efecto corre dos
  // veces en dev — el cleanup destruye la primera instancia correctamente.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const tokens = readEcgTokens()
    const initialWidth = Math.max(container.clientWidth, 600)
    const startTimestamp = signal.startTimestamp

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
        setScale: [
          (u, scaleKey) => {
            if (scaleKey !== 'x') return
            const { min, max } = u.scales.x
            if (min == null || max == null) return
            const last = lastViewportRef.current
            if (last && last.min === min && last.max === max) return
            lastViewportRef.current = { min, max }
            onViewportChangeRef.current?.({
              startMs: startTimestamp + min * 1000,
              endMs: startTimestamp + max * 1000,
            })
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

    // Viewport inicial: últimos `initialWindowSec` segundos. Si la señal es más
    // corta que la ventana pedida, mostramos el rango completo.
    const initialSpan = Math.min(initialWindowSec, durationSec)
    u.setScale('x', { min: durationSec - initialSpan, max: durationSec })

    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry || !uplotRef.current) return
      const w = Math.floor(entry.contentRect.width)
      if (w > 0) uplotRef.current.setSize({ width: w, height })
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
    }
    container.addEventListener('pointerdown', handlePointerDown)
    container.addEventListener('pointermove', handlePointerMove)
    container.addEventListener('pointerup', handlePointerUp)
    container.addEventListener('pointercancel', handlePointerUp)

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
      container.removeEventListener('keydown', handleKeyDown)
      ro.disconnect()
      uplotRef.current?.destroy()
      uplotRef.current = null
      lastViewportRef.current = null
    }
  }, [signal, height, xs, durationSec, initialWindowSec])

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
        const halfSpan = (max - min) / 2
        const [newMin, newMax] = clampRange(
          targetSec - halfSpan,
          targetSec + halfSpan,
          0,
          durationSec,
        )
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
    <div
      ref={containerRef}
      className="w-full cursor-grab outline-none focus:ring-2 focus:ring-primary/40 rounded-md"
      style={{ height }}
      tabIndex={0}
    />
  )
})

function buildXAxis(signal: ECGSignal): Float64Array {
  const n = signal.samples.length
  const xs = new Float64Array(n)
  const dt = 1 / signal.sampleRate
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
