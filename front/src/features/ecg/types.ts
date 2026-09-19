export type ECGAnnotationSeverity = 'low' | 'medium' | 'high' | 'critical'

export type ECGAnnotationCategory = 'signal_quality' | 'clinical' | 'patient_marker' | 'technical'

/** Un tramo contiguo de grabación, con su hora de pared real. */
export interface ECGTimelineSegment {
  ordinal: number
  /** Dónde arranca dentro del buffer de muestras (que no deja huecos). */
  startSampleIndex: number
  sampleCount: number
  /** Hora real de la primera y la última muestra del tramo. */
  startEpochMs: number
  endEpochMs: number
  bootId: number | null
  /**
   * `ntp` es una hora sincronizada por el puente WiFi. `server_receive` es el
   * camino viejo, derivado de la hora de recepción del backend, así que trae
   * adentro la latencia del pedido. `none` es el puente sin sincronizar.
   */
  anchorSource: 'ntp' | 'none' | 'server_receive'
  anchorUncertaintyMs: number | null
}

export interface ECGAnnotation {
  id: string
  kind: string
  category: ECGAnnotationCategory
  severity: ECGAnnotationSeverity
  /** Timestamp UNIX absoluto del inicio del aviso. */
  startMs: number
  /** Timestamp UNIX absoluto del final (igual a startMs para eventos puntuales). */
  endMs: number
  confidenceScore: number | null
  /**
   * Para la respuesta del paciente a un aviso: el id del hallazgo que contesta.
   * El backend ancla la marca dentro de esa banda, así que las dos se leen como
   * una sola cosa; este campo es lo que además permite etiquetarla ("Respuesta:
   * Taquicardia") y resaltar las dos juntas al seleccionar cualquiera.
   */
  linkedAnnotationId: string | null
  /** Texto corto del aviso — hoy, los síntomas que informó el paciente. */
  description: string | null
}

/**
 * Señal ECG de un único canal. Los samples se guardan en `Float32Array` por
 * performance — pasarlos como `number[]` multiplicaría el uso de memoria y haría
 * imposible renderizar 900k puntos a 60 fps en uPlot.
 *
 * El timestamp absoluto de cualquier muestra `i` se calcula como
 * `startTimestamp + (i / sampleRate) * 1000` (ms epoch).
 */
export interface ECGSignal {
  /** Frecuencia de muestreo en Hz (típicamente 250 Hz para Holter clínico). */
  sampleRate: number
  /** Duración del eje visible; excluye intervalos en los que no hubo muestras. */
  durationMs: number
  /** Muestras del canal único, en mV. */
  samples: Float32Array
  /** Timestamp UNIX en ms del primer sample (`samples[0]`). */
  startTimestamp: number
  /**
   * Hora de pared de cada muestra, en ms epoch, alineada con `samples`.
   *
   * Es lo que permite que el eje sea hora real y no tiempo transcurrido. Existe
   * porque las dos cosas dejan de coincidir en cuanto el chaleco deja de grabar
   * un rato: el buffer de muestras pega los bordes del hueco y el índice de
   * muestra se corre respecto de la hora para todo lo que viene después.
   */
  timestampsMs: Float64Array
  /**
   * Índices de `samples` donde arranca cada tramo posterior al primero. El visor
   * corta la traza ahí para que un hueco se vea como un hueco y no como una
   * línea que une dos instantes que nunca fueron contiguos.
   */
  gapIndices: number[]
  /** Tramos contiguos de grabación con su hora real. Vacío en estudios legacy. */
  timeline: ECGTimelineSegment[]
  /** Hallazgos y problemas de calidad alineados al mismo eje temporal. */
  annotations: ECGAnnotation[]
  /** Metadatos de adquisición necesarios para el informe clínico. */
  metadata?: {
    formatVersion: number
    encoding: string
    sampleCount: number
    isSimulated: boolean
    /** Tamaño del bucket de la vista descargada; null si la señal es cruda. */
    overviewSamplesPerBucket: number | null
  }
}

