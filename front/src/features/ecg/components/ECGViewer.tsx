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
  annotationChartIcon,
  annotationChartLabel,
  buildAnnotationLinks,
  compareAnnotationsForPainting,
  isAnnotationHighlighted,
} from '../annotationMeta'
import { centerRangeAt, layoutVisibleAnnotationLabels } from '../annotationLayout'
import { drawAnnotationBands, type ECGAnnotationColors } from '../annotationPlugin'
import { drawPaperGrid, type PaperGridColors } from '../paperGridPlugin'
import {
  DEFAULT_AMPLITUDE,
  DEFAULT_PAPER_SPEED,
  autoVerticalRange,
  baselineMv,
  pannedCenterMv,
  matchesScale,
  measurePxPerMm,
  paperScale,
  verticalRange,
  visibleSeconds,
  type PaperScale,
} from '../paperScale'
import type {
  ECGAnnotation,
  ECGAnnotationSeverity,
  ECGSignal,
  ECGViewerHandle,
  ECGViewerProps,
  ECGViewportChange,
} from '../types'
import { formatWallClock, formatWallClockShort } from '../utils/formatEcgTimestamp'
import { latestTimestampMs } from '../utils/processedRange'
import { sampleRangeForSeconds } from '../utils/sampleRange'
import { coversRange, detailRangeFor, mergeDetail, type DetailRange } from '../utils/traceDetail'

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
  paperGrid: PaperGridColors
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
    paperGrid: {
      minor: style.getPropertyValue('--ecg-grid-minor').trim() || 'rgba(214, 138, 128, 0.35)',
      major: style.getPropertyValue('--ecg-grid-major').trim() || 'rgba(197, 92, 78, 0.55)',
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

const NO_ANNOTATIONS: ECGAnnotation[] = []

/** Espera tras el último zoom o desplazamiento antes de pedir muestras. */
const DETAIL_DEBOUNCE_MS = 150

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
 * - Zoom con `Ctrl/Cmd + wheel`. Pan con drag o flechas cuando tiene focus.
 *   Con ganancia fija el pan también es vertical: un trazado que se sale de
 *   los mV que entran en pantalla se alcanza arrastrando, sin cambiar la
 *   escala. En amplitud automática el rango lo pone la señal y no se mueve.
 * - Tooltip mostrado vía la legend nativa de uPlot, con formatter custom para
 *   timestamp en `HH:MM:SS.mmm`.
 */
export const ECGViewer = forwardRef<ECGViewerHandle, ECGViewerProps>(function ECGViewer(
  {
    signal,
    height = 400,
    paperSpeed = DEFAULT_PAPER_SPEED,
    amplitude = DEFAULT_AMPLITUDE,
    initialViewport,
    initialWindowSeconds,
    followLatest = false,
    initialCursorMs,
    onViewportChange,
    onCursorChange,
    onScaleMatchChange,
    selectedAnnotationId = null,
    onAnnotationSelect,
    showAnnotations = true,
    toolbar,
  },
  ref,
) {
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
  // Sobrevive a la recreación de uPlot cuando llega una señal nueva por
  // polling. Es absoluto porque la duración crece y el eje puede tener huecos.
  const preservedViewportRef = useRef<ECGViewportChange | null>(initialViewport ?? null)
  const followsLatestRef = useRef(followLatest)
  const isInitializingFrameRef = useRef(false)
  // El último viewport notificado (en segundos), para no disparar el callback
  // con valores idénticos durante interacciones continuas.
  const lastViewportRef = useRef<{ min: number; max: number } | null>(null)
  // Callback estable — guardarlo en ref para que los handlers de eventos no se
  // re-creen en cada render cuando el padre pasa un closure nuevo.
  const onViewportChangeRef = useRef(onViewportChange)
  const onCursorChangeRef = useRef(onCursorChange)
  const cursorTimestampRef = useRef<number>(initialCursorMs ?? latestTimestampMs(signal))
  const hasCursorAnchorRef = useRef(initialCursorMs != null)
  useEffect(() => {
    onViewportChangeRef.current = onViewportChange
  }, [onViewportChange])
  useEffect(() => {
    onCursorChangeRef.current = onCursorChange
  }, [onCursorChange])
  useEffect(() => {
    onAnnotationSelectRef.current = onAnnotationSelect
  }, [onAnnotationSelect])
  useEffect(() => {
    selectedAnnotationIdRef.current = selectedAnnotationId
    uplotRef.current?.redraw()
  }, [selectedAnnotationId])

  // --- Escala clínica ------------------------------------------------------ #
  //
  // Se mide una sola vez por montaje: es una lectura de layout y no cambia
  // salvo que el usuario mueva el zoom del navegador.
  const [pxPerMm] = useState(measurePxPerMm)
  const scale = useMemo(
    () => paperScale(paperSpeed, amplitude, pxPerMm),
    [paperSpeed, amplitude, pxPerMm],
  )
  // La escala viaja por ref y no por la dependencia del efecto a propósito:
  // `scales.y.range` y el hook de la grilla la leen en cada dibujo, así que
  // cambiar la ganancia repinta sin recrear la instancia de uPlot (que
  // significaría perder el viewport y rearmar el canvas).
  const scaleRef = useRef<PaperScale>(scale)
  // Desplazamiento vertical que eligió el médico, en mV sobre la línea de base
  // de la ventana visible. Relativo y no absoluto: así acompaña a la traza
  // cuando la línea de base deriva al desplazarse en el tiempo.
  const verticalOffsetRef = useRef(0)
  const onScaleMatchChangeRef = useRef(onScaleMatchChange)
  useEffect(() => {
    onScaleMatchChangeRef.current = onScaleMatchChange
  }, [onScaleMatchChange])

  // Eje X precalculado en segundos desde el inicio. Memoizado por largo y
  // sample rate para evitar reallocar 900k floats en cada render.
  const xs = useMemo(() => buildXAxis(signal), [signal])
  const ys = useMemo(() => buildYSeries(signal), [signal])
  // Ocultar los avisos no los borra: el panel los sigue listando. Viajan a
  // uPlot por ref y solo repintan: recrear la instancia para esto le hacía
  // perder al médico el zoom y el cursor cada vez que los alternaba.
  const annotations = showAnnotations ? signal.annotations : NO_ANNOTATIONS
  const annotationDrawOrder = useMemo(
    () => [...annotations].sort(compareAnnotationsForPainting),
    [annotations],
  )
  const annotationLinks = useMemo(() => buildAnnotationLinks(annotations), [annotations])
  const annotationDrawOrderRef = useRef(annotationDrawOrder)
  const annotationLinksRef = useRef(annotationLinks)
  useEffect(() => {
    if (
      annotationDrawOrderRef.current === annotationDrawOrder &&
      annotationLinksRef.current === annotationLinks
    )
      return
    annotationDrawOrderRef.current = annotationDrawOrder
    annotationLinksRef.current = annotationLinks
    uplotRef.current?.redraw(false)
  }, [annotationDrawOrder, annotationLinks])

  const annotationLabelLayouts = useMemo(() => {
    if (!overlayViewport || !plotArea) return []
    return layoutVisibleAnnotationLabels({
      annotations,
      viewportStartMs: overlayViewport.startMs,
      viewportEndMs: overlayViewport.endMs,
      plotWidthPx: plotArea.width,
      labelWidths,
      selectedAnnotationId,
    })
  }, [labelWidths, overlayViewport, plotArea, selectedAnnotationId, annotations])

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
    const timeVerified = signal.metadata?.startTimeVerified !== false

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

    // --- Muestras de cerca ------------------------------------------------- #
    //
    // `signal.samples` es un resumen: un mínimo y un máximo por balde. Cuando
    // lo visible pide más detalle que un balde por píxel, se descargan las
    // muestras del tramo y se empalman en el lugar del resumen; al alejarse se
    // vuelve a él. `data-trace` dice cuál de los dos está en pantalla.
    const detail = signal.detail
    const overviewBucket = signal.metadata?.overviewSamplesPerBucket ?? null
    let detailLoaded: DetailRange | null = null
    let detailTimer: number | null = null
    let detailAbort: AbortController | null = null
    container.dataset.trace = overviewBucket === null ? 'samples' : 'overview'

    const showOverview = (inst: uPlot) => {
      if (detailLoaded === null) return
      detailLoaded = null
      container.dataset.trace = 'overview'
      inst.setData([xs, ys], false)
      inst.redraw()
    }

    const updateDetail = () => {
      detailTimer = null
      const inst = uplotRef.current
      if (!inst || !detail || overviewBucket === null) return
      const { min, max } = inst.scales.x
      if (min == null || max == null) return
      // De puntos del resumen a muestras: cada par mín/máx es un balde. Un
      // punto de más a la izquierda por el balde que asoma cortado.
      const [fromPoint, toPoint] = sampleRangeForSeconds(signal, min, max)
      const visible = {
        startSample: Math.floor(Math.max(fromPoint - 1, 0) / 2) * overviewBucket,
        endSample: Math.min(Math.ceil(toPoint / 2) * overviewBucket, detail.sampleCount),
      }
      const wanted = detailRangeFor({
        visibleStartSample: visible.startSample,
        visibleEndSample: visible.endSample,
        plotWidthPx: plotWidthPx(inst),
        overviewSamplesPerBucket: overviewBucket,
        availableSamples: detail.sampleCount,
      })
      if (!wanted) {
        detailAbort?.abort()
        detailAbort = null
        showOverview(inst)
        return
      }
      if (coversRange(detailLoaded, visible)) return
      detailAbort?.abort()
      const controller = new AbortController()
      detailAbort = controller
      detail.load(wanted.startSample, wanted.endSample, controller.signal).then(
        (loaded) => {
          if (controller.signal.aborted || uplotRef.current !== inst) return
          const detailXs = new Float64Array(loaded.timestampsMs.length)
          for (let i = 0; i < detailXs.length; i++) {
            detailXs[i] = (loaded.timestampsMs[i] - signal.startTimestamp) / 1000
          }
          const merged = mergeDetail(xs, ys, detailXs, loaded.samples, loaded.gapIndices)
          detailLoaded = { startSample: loaded.startSample, endSample: loaded.endSample }
          container.dataset.trace = 'samples'
          inst.setData([merged.xs, merged.ys], false)
          // `setData` sin reescalar no repinta solo.
          inst.redraw()
        },
        () => {
          // Sin las muestras queda el resumen, que es lo que ya se veía. Una
          // descarga cancelada por otro zoom también termina acá.
        },
      )
    }

    const scheduleDetail = () => {
      if (!detail) return
      if (detailTimer != null) window.clearTimeout(detailTimer)
      detailTimer = window.setTimeout(updateDetail, DETAIL_DEBOUNCE_MS)
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
        // Rango FIJO derivado de la ganancia, no `auto`. Con `auto` uPlot elegía
        // el rango a partir de lo que hubiera en la ventana, así que la misma
        // onda se veía más alta o más baja según qué más entrara: un artefacto
        // grande aplastaba el trazado y ningún milímetro medía lo mismo que el
        // de al lado.
        //
        // El callback sigue acá aunque `applyVerticalRange` lo pise enseguida,
        // y no es redundante: uPlot lo llama una sola vez, durante la
        // construcción, con el área de trazado todavía en 0 px, y después no lo
        // vuelve a llamar por su cuenta. Sacarlo dejaba a la escala sin rango
        // hasta la primera corrección y el gráfico salía **en blanco**. Acá es
        // la red: un encuadre aproximado siempre es mejor que nada dibujado.
        y: {
          auto: false,
          range: (u, dataMin, dataMax) =>
            scaleRef.current.autoAmplitude
              ? [dataMin, dataMax]
              : verticalRange(scaleRef.current, plotHeightPx(u), (dataMin + dataMax) / 2),
        },
      },
      axes: [
        {
          stroke: tokens.fg,
          ticks: { stroke: tokens.grid, width: 1 },
          // La grilla de uPlot se apaga en los dos ejes: pone sus divisiones
          // donde le queden números redondos, y en un ECG la retícula ES el
          // instrumento de medida. La dibuja `drawPaperGrid` a escala real.
          grid: { show: false },
          // Hora de pared real, no tiempo transcurrido: es lo que el médico
          // necesita para cruzar un hallazgo con lo que el paciente estaba
          // haciendo. `v` es segundos desde el inicio del eje, que arranca en
          // `startTimestamp`.
          values: (_self, splits) =>
            splits.map((v) =>
              timeVerified
                ? formatWallClockShort(startTimestamp + v * 1000)
                : formatRelativeSeconds(v),
            ),
          size: 30,
        },
        {
          stroke: tokens.fg,
          ticks: { stroke: tokens.grid, width: 1 },
          grid: { show: false },
          values: (_self, splits) => splits.map((v) => `${v.toFixed(1)} mV`),
          size: 60,
        },
      ],
      series: [
        {
          label: 'Tiempo',
          value: (_self, v) =>
            v == null
              ? '—'
              : timeVerified
                ? formatWallClock(startTimestamp + v * 1000)
                : formatRelativeSeconds(v),
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
          // El orden importa: retícula, después bandas, y uPlot pinta la serie
          // arriba de todo. Al revés la grilla taparía el trazado.
          (u) => {
            drawPaperGrid(u, scaleRef.current, tokens.paperGrid)
          },
          (u) => {
            drawAnnotationBands(
              u,
              annotationDrawOrderRef.current,
              startTimestamp,
              tokens.alerts,
              selectedAnnotationIdRef.current,
              annotationLinksRef.current,
            )
          },
        ],
        setScale: [
          (u, scaleKey) => {
            if (scaleKey !== 'x') return
            const { min, max } = u.scales.x
            if (min == null || max == null) return
            // El `_init()` de uPlot autoescala al estudio entero y avisa en una
            // microtarea, antes del primer `ResizeObserver`. Si eso llegaba a
            // `preservedViewportRef`, la recreación por polling "restauraba" el
            // estudio entero: el zoom y el cursor se reiniciaban solos. El
            // encuadre real lo pone el `ResizeObserver`, y ese sí se notifica.
            if (isInitializingFrameRef.current) return
            const plotWidth = plotWidthPx(u)
            const isClinicalScale = matchesScale(scaleRef.current, plotWidth, max - min)
            const nextViewport = {
              startMs: startTimestamp + min * 1000,
              endMs: startTimestamp + max * 1000,
              millisecondsPerPixel: plotWidth > 0 ? ((max - min) * 1000) / plotWidth : undefined,
              isClinicalScale,
            }
            preservedViewportRef.current = nextViewport
            if (followLatest) {
              // No perseguimos al médico si se fue a revisar el pasado. Volver
              // al borde derecho (incluido un zoom ahí) reactiva el seguimiento.
              followsLatestRef.current = Math.abs(max - durationSec) < 0.05
            }
            const last = lastViewportRef.current
            if (last && last.min === min && last.max === max) return
            lastViewportRef.current = { min, max }
            scheduleDetail()
            setOverlayViewport((current) =>
              current?.startMs === nextViewport.startMs && current.endMs === nextViewport.endMs
                ? current
                : nextViewport,
            )
            onViewportChangeRef.current?.(nextViewport)
            // La línea de base sigue a la ventana visible, así que el rango
            // vertical se recalcula acá. El span no cambia —lo fija la ganancia—
            // pero el centro sí (y en amplitud automática, también el span).
            //
            // **Diferido a propósito.** Este hook corre adentro del `setScales`
            // de uPlot, que al terminar de avisar borra todas las escalas
            // pendientes: un `setScale('y')` hecho acá se perdía, y el eje Y
            // quedaba con el rango de la primera ventana aunque uno se
            // desplazara o hiciera zoom. La microtarea corre después de ese
            // borrado y antes de que el navegador pinte, así que no parpadea.
            queueMicrotask(() => {
              if (uplotRef.current !== u) return
              const { min: nextMin, max: nextMax } = u.scales.x
              if (nextMin == null || nextMax == null) return
              applyVerticalRange(u, scaleRef.current, signal, nextMin, nextMax, verticalOffsetRef)
            })
            // El zoom libre sirve para navegar, no para medir. Si el rango
            // visible dejó de corresponder a `paperSpeed`, el rótulo de la barra
            // tiene que decirlo: si no, alguien puede medir un QT sobre una
            // escala que no es la que el cartel afirma.
            onScaleMatchChangeRef.current?.(isClinicalScale)
          },
        ],
        setCursor: [
          (u) => {
            const left = u.cursor.left
            if (left == null || left < 0) return
            const timestamp = startTimestamp + u.posToVal(left, 'x') * 1000
            cursorTimestampRef.current = timestamp
            hasCursorAnchorRef.current = true
            onCursorChangeRef.current?.(timestamp)
          },
        ],
      },
    }

    const data: uPlot.AlignedData = [xs, ys]

    isInitializingFrameRef.current = true
    const u = new uPlot(opts, data, container)
    uplotRef.current = u

    // La primera instancia usa `initialViewport`; las recreaciones por polling
    // restauran el último rango observado. Si la señal es más corta que el
    // rango pedido, se recorta de forma segura.
    syncPlotArea(u)

    // El encuadre inicial queda pendiente a propósito: ver el comentario del
    // `ResizeObserver` de abajo.
    pendingInitialSpanRef.current = true

    const ro = new ResizeObserver((entries) => {
      const entry = entries[0]
      const inst = uplotRef.current
      if (!entry || !inst) return
      const w = Math.floor(entry.contentRect.width)
      if (w <= 0) return
      // Si venía a escala clínica, tiene que seguir a escala clínica después de
      // redimensionar: eso significa mostrar MÁS segundos, no los mismos
      // estirados. Es la diferencia observable entre una escala de verdad y un
      // eje que solo se veía bien con cierto ancho de ventana.
      const before = inst.scales.x
      const previousWidth = plotWidthPx(inst)
      const wasOnScale =
        before.min != null &&
        before.max != null &&
        matchesScale(scaleRef.current, previousWidth, before.max - before.min)

      inst.setSize({ width: w, height })
      if (pendingInitialSpanRef.current) {
        // **El encuadre inicial se hace acá y no antes.** Es el único momento
        // con la geometría definitiva: el constructor de uPlot corre antes de
        // que el navegador maquete el contenedor (`over` mide 0), y su hook
        // `ready` también. Y uPlot reserva el ancho de los ejes recién al medir
        // las etiquetas durante el primer dibujo, así que hasta ahí el área de
        // trazado mide de más. Medido en el navegador, encuadrar temprano daba
        // 7,2 mm/s en el peor caso y 24,0 mm/s en el mejor, con 25 declarados.
        //
        // La spec de `ResizeObserver` garantiza un callback inicial por cada
        // elemento observado, así que esto siempre corre.
        //
        // Un `jumpTo` que llegó antes (ver `pendingJumpRef`) encuadra a la
        // escala clínica y centra en su destino. No restaura el viewport
        // preservado: con el visor oculto, el autoescalado de uPlot y el propio
        // salto lo dejaron en el estudio entero.
        //
        // Siguiendo lo último que llega, el encuadre se pega al nuevo borde
        // derecho pero **con el mismo zoom**: volver a la escala clínica por
        // defecto le deshacía el zoom al médico en cada lote.
        const jumpTarget = pendingJumpRef.current
        const following = jumpTarget === null && followsLatestRef.current
        const preserved = preservedViewportRef.current
        const endMs = startTimestamp + durationSec * 1000
        const restore =
          jumpTarget !== null
            ? null
            : following && preserved
              ? { ...preserved, startMs: endMs - (preserved.endMs - preserved.startMs), endMs }
              : following
                ? null
                : preserved
        const anchorMs = following
          ? endMs
          : hasCursorAnchorRef.current
            ? cursorTimestampRef.current
            : undefined
        // Desde acá los cambios de escala son el encuadre de verdad y se avisan.
        isInitializingFrameRef.current = false
        if (
          applyInitialFraming(
            inst,
            scaleRef.current,
            signal,
            durationSec,
            restore,
            jumpTarget !== null ? undefined : initialWindowSeconds,
            anchorMs,
          )
        ) {
          pendingInitialSpanRef.current = false
          pendingJumpRef.current = null
          if (jumpTarget !== null) {
            const { min, max } = inst.scales.x
            if (min != null && max != null) {
              const targetSec = (jumpTarget - startTimestamp) / 1000
              const [newMin, newMax] = centerRangeAt(targetSec, min, max, 0, durationSec)
              inst.setScale('x', { min: newMin, max: newMax })
            }
          }
          // El cursor se queda donde lo dejó el médico mientras siga a la
          // vista; solo se lo lleva a lo último si quedó afuera o nunca se fijó.
          const visible = inst.scales.x
          const cursorSec = (cursorTimestampRef.current - startTimestamp) / 1000
          const cursorVisible =
            visible.min != null &&
            visible.max != null &&
            cursorSec >= visible.min &&
            cursorSec <= visible.max
          if (following && (!hasCursorAnchorRef.current || !cursorVisible)) {
            cursorTimestampRef.current = latestTimestampMs(signal)
          }
          setCursorAtTimestamp(inst, cursorTimestampRef.current, startTimestamp, durationSec)
        } else {
          // Sin ancho todavía: el próximo callback reintenta.
          isInitializingFrameRef.current = true
        }
      } else if (wasOnScale && before.min != null && before.max != null) {
        applyScaleSpanAtCursor(
          inst,
          scaleRef.current,
          durationSec,
          cursorTimestampRef.current,
          signal.startTimestamp,
          before.min,
          before.max,
        )
      }
      // El alto pudo cambiar, y con él cuántos mV entran.
      const after = inst.scales.x
      if (after.min != null && after.max != null) {
        applyVerticalRange(inst, scaleRef.current, signal, after.min, after.max, verticalOffsetRef)
      }
      syncPlotArea(inst)
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
    // selection nativo de uPlot.) Vertical solo con ganancia fija.
    let panStart: {
      px: number
      py: number
      min: number
      max: number
      offsetMv: number
      spanMv: number
    } | null = null
    let suppressClick = false
    let suppressClickTimeout: number | null = null
    const handlePointerDown = (e: PointerEvent) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return
      const inst = uplotRef.current
      if (!inst) return
      const { min, max } = inst.scales.x
      if (min == null || max == null) return
      const { min: yMin, max: yMax } = inst.scales.y
      panStart = {
        px: e.clientX,
        py: e.clientY,
        min,
        max,
        offsetMv: verticalOffsetRef.current,
        spanMv: yMin != null && yMax != null ? yMax - yMin : 0,
      }
      container.setPointerCapture(e.pointerId)
      container.style.cursor = 'grabbing'
    }
    const handlePointerMove = (e: PointerEvent) => {
      if (!panStart) return
      const inst = uplotRef.current
      if (!inst) return
      const rect = inst.over.getBoundingClientRect()
      const dxPx = e.clientX - panStart.px
      const dyPx = e.clientY - panStart.py
      if (Math.hypot(dxPx, dyPx) > 3) suppressClick = true
      if (!scaleRef.current.autoAmplitude && rect.height > 0 && panStart.spanMv > 0) {
        // Se arrastra el papel: bajar el puntero baja la traza y deja ver lo
        // que estaba por encima.
        verticalOffsetRef.current = panStart.offsetMv + (dyPx / rect.height) * panStart.spanMv
        const { min, max } = inst.scales.x
        if (min != null && max != null) {
          applyVerticalRange(inst, scaleRef.current, signal, min, max, verticalOffsetRef)
        }
      }
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
      const drawOrder = annotationDrawOrderRef.current
      if (!inst || drawOrder.length === 0) return
      const rect = inst.over.getBoundingClientRect()
      const x = e.clientX - rect.left
      if (x < 0 || x > rect.width) return
      const clickedMs = startTimestamp + inst.posToVal(x, 'x') * 1000
      const { min, max } = inst.scales.x
      const toleranceMs =
        min != null && max != null ? ((max - min) * 1000 * Math.max(8, 1)) / rect.width : 0
      const hit = [...drawOrder]
        .reverse()
        .find(
          (annotation) =>
            clickedMs >= annotation.startMs - toleranceMs &&
            clickedMs <= Math.max(annotation.endMs, annotation.startMs) + toleranceMs,
        )
      if (hit) onAnnotationSelectRef.current?.(hit)
    }
    container.addEventListener('click', handleClick)

    // Teclado: flechas mueven el viewport en 10% del ancho (o del alto) actual.
    const handleKeyDown = (e: KeyboardEvent) => {
      const inst = uplotRef.current
      if (!inst) return
      if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        if (scaleRef.current.autoAmplitude) return
        const { min: yMin, max: yMax } = inst.scales.y
        const { min, max } = inst.scales.x
        if (yMin == null || yMax == null || min == null || max == null) return
        const dir = e.key === 'ArrowUp' ? 1 : -1
        verticalOffsetRef.current += (yMax - yMin) * 0.1 * dir
        applyVerticalRange(inst, scaleRef.current, signal, min, max, verticalOffsetRef)
        e.preventDefault()
        return
      }
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
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
      if (detailTimer != null) window.clearTimeout(detailTimer)
      detailAbort?.abort()
      ro.disconnect()
      uplotRef.current?.destroy()
      uplotRef.current = null
      lastViewportRef.current = null
    }
  }, [signal, height, xs, ys, durationSec, initialWindowSeconds, followLatest])

  // Cambiar ganancia o barrido NO recrea la instancia: se actualiza la ref que
  // leen `scales.y.range` y la grilla, se reencuadra el eje de tiempo a la
  // escala nueva y se repinta. Recrearla costaría el canvas entero y, peor,
  // perdería el viewport justo cuando el médico está mirando algo.
  //
  // Solo reencuadra cuando la escala **cambió de verdad**. Sin esa guarda el
  // efecto también corría al montar y al recrearse la instancia por polling, y
  // ahí pisaba el viewport que el efecto de creación acababa de restaurar: el
  // médico perdía el tramo que estaba mirando cada vez que llegaba un lote nuevo.
  const appliedScaleRef = useRef<PaperScale | null>(null)
  // El encuadre inicial quedó pendiente porque al crear la instancia el área de
  // trazado todavía medía 0. Lo resuelve el primer `ResizeObserver`, que corre
  // ya con layout.
  const pendingInitialSpanRef = useRef(false)
  // Un `jumpTo` pedido mientras el encuadre inicial está pendiente. Pasa con el
  // visor oculto (la pestaña Señal con `forceMount`): el polling lo recrea
  // dentro de un contenedor de ancho 0 y el encuadre espera al primer layout
  // con ancho. Saltar en ese momento centraba sobre el rango automático de
  // uPlot, y al mostrarse la pestaña el encuadre pendiente lo pisaba con lo
  // último grabado o con el estudio entero: "Ver en el ECG" abría otro lado.
  const pendingJumpRef = useRef<number | null>(null)
  // La señal, para el efecto de escala: no puede entrar como dependencia sin
  // reencuadrar en cada lote nuevo que llega por polling.
  const signalRef = useRef(signal)
  signalRef.current = signal
  useEffect(() => {
    scaleRef.current = scale
    const previous = appliedScaleRef.current
    appliedScaleRef.current = scale
    if (previous === null || previous === scale) return
    // Otra ganancia es otro encuadre: el desplazamiento que servía para la
    // anterior ya no apunta a lo mismo.
    verticalOffsetRef.current = 0
    const inst = uplotRef.current
    if (!inst) return
    const { min, max } = inst.scales.x
    applyScaleSpanAtCursor(
      inst,
      scale,
      durationSec,
      cursorTimestampRef.current,
      signalRef.current.startTimestamp,
      min ?? 0,
      max ?? durationSec,
    )
    const { min: nextMin, max: nextMax } = inst.scales.x
    if (nextMin != null && nextMax != null) {
      applyVerticalRange(inst, scale, signalRef.current, nextMin, nextMax, verticalOffsetRef)
    }
    inst.redraw()
  }, [scale, durationSec])

  // API imperativa — convierte timestamps absolutos a segundos desde el inicio.
  useImperativeHandle(
    ref,
    () => ({
      jumpTo(timestampMs: number) {
        const inst = uplotRef.current
        if (!inst) return
        if (pendingInitialSpanRef.current) {
          // Saltar es dejar de seguir lo último que llega: si no, el encuadre
          // pendiente volvería al borde derecho.
          pendingJumpRef.current = timestampMs
          followsLatestRef.current = false
          return
        }
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
      restoreViewport(viewport: ECGViewportChange) {
        const inst = uplotRef.current
        if (!inst) return
        applyInitialFraming(
          inst,
          scaleRef.current,
          signal,
          durationSec,
          viewport,
          undefined,
          cursorTimestampRef.current,
        )
      },
      resetZoom() {
        const inst = uplotRef.current
        if (!inst) return
        inst.setScale('x', { min: 0, max: durationSec })
      },
      resetScale() {
        const inst = uplotRef.current
        if (!inst) return
        verticalOffsetRef.current = 0
        const { min, max } = inst.scales.x
        applyScaleSpanAtCursor(
          inst,
          scaleRef.current,
          durationSec,
          cursorTimestampRef.current,
          signal.startTimestamp,
          min ?? 0,
          max ?? durationSec,
        )
      },
      setCursor(timestampMs: number) {
        const inst = uplotRef.current
        if (!inst) return
        cursorTimestampRef.current = timestampMs
        hasCursorAnchorRef.current = true
        setCursorAtTimestamp(inst, timestampMs, signal.startTimestamp, durationSec)
      },
    }),
    [signal, durationSec],
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
            const Icon = annotationChartIcon(annotation, annotationLinks)
            const severity = ANNOTATION_SEVERITY[annotation.severity]
            const isSelected = annotation.id === selectedAnnotationId
            const isHighlighted = isAnnotationHighlighted(
              annotation,
              selectedAnnotationId,
              annotationLinks,
            )
            const label = annotationChartLabel(annotation, annotationLinks)
            return (
              <button
                key={annotation.id}
                type="button"
                data-annotation-label-id={annotation.id}
                aria-label={`${label}, severidad ${severity.label.toLowerCase()}`}
                aria-pressed={isSelected}
                title={
                  annotation.description
                    ? `${label} · ${annotation.description}`
                    : `${label} · ${severity.label}`
                }
                onClick={() => onAnnotationSelect?.(annotation)}
                className={cn(
                  'pointer-events-auto absolute flex h-6 max-w-full cursor-pointer items-center gap-1 rounded-full border px-2 text-xs font-medium shadow-sm backdrop-blur-sm',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
                  isHighlighted && 'ring-2 ring-primary/50',
                )}
                style={{
                  left: leftPx,
                  top: 4 + lane * 28,
                  zIndex: isHighlighted ? 20 : 10,
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
      {toolbar ? (
        // Fuera del contenedor de uPlot a propósito: un click acá no tiene que
        // arrancar un pan ni seleccionar el aviso que esté debajo.
        <div
          className="absolute z-10"
          style={{ top: (plotArea?.top ?? 0) + 8, right: 8 }}
          data-testid="ecg-viewer-toolbar"
        >
          {toolbar}
        </div>
      ) : null}
    </div>
  )
})

/**
 * Eje X en segundos desde el inicio del estudio, derivado de la hora real de
 * cada punto.
 *
 * Antes se repartía `durationMs` uniformemente entre los puntos. Eso es correcto
 * solo si la grabación no tiene cortes: en cuanto el chaleco deja de grabar un
 * rato, el buffer de muestras pega los dos bordes y el eje uniforme corre la
 * hora de todo lo que viene después del hueco.
 */
function formatRelativeSeconds(value: number): string {
  const seconds = Math.max(0, Math.floor(value))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const rest = seconds % 60
  return `+${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${rest.toString().padStart(2, '0')}`
}

function buildXAxis(signal: ECGSignal): Float64Array {
  const n = signal.samples.length
  const xs = new Float64Array(n)
  if (signal.timestampsMs.length === n) {
    for (let i = 0; i < n; i++) xs[i] = (signal.timestampsMs[i] - signal.startTimestamp) / 1000
    return xs
  }
  const dt = n > 0 ? signal.durationMs / 1000 / n : 0
  for (let i = 0; i < n; i++) xs[i] = i * dt
  return xs
}

/**
 * Serie Y con un corte en cada hueco.
 *
 * uPlot corta la traza en un `null` cuando `spanGaps` es falso, que ya está
 * puesto. Sin este corte, los dos bordes de un hueco quedarían unidos por una
 * recta larga: una línea que el chaleco nunca midió, dibujada con la misma
 * tinta que la señal real.
 *
 * Camino rápido cuando no hay huecos: se pasa el `Float32Array` tal cual y no se
 * copia nada, que es lo que hace viable dibujar cientos de miles de puntos.
 */
function buildYSeries(signal: ECGSignal): Float32Array | (number | null)[] {
  if (signal.gapIndices.length === 0) return signal.samples
  const gaps = new Set(signal.gapIndices)
  const ys: (number | null)[] = new Array(signal.samples.length)
  for (let i = 0; i < signal.samples.length; i++) {
    ys[i] = gaps.has(i) ? null : signal.samples[i]
  }
  return ys
}

/**
 * Clampea el par `[min, max]` dentro de `[boundsMin, boundsMax]` preservando
 * el span — si el rango pedido es más ancho que las cotas, devuelve las cotas
 * completas.
 */
/**
 * Reencuadra el eje de tiempo a la escala vigente, anclado en `from`.
 *
 * Es la operación inversa a la de antes: la escala fija cuántos segundos entran
 * en el ancho disponible, y el viewport se ajusta a eso. Se usa al cambiar la
 * ganancia o el barrido, al redimensionar y al volver desde el zoom libre.
 */
/**
 * Ancho y alto del área de trazado, en px CSS.
 *
 * **Sale de `bbox` y no de `over`, y la diferencia importa.** uPlot fija `bbox`
 * dentro del constructor, mientras que `over` es un elemento que el navegador
 * todavía no maquetó: durante la construcción mide 0. Y `scales.y.range` corre
 * justo ahí, así que midiendo `over` el rango vertical caía en su fallback de
 * ±1 mV y se quedaba pegado (uPlot no vuelve a llamar `range` por su cuenta).
 * Medido en el navegador: 22,9 mm/mV efectivos con la escala declarada en 10, y
 * 7,2 mm/s con el barrido declarado en 25.
 *
 * `bbox` viene en píxeles de dispositivo, de ahí la división por `pxRatio`. El
 * fallback a `over` cubre los dobles de test, que no simulan el canvas.
 */
function plotWidthPx(inst: uPlot): number {
  const fromBbox = inst.bbox?.width
  if (fromBbox) return fromBbox / (uPlot.pxRatio || 1)
  return inst.over?.clientWidth ?? 0
}

function plotHeightPx(inst: uPlot): number {
  const fromBbox = inst.bbox?.height
  if (fromBbox) return fromBbox / (uPlot.pxRatio || 1)
  return inst.over?.clientHeight ?? 0
}

/**
 * Fija el rango vertical: span de la ganancia, centro en la línea de base.
 *
 * El span no depende de los datos —eso es lo que hace comparable un milímetro
 * con el de al lado— pero el centro sí: el front-end es DC-acoplado y el
 * potencial de media celda de los electrodos puede correr el trazado decenas de
 * mV sin que sea una falla (`INTEGRACION.md` §3.2, y es por eso que `raw_uV` es
 * int32 y no int16). Un rango anclado en 0 dejaría a esos pacientes con la
 * pantalla en blanco.
 *
 * Sobre ese centro se suma el desplazamiento vertical que eligió el médico,
 * acotado para que la traza nunca quede entera fuera de pantalla. El valor
 * acotado se escribe de vuelta en `offsetMv`: si no, seguir arrastrando más
 * allá del tope dejaría un desplazamiento fantasma que habría que deshacer.
 */
function applyVerticalRange(
  inst: uPlot,
  scale: PaperScale,
  signal: ECGSignal,
  minSec: number,
  maxSec: number,
  offsetMv: { current: number },
): void {
  const heightPx = plotHeightPx(inst)
  if (heightPx <= 0) return
  const [from, to] = sampleRangeForSeconds(signal, minSec, maxSec)
  // Una ventana sin muestras (un hueco, o antes de que llegue el lote) no dice
  // dónde está la señal: se conserva el rango actual. Centrar en 0 mV dejaba
  // fuera de pantalla a cualquier trazado con offset.
  const extent = autoVerticalRange(signal.samples, from, to)
  if (!extent) return
  // En modo automático el rango lo pone la señal visible: es lo que permite ver
  // un trazado que se sale de cualquier ganancia fija.
  let min: number
  let max: number
  if (scale.autoAmplitude) {
    ;[min, max] = extent
  } else {
    const baseline = baselineMv(signal.samples, from, to)
    const center = pannedCenterMv(baseline, offsetMv.current, extent)
    offsetMv.current = center - baseline
    ;[min, max] = verticalRange(scale, heightPx, center)
  }
  const current = inst.scales.y
  // Sin la comparación esto se llamaría a sí mismo: `setScale` dispara el hook
  // que lo invocó. El epsilon absorbe el redondeo del centro.
  if (
    current.min != null &&
    current.max != null &&
    Math.abs(current.min - min) < 1e-9 &&
    Math.abs(current.max - max) < 1e-9
  )
    return
  inst.setScale('y', { min, max })
}

/**
 * Encuadre inicial: restaura el viewport preservado, o encuadra a la escala.
 *
 * `preservedViewportRef` lleva el rango que el médico estaba mirando y tiene que
 * sobrevivir a que llegue un lote nuevo por polling, que recrea la instancia.
 */
function applyInitialFraming(
  inst: uPlot,
  scale: PaperScale,
  signal: ECGSignal,
  durationSec: number,
  restore: ECGViewportChange | null,
  initialWindowSeconds: number | undefined,
  anchorTimestampMs: number | undefined,
): boolean {
  if (restore) {
    const startSec = Math.max(0, (restore.startMs - signal.startTimestamp) / 1000)
    const endSec = Math.min(durationSec, (restore.endMs - signal.startTimestamp) / 1000)
    const hasAnchor = anchorTimestampMs !== undefined
    const anchorSec = Math.min(
      Math.max(
        hasAnchor ? (anchorTimestampMs - signal.startTimestamp) / 1000 : (startSec + endSec) / 2,
        0,
      ),
      durationSec,
    )
    const anchorRatio = hasAnchor ? viewportAnchorRatio(startSec, endSec, anchorSec) : 0.5
    if (restore.isClinicalScale) {
      // La escala clínica manda: al ir a una pantalla más ancha se muestran
      // más segundos sin alterar los mm/s. El cursor es el ancla para que no
      // salte de lugar al pasar entre contenedores de distinto ancho.
      return hasAnchor
        ? applyScaleSpanAtAnchor(inst, scale, anchorSec, anchorRatio, durationSec)
        : applyScaleSpan(inst, scale, startSec, durationSec)
    }
    if (restore.millisecondsPerPixel && restore.millisecondsPerPixel > 0) {
      // El zoom libre no declara una escala clínica. En ese modo se conserva
      // la densidad temporal y la posición relativa del cursor, no el span
      // absoluto, que cambiaría el zoom al pasar de una vista a la otra.
      const spanSec = (restore.millisecondsPerPixel * plotWidthPx(inst)) / 1000
      const [min, max] = rangeAtAnchor(anchorSec, anchorRatio, spanSec, 0, durationSec)
      inst.setScale('x', { min, max })
      return true
    }
    inst.setScale(
      'x',
      endSec > startSec ? { min: startSec, max: endSec } : { min: 0, max: durationSec },
    )
    return true
  }
  if (initialWindowSeconds !== undefined) {
    const initialSpan = Math.min(durationSec, initialWindowSeconds)
    const [min, max] = clampRange(durationSec - initialSpan, durationSec, 0, durationSec)
    inst.setScale('x', { min, max })
    return true
  }
  return applyScaleSpan(inst, scale, durationSec, durationSec)
}

function applyScaleSpanAtCursor(
  inst: uPlot,
  scale: PaperScale,
  durationSec: number,
  cursorTimestampMs: number,
  startTimestamp: number,
  currentMin: number,
  currentMax: number,
): boolean {
  const cursorSec = Math.min(Math.max((cursorTimestampMs - startTimestamp) / 1000, 0), durationSec)
  return applyScaleSpanAtAnchor(
    inst,
    scale,
    cursorSec,
    viewportAnchorRatio(currentMin, currentMax, cursorSec),
    durationSec,
  )
}

function applyScaleSpanAtAnchor(
  inst: uPlot,
  scale: PaperScale,
  anchorSec: number,
  anchorRatio: number,
  durationSec: number,
): boolean {
  const span = visibleSeconds(scale, plotWidthPx(inst))
  if (span <= 0) return false
  const [min, max] = rangeAtAnchor(anchorSec, anchorRatio, span, 0, durationSec)
  inst.setScale('x', { min, max })
  return true
}

function viewportAnchorRatio(min: number, max: number, anchor: number): number {
  if (max <= min) return 0.5
  return Math.min(Math.max((anchor - min) / (max - min), 0), 1)
}

function rangeAtAnchor(
  anchor: number,
  anchorRatio: number,
  span: number,
  boundsMin: number,
  boundsMax: number,
): [number, number] {
  const min = anchor - span * anchorRatio
  return clampRange(min, min + span, boundsMin, boundsMax)
}

function setCursorAtTimestamp(
  inst: uPlot,
  timestampMs: number,
  startTimestamp: number,
  durationSec: number,
): void {
  const target = Math.min(Math.max((timestampMs - startTimestamp) / 1000, 0), durationSec)
  const left = inst.valToPos(target, 'x')
  const setCursor = (
    inst as uPlot & { setCursor?: (cursor: { left: number; top: number }) => void }
  ).setCursor
  if (Number.isFinite(left) && setCursor) setCursor.call(inst, { left, top: 0 })
}

function applyScaleSpan(
  inst: uPlot,
  scale: PaperScale,
  from: number,
  durationSec: number,
): boolean {
  const span = visibleSeconds(scale, plotWidthPx(inst))
  // Ancho 0: el navegador todavía no maquetó el área de trazado. No se inventa
  // un encuadre; el llamador reintenta cuando haya medida.
  if (span <= 0) return false
  // `clampRange` ya devuelve el estudio entero cuando el span pedido lo supera,
  // que es el caso de un registro más corto que una tira de papel.
  const [min, max] = clampRange(from, from + span, 0, durationSec)
  inst.setScale('x', { min, max })
  return true
}

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
