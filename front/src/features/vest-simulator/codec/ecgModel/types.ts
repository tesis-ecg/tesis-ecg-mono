/**
 * Configuración del modelo de ECG: el paciente, el chaleco y los episodios.
 *
 * Los números por defecto salen de lo medido sobre la placa, por el canal 2, en
 * `../Holter-ECG-System/capturas/README` (T/R, P/R, FC, ruido de red y
 * compresión). No de la literatura: el simulador tiene que parecerse a **este**
 * equipo, que graba la entrada DC-acoplada y sin filtrar.
 */

/** Tipo de electrodo. El seco mete más ruido y más deriva que el de gel. */
export type ElectrodeKind = 'dry' | 'gel'

/**
 * Cuánta interferencia de red le llega a la entrada. Es la variable que más
 * mueve la compresión y, con ella, la autonomía sin conexión.
 */
export type MainsEnvironment = 'clean' | 'home' | 'router'

export interface SignalProfile {
  seed: number
  /** FC de reposo, antes del ritmo circadiano. */
  baseBpm: number
  /** Amplitud de la onda R en µV. */
  rAmplitudeUV: number
  electrode: ElectrodeKind
  environment: MainsEnvironment
  /** Respiraciones por minuto: modulan el RR (arritmia sinusal) y la línea de base. */
  respirationPerMin: number
  /**
   * Offset de continua en µV. El front-end es DC-acoplado: con el chaleco
   * puesto la entrada se para en ~40-50 mV, y el visor lo saca con su
   * pasa-altos. Lo que se archiva es el crudo, con el offset adentro.
   */
  dcOffsetUV: number
  /** Si la FC sigue la hora del día de la muestra: más baja de noche. */
  circadian: boolean
}

export type EpisodeKind =
  | 'tachycardia'
  | 'bradycardia'
  | 'pause'
  | 'afib'
  | 'pvc'
  | 'pvc_bigeminy'
  | 'pac'
  | 'lead_off'
  | 'rld_off'
  | 'motion'
  | 'saturation'
  | 'symptom'

/**
 * Un episodio agendado a mano dentro de una corrida.
 *
 * `batch` es el número de lote **de la corrida** (1..N) y `startSec` es
 * relativo al inicio de ese lote. Un episodio que se pasa del final del lote
 * continúa en el siguiente: el generador lo arrastra en su estado.
 */
export interface Episode {
  id: string
  kind: EpisodeKind
  batch: number
  startSec: number
  durationSec: number
  /** El parámetro del tipo; ver `EPISODE_META[kind].valueLabel`. */
  value: number
}

/** Un episodio ya ubicado dentro del lote que se está generando. */
export interface ResolvedEpisode {
  kind: EpisodeKind
  startSec: number
  endSec: number
  value: number
}

export interface EpisodeMeta {
  label: string
  /** Qué significa `value`, o `null` si el tipo no lo usa. */
  valueLabel: string | null
  defaultValue: number
  defaultDurationSec: number
  /** Puntual: la duración no se usa. */
  instant: boolean
}

export const EPISODE_META: Record<EpisodeKind, EpisodeMeta> = {
  tachycardia: {
    label: 'Taquicardia sinusal',
    valueLabel: 'FC (lpm)',
    defaultValue: 140,
    defaultDurationSec: 60,
    instant: false,
  },
  bradycardia: {
    label: 'Bradicardia sinusal',
    valueLabel: 'FC (lpm)',
    defaultValue: 42,
    defaultDurationSec: 60,
    instant: false,
  },
  pause: {
    label: 'Pausa sinusal',
    valueLabel: 'Duración (ms)',
    defaultValue: 3000,
    defaultDurationSec: 0,
    instant: true,
  },
  afib: {
    label: 'Fibrilación auricular',
    valueLabel: 'Respuesta ventricular (lpm)',
    defaultValue: 110,
    defaultDurationSec: 120,
    instant: false,
  },
  pvc: {
    label: 'Extrasístoles ventriculares',
    valueLabel: 'PVC por minuto',
    defaultValue: 6,
    defaultDurationSec: 120,
    instant: false,
  },
  pvc_bigeminy: {
    label: 'Bigeminismo ventricular',
    valueLabel: null,
    defaultValue: 0,
    defaultDurationSec: 60,
    instant: false,
  },
  pac: {
    label: 'Extrasístoles auriculares',
    valueLabel: 'PAC por minuto',
    defaultValue: 6,
    defaultDurationSec: 120,
    instant: false,
  },
  lead_off: {
    label: 'Electrodo suelto',
    valueLabel: null,
    defaultValue: 0,
    defaultDurationSec: 150,
    instant: false,
  },
  rld_off: {
    label: 'Tierra suelta (RLD)',
    valueLabel: null,
    defaultValue: 0,
    defaultDurationSec: 30,
    instant: false,
  },
  motion: {
    label: 'Artefacto de movimiento',
    valueLabel: 'Amplitud (µV)',
    defaultValue: 2500,
    defaultDurationSec: 20,
    instant: false,
  },
  saturation: {
    label: 'Saturación del ADC',
    valueLabel: null,
    defaultValue: 0,
    defaultDurationSec: 2,
    instant: false,
  },
  symptom: {
    label: 'Botón de síntoma',
    valueLabel: null,
    defaultValue: 0,
    defaultDurationSec: 0,
    instant: true,
  },
}

export const DEFAULT_SIGNAL_PROFILE: SignalProfile = {
  seed: 1,
  baseBpm: 68,
  rAmplitudeUV: 1500,
  electrode: 'dry',
  environment: 'clean',
  respirationPerMin: 15,
  dcOffsetUV: 42_000,
  circadian: true,
}