export interface ECGViewerProps {
  signal: ECGSignal
  /** Alto del viewer en píxeles. Default 400. */
  height?: number
  /**
   * Velocidad de barrido en mm/s. Default 25, el estándar de diagnóstico para
   * adultos. **Decide cuántos segundos entran en el ancho disponible**, así que
   * agrandar la ventana muestra más señal en vez de estirar la misma.
   */
  paperSpeed?: number
  /**
   * Ganancia en mm/mV. Default 10, el estándar. Fija el rango vertical: la misma
   * onda mide lo mismo sin importar qué más haya en la ventana.
   */
  amplitude?: number
  /**
   * Viewport inicial absoluto (timestamps en ms epoch). Si se pasa, sobreescribe
   * a `initialWindowSec`. Útil para sincronizar dos instancias del viewer (por
   * ejemplo cuando se abre el viewer en una modal mostrando lo mismo).
   */
  initialViewport?: ECGViewportChange
  /** Ventana inicial alternativa para vistas de dominio como el estudio. */
  initialWindowSeconds?: number
  /** Mantiene el extremo derecho en datos nuevos mientras el médico siga ahí. */
  followLatest?: boolean
  /** Cursor inicial absoluto. Por defecto se ubica en la última muestra. */
  initialCursorMs?: number
  /**
   * Callback opcional disparado cuando cambia el viewport (zoom, pan o llamada
   * a la API imperativa). Útil para sincronizar mini-mapa, panel lateral, etc.
   */
  onViewportChange?: (viewport: ECGViewportChange) => void
  /** Timestamp bajo la cruz del cursor; permite sincronizar dos viewers. */
  onCursorChange?: (cursorMs: number) => void
  /**
   * Avisa si el rango visible todavía corresponde a `paperSpeed`.
   *
   * El zoom libre (Ctrl + rueda, el mini-mapa) sirve para navegar, no para
   * medir. Quien dibuje el rótulo de la escala necesita saber cuándo dejó de ser
   * cierto: un cartel que diga "25 mm/s" sobre un trazado que no lo está es peor
   * que no tener cartel.
   */
  onScaleMatchChange?: (matchesScale: boolean) => void
  /** Aviso resaltado en el gráfico y el panel de hallazgos. */
  selectedAnnotationId?: string | null
  /** Selección de una banda directamente sobre el canvas. */
  onAnnotationSelect?: (annotation: ECGAnnotation) => void
}

/**
 * Handle imperativo del viewer. Lo consumen los controles externos (mini-mapa,
 * botones de zoom, panel de hallazgos en el futuro) para mover el viewport
 * sin acoplarse a la implementación de uPlot.
 *
 * Los timestamps son **absolutos** (ms epoch). El handle traduce internamente
 * a segundos desde `signal.startTimestamp`.
 */
export interface ECGViewerHandle {
  /** Centra la vista en `timestampMs` manteniendo el zoom actual. */
  jumpTo: (timestampMs: number) => void
  /** Ajusta el viewport para mostrar exactamente el rango `[startMs, endMs]`. */
  zoomToRange: (startMs: number, endMs: number) => void
  /** Restaura viewport, densidad y modo de escala compartidos entre viewers. */
  restoreViewport: (viewport: ECGViewportChange) => void
  /** Vuelve al rango completo del estudio. */
  resetZoom: () => void
  /** Vuelve a la escala clínica declarada, conservando dónde está mirando. */
  resetScale: () => void
  /** Ubica la cruz del cursor sin modificar el viewport. */
  setCursor: (timestampMs: number) => void
}

/**
 * Notificación de cambios de viewport. Disparada por zoom/pan internos del
 * viewer y por las llamadas a la API imperativa. Permite mantener sincronizado
 * el mini-mapa y eventualmente highlight de hallazgos visibles.
 */
export type ECGViewportChange = {
  /** Timestamp UNIX en ms del primer sample visible. */
  startMs: number
  /** Timestamp UNIX en ms del último sample visible. */
  endMs: number
  /**
   * Densidad horizontal efectiva del viewport. Se comparte al pasar entre el
   * visor embebido y la pantalla completa para que un zoom libre no cambie por
   * el solo hecho de cambiar el ancho disponible.
   */
  millisecondsPerPixel?: number
  /** Si el rango representa la escala clínica declarada (mm/s). */
  isClinicalScale?: boolean
}
