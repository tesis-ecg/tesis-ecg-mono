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
  /** Muestras del canal único, en mV. */
  samples: Float32Array
  /** Timestamp UNIX en ms del primer sample (`samples[0]`). */
  startTimestamp: number
}

export interface ECGViewerProps {
  signal: ECGSignal
  /** Alto del viewer en píxeles. Default 400. */
  height?: number
  /**
   * Velocidad de papel clínica en mm/s. Default 25 (estándar para diagnóstico
   * de adultos). Usado para calcular el aspect ratio horizontal.
   */
  paperSpeed?: number
  /**
   * Amplitud clínica en mm/mV. Default 10 (estándar). Usado para calcular el
   * aspect ratio vertical.
   */
  amplitude?: number
  /**
   * Ancho del viewport inicial en segundos. Default 10 s — convención clínica
   * de tira de papel (25 mm/s × 25 cm ≈ 10 s). El viewer arranca mostrando los
   * últimos `initialWindowSec` segundos de la señal.
   *
   * Si `initialViewport` también está provisto, este último tiene precedencia.
   */
  initialWindowSec?: number
  /**
   * Viewport inicial absoluto (timestamps en ms epoch). Si se pasa, sobreescribe
   * a `initialWindowSec`. Útil para sincronizar dos instancias del viewer (por
   * ejemplo cuando se abre el viewer en una modal mostrando lo mismo).
   */
  initialViewport?: ECGViewportChange
  /**
   * Callback opcional disparado cuando cambia el viewport (zoom, pan o llamada
   * a la API imperativa). Útil para sincronizar mini-mapa, panel lateral, etc.
   */
  onViewportChange?: (viewport: ECGViewportChange) => void
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
  /** Vuelve al rango completo del estudio. */
  resetZoom: () => void
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
}
