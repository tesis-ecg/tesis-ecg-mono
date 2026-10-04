/**
 * Generador de ECG de una derivación, con estado entre lotes.
 *
 * Lo que tiene que parecerse al equipo real, en orden de importancia:
 *
 * 1. **El trazado en el visor**: latidos tipo derivación II, con P, QRS y T
 *    proporcionados, RR que respira y cambia con la hora del día, y la línea de
 *    base moviéndose como se mueve sobre la piel.
 * 2. **Lo que mide el backend**: FC, HRV, pausas y ST salen de esta señal, así
 *    que las arritmias tienen que estar en el trazado y no solo en una etiqueta.
 * 3. **La compresión**: el volumen que sube el simulador depende de cuánto
 *    comprima el Rice. El ruido está calibrado con el residuo de orden 2 de las
 *    capturas reales (`capturas/`): mediana de |Δ²x| ~4 µV con gel limpio,
 *    ~15 µV seco limpio, ~300 µV con la red de una casa y ~2.200 µV al lado del
 *    router.
 *
 * El estado (`GeneratorState`) es serializable y viaja con el reloj del
 * chaleco. Sin él, cada lote arrancaba de la misma semilla —todos los lotes
 * eran idénticos— y con la fase y la deriva en cero, así que el trazado tenía
 * un escalón cada 10 minutos.
 */

import {
  FLAG_ADC_SATURATED,
  FLAG_EVENT_MARKER,
  FLAG_LEAD_OFF,
  FLAG_R_PEAK,
  FLAG_RLD_OFF,
  FLAG_SQI_SHIFT,
  SAMPLE_RATE_HZ,
  SQ_BAD,
  SQ_GOOD,
  SQ_MARGINAL,
  STEP_MS,
} from '../frame'
import type { EcgSample } from '../riceEncoder'
import { BEAT_AFTER_SEC, BEAT_BEFORE_SEC, renderBeat, type Beat } from './beats'
import { ouStep, Rng } from './random'
import type { EpisodeKind, ResolvedEpisode, SignalProfile } from './types'

const FS = SAMPLE_RATE_HZ
const DT = 1 / FS

/** Fondo de escala del ADS1292R con PGA 6 y VREF 2,42 V (`config.h`). */
export const FULL_SCALE_UV = 403_333
/** El firmware marca `ADC_SATURATED` por encima del 95 % del fondo de escala. */
export const SATURATION_UV = Math.round(FULL_SCALE_UV * 0.95)

/** Umbrales del SQI del firmware para una derivación, con 10 % de histéresis. */
const SQI_BAD_RATIO = 10
const SQI_MARGINAL_RATIO = 15
const SQI_HYSTERESIS = 0.1

/** Bits de `leadOffFlags` del STATUS (`HolterStatus.h:133-199`). */
export const STATUS_LEAD_RA_OFF = 0x01
export const STATUS_LEAD_RLD_OFF = 0x04
export const STATUS_LEAD_ADC_SATURATED = 0x08
export const STATUS_LEAD_SIGNAL_SUSPECT = 0x20

interface ElectrodeNoise {
  /** Ruido blanco de la entrada, en µV RMS. */
  whiteUV: number
  /** Deriva de línea de base, desvío estacionario en µV. */
  baselineUV: number
  /** Componente respiratoria de la línea de base, en µV de pico. */
  respirationUV: number
  /** Red de 50 Hz que entra aun en un ambiente limpio, en µV de pico. */
  cleanMainsUV: number
  /** EMG durante una ráfaga, en µV RMS. */
  emgUV: number
  /** Deriva lenta del offset de continua, en µV. */
  dcDriftUV: number
}

const ELECTRODE: Record<SignalProfile['electrode'], ElectrodeNoise> = {
  gel: {
    whiteUV: 1.8,
    baselineUV: 130,
    respirationUV: 45,
    cleanMainsUV: 12,
    emgUV: 14,
    dcDriftUV: 900,
  },
  dry: {
    whiteUV: 6,
    baselineUV: 230,
    respirationUV: 85,
    cleanMainsUV: 38,
    emgUV: 24,
    dcDriftUV: 2200,
  },
}

/** Red de 50 Hz en µV de pico. ~2 mV pp en una casa, ~18 mV pp al lado del router. */
const MAINS_UV: Record<Exclude<SignalProfile['environment'], 'clean'>, number> = {
  home: 1050,
  router: 8900,
}

export interface CarriedEpisode {
  kind: EpisodeKind
  /** En segundos del reloj del generador. */
  start: number
  end: number
  value: number
}

export interface GeneratorState {
  version: 1
  rng: number
  /** Reloj del generador al inicio de la próxima muestra, en segundos. */
  t: number
  /** Latidos que todavía tocan muestras futuras; el último es el próximo a dibujar. */
  beats: Beat[]
  /** FC sinusal actual: persigue al objetivo con una constante de tiempo. */
  hr: number
  /** Componente lenta de la FC, en lpm. */
  hrWalk: number
  /** R del latido que cierra la pausa compensatoria de una PVC. */
  compensateTo: number | null
  /** Inicio de las pausas ya aplicadas, para no aplicarlas dos veces. */
  pausesDone: number[]
  respPhase: number
  mainsPhase: number
  mainsMod: number
  afPhase: number
  baseline: number
  dcDrift: number
  emgEnvelope: number
  emgRemaining: number
  emgLast: number
  emgFiltered: number
  motion: number
  leadBlend: number
  sqi: number
  /** Episodios que siguen activos (o todavía no empezaron) al terminar el lote. */
  carried: CarriedEpisode[]
}

export function initialGeneratorState(profile: SignalProfile): GeneratorState {
  const rng = new Rng(profile.seed)
  const rr = 60 / profile.baseBpm
  return {
    version: 1,
    t: 0,
    beats: [{ r: 0.2 + rng.next() * rr, kind: 'N', rr, amp: profile.rAmplitudeUV }],
    hr: profile.baseBpm,
    hrWalk: 0,
    compensateTo: null,
    pausesDone: [],
    respPhase: rng.next() * 2 * Math.PI,
    mainsPhase: rng.next() * 2 * Math.PI,
    mainsMod: 0,
    afPhase: 0,
    baseline: 0,
    dcDrift: 0,
    emgEnvelope: 0,
    emgRemaining: 0,
    emgLast: 0,
    emgFiltered: 0,
    motion: 0,
    leadBlend: 0,
    sqi: SQ_GOOD,
    carried: [],
    rng: rng.state,
  }
}

export function isGeneratorState(value: unknown): value is GeneratorState {
  if (typeof value !== 'object' || value === null) return false
  const s = value as Record<string, unknown>
  return s.version === 1 && typeof s.t === 'number' && Array.isArray(s.beats) && s.beats.length > 0
}

export interface GenerateRequest {
  profile: SignalProfile
  durationSec: number
  /** Episodios del lote, con tiempos relativos a su inicio. */
  episodes: ResolvedEpisode[]
  state: GeneratorState
  /** `millis()` del equipo en la primera muestra. Se escribe módulo 2³². */
  startT0Ms: number
  /** Hora de pared de la primera muestra: la usa el ritmo circadiano. */
  wallStartEpochMs: number
}

export interface GeneratedEcg {
  samples: EcgSample[]
  /** Picos R dibujados dentro del lote. */
  beats: number
  state: GeneratorState
  /**
   * Estado por segundo de señal: 0 bien, 1 electrodo suelto, 2 calidad mala.
   * Es lo que mira el aviso de colocación, que decide por duración.
   */
  secondStatus: Uint8Array
  /** OR de los bits de `leadOffFlags` del STATUS durante el lote. */
  leadFlags: number
  /** Peor SQI del lote (1..3), o 0 si no hubo muestras. */
  worstSqi: number
}

function circadianOffsetBpm(epochMs: number): number {
  const date = new Date(epochMs)
  const hour = date.getHours() + date.getMinutes() / 60
  // Mínimo de madrugada (~−7 lpm a las 3) y máximo a media tarde (~+5 a las 15).
  return -1 + 6 * Math.cos((2 * Math.PI * (hour - 15)) / 24)
}

function activeAt(episodes: CarriedEpisode[], kind: EpisodeKind, t: number) {
  return episodes.find((e) => e.kind === kind && t >= e.start && t < e.end)
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

/**
 * El latido siguiente a `prev`. Acá vive todo el ritmo: la FC sinusal con su
 * variabilidad y los episodios que la reemplazan.
 */
function planNext(
  prev: Beat,
  state: GeneratorState,
  episodes: CarriedEpisode[],
  profile: SignalProfile,
  rng: Rng,
  respAt: (t: number) => number,
  wallAt: (t: number) => number,
): Beat {
  const r = prev.r
  const tachy = activeAt(episodes, 'tachycardia', r)
  const brady = activeAt(episodes, 'bradycardia', r)
  const target =
    tachy?.value ??
    brady?.value ??
    profile.baseBpm + (profile.circadian ? circadianOffsetBpm(wallAt(r)) : 0)

  // La FC sinusal no salta: sube en segundos al empezar una taquicardia y
  // vuelve más lento después.
  const tau = tachy || brady ? 6 : 20
  state.hr += (target - state.hr) * (1 - Math.exp(-prev.rr / tau))
  state.hrWalk = ouStep(state.hrWalk, 60, 2.5, prev.rr, rng)

  // Arritmia sinusal respiratoria (HF) y onda de Mayer (LF, 0,1 Hz).
  const modulation =
    1 + 0.035 * Math.sin(respAt(r)) + 0.02 * Math.sin(2 * Math.PI * 0.1 * r) + 0.006 * rng.normal()
  const sinusRr = (60 / Math.max(25, state.hr + state.hrWalk)) * modulation
  const amp = (rr: number) =>
    profile.rAmplitudeUV * (1 + 0.06 * Math.sin(respAt(r + rr))) * (1 + 0.025 * rng.normal())

  if (state.compensateTo !== null) {
    const next = state.compensateTo
    state.compensateTo = null
    return { r: next, kind: 'N', rr: next - r, amp: amp(next - r) }
  }

  const pause = episodes.find(
    (e) =>
      e.kind === 'pause' &&
      e.start > r &&
      e.start <= r + sinusRr &&
      !state.pausesDone.includes(e.start),
  )
  if (pause) {
    state.pausesDone.push(pause.start)
    const rr = Math.max(sinusRr, pause.value / 1000)
    return { r: r + rr, kind: 'N', rr, amp: amp(rr) }
  }

  const af = activeAt(episodes, 'afib', r)
  if (af) {
    // Irregularmente irregular: sin la P que marca el paso, cada RR es nuevo.
    const rr = clamp((60 / Math.max(30, af.value)) * (1 + 0.22 * rng.normal()), 0.28, 2.2)
    return { r: r + rr, kind: 'F', rr, amp: amp(rr) }
  }

  const bigeminy = activeAt(episodes, 'pvc_bigeminy', r)
  const pvc = activeAt(episodes, 'pvc', r)
  if (prev.kind !== 'V' && (bigeminy || (pvc && rng.next() < (pvc.value / 60) * sinusRr))) {
    // Prematura, con pausa compensatoria completa: el nodo sinusal no se
    // reinicia, así que el latido siguiente cae a dos RR del anterior.
    const rr = 0.6 * sinusRr
    state.compensateTo = r + 2 * sinusRr
    return { r: r + rr, kind: 'V', rr, amp: amp(rr) }
  }

  const pac = activeAt(episodes, 'pac', r)
  if (prev.kind === 'N' && pac && rng.next() < (pac.value / 60) * sinusRr) {
    // Prematura y sin pausa compensatoria: la P ectópica reinicia el nodo.
    const rr = 0.68 * sinusRr
    return { r: r + rr, kind: 'A', rr, amp: amp(rr) }
  }

  return { r: r + sinusRr, kind: 'N', rr: sinusRr, amp: amp(sinusRr) }
}

function classifySqi(current: number, ratio: number): number {
  if (ratio < SQI_BAD_RATIO * (1 - SQI_HYSTERESIS)) return SQ_BAD
  if (ratio >= SQI_MARGINAL_RATIO * (1 + SQI_HYSTERESIS)) return SQ_GOOD
  if (ratio < SQI_MARGINAL_RATIO * (1 - SQI_HYSTERESIS)) {
    return current === SQ_BAD && ratio < SQI_BAD_RATIO * (1 + SQI_HYSTERESIS) ? SQ_BAD : SQ_MARGINAL
  }
  return current === SQ_BAD ? SQ_MARGINAL : current
}

/** Marca `mask[i] = 1` en las muestras del lote que caen dentro de los episodios `kind`. */
function maskFor(
  episodes: CarriedEpisode[],
  kind: EpisodeKind,
  g0: number,
  n: number,
): Uint8Array | null {
  let mask: Uint8Array | null = null
  for (const e of episodes) {
    if (e.kind !== kind) continue
    const from = Math.max(0, Math.ceil((e.start - g0) * FS))
    const to = Math.min(n, Math.ceil((e.end - g0) * FS))
    if (from >= to) continue
    mask ??= new Uint8Array(n)
    mask.fill(1, from, to)
  }
  return mask
}

function valueMask(
  episodes: CarriedEpisode[],
  kind: EpisodeKind,
  g0: number,
  n: number,
): Float32Array | null {
  let values: Float32Array | null = null
  for (const e of episodes) {
    if (e.kind !== kind) continue
    const from = Math.max(0, Math.ceil((e.start - g0) * FS))
    const to = Math.min(n, Math.ceil((e.end - g0) * FS))
    if (from >= to) continue
    values ??= new Float32Array(n)
    values.fill(e.value, from, to)
  }
  return values
}

export function generateEcg(request: GenerateRequest): GeneratedEcg {
  const { profile, episodes: resolved } = request
  // Copia profunda: el estado de entrada no se muta, así un lote que falla a
  // mitad de camino no deja el generador a medias.
  const state: GeneratorState = structuredClone(request.state)
  const rng = new Rng(state.rng)
  const n = Math.round(request.durationSec * FS)
  const g0 = state.t
  const gEnd = g0 + n / FS
  const noise = ELECTRODE[profile.electrode]
  const mainsUV =
    profile.environment === 'clean' ? noise.cleanMainsUV : MAINS_UV[profile.environment]
  const respHz = Math.max(4, profile.respirationPerMin) / 60
  const respPhase0 = state.respPhase
  const respAt = (t: number) => respPhase0 + 2 * Math.PI * respHz * (t - g0)
  const wallAt = (t: number) => request.wallStartEpochMs + (t - g0) * 1000

  const episodes: CarriedEpisode[] = [
    ...state.carried,
    ...resolved.map((e) => ({
      kind: e.kind,
      start: g0 + e.startSec,
      end: g0 + Math.max(e.startSec, e.endSec),
      value: e.value,
    })),
  ]

  // 1. Ritmo: se agenda hasta que el próximo latido ya no toque este lote.
  const beats = state.beats
  while (beats[beats.length - 1].r - BEAT_BEFORE_SEC < gEnd) {
    beats.push(planNext(beats[beats.length - 1], state, episodes, profile, rng, respAt, wallAt))
  }

  // 2. Morfología.
  const ecg = new Float64Array(n)
  for (const beat of beats) renderBeat(ecg, g0, FS, beat)

  // 3. Máscaras de episodios.
  const leadOff = maskFor(episodes, 'lead_off', g0, n)
  const rldOff = maskFor(episodes, 'rld_off', g0, n)
  const saturated = maskFor(episodes, 'saturation', g0, n)
  const motionAmp = valueMask(episodes, 'motion', g0, n)
  const afRate = valueMask(episodes, 'afib', g0, n)

  const values = new Float64Array(n)
  const artifact = new Float64Array(n)
  const flags = new Uint8Array(n)
  const emgStartPerSample = 1 / (90 * FS)

  for (let i = 0; i < n; i++) {
    const t = g0 + i * DT
    state.respPhase += 2 * Math.PI * respHz * DT
    state.baseline = ouStep(state.baseline, 15, noise.baselineUV, DT, rng)
    state.dcDrift = ouStep(state.dcDrift, 900, noise.dcDriftUV, DT, rng)
    state.mainsMod = ouStep(state.mainsMod, 30, 0.12, DT, rng)
    state.mainsPhase += 2 * Math.PI * 50 * DT
    if (state.mainsPhase > 2 * Math.PI) state.mainsPhase -= 2 * Math.PI

    const rld = rldOff?.[i] === 1
    const mains =
      mainsUV *
      (1 + state.mainsMod) *
      (rld ? 6 : 1) *
      (Math.sin(state.mainsPhase) + 0.03 * Math.sin(3 * state.mainsPhase))

    // EMG en ráfagas cortas, como un paciente quieto que igual se acomoda.
    const motionValue = motionAmp?.[i] ?? 0
    if (state.emgRemaining <= 0 && rng.next() < emgStartPerSample) {
      state.emgRemaining = Math.round((1 + 3 * rng.next()) * FS)
    }
    const emgTarget = state.emgRemaining > 0 || motionValue > 0 ? 1 : 0
    state.emgEnvelope += (emgTarget - state.emgEnvelope) / (0.15 * FS)
    if (state.emgRemaining > 0) state.emgRemaining--
    const w = rng.normal()
    state.emgFiltered += 0.6 * (w - state.emgLast - state.emgFiltered)
    state.emgLast = w
    const emg =
      state.emgFiltered * noise.emgUV * 0.9 * state.emgEnvelope * (motionValue > 0 ? 4 : 1)

    state.motion =
      motionValue > 0
        ? ouStep(state.motion, 0.3, motionValue, DT, rng)
        : state.motion * (1 - DT / 0.4)

    let fWave = 0
    if (afRate) {
      if (afRate[i] > 0) {
        state.afPhase += 2 * Math.PI * (5.8 + 0.8 * Math.sin(2 * Math.PI * 0.25 * t)) * DT
        fWave = 55 * (1 + 0.3 * Math.sin(2 * Math.PI * 0.4 * t)) * Math.sin(state.afPhase)
      }
    }

    const white = noise.whiteUV * rng.normal()
    let value =
      profile.dcOffsetUV +
      state.dcDrift +
      state.baseline +
      noise.respirationUV * Math.sin(state.respPhase) +
      ecg[i] +
      fWave +
      mains +
      white +
      emg +
      state.motion

    // Electrodo suelto: la entrada queda flotando y se va al riel con la red
    // encima; al volver tarda en asentarse. Se graba igual y se marca.
    const off = leadOff?.[i] === 1
    state.leadBlend += ((off ? 1 : 0) - state.leadBlend) * (DT / (off ? 0.35 : 1.2))
    if (state.leadBlend > 1e-4) {
      const floating = 0.985 * FULL_SCALE_UV + 15_000 * Math.sin(state.mainsPhase)
      value = (1 - state.leadBlend) * value + state.leadBlend * floating
    }
    if (saturated?.[i] === 1) value = FULL_SCALE_UV

    value = clamp(value, -FULL_SCALE_UV, FULL_SCALE_UV)
    values[i] = value
    artifact[i] = white + emg + state.motion

    let f = 0
    if (off || state.leadBlend > 0.5) f |= FLAG_LEAD_OFF
    if (rld) f |= FLAG_RLD_OFF
    if (Math.abs(value) >= SATURATION_UV) f |= FLAG_ADC_SATURATED
    flags[i] = f
  }

  // 4. Picos R y botón de síntoma.
  let beatCount = 0
  for (const beat of beats) {
    if (beat.r < g0 || beat.r >= gEnd) continue
    const i = Math.min(n - 1, Math.round((beat.r - g0) * FS))
    beatCount++
    if ((flags[i] & FLAG_LEAD_OFF) === 0 && state.leadBlend < 0.1) flags[i] |= FLAG_R_PEAK
  }
  for (const e of episodes) {
    if (e.kind !== 'symptom' || e.start < g0 || e.start >= gEnd) continue
    flags[Math.min(n - 1, Math.round((e.start - g0) * FS))] |= FLAG_EVENT_MARKER
  }

  // 5. SQI por segundo: el ruido que el firmware ve sobre la señal filtrada
  // contra la amplitud de R. La red no entra: el notch se la saca, y por eso el
  // índice del equipo dice BUENA aun al lado del router.
  const seconds = Math.ceil(n / FS)
  const secondStatus = new Uint8Array(seconds)
  let worstSqi = 0
  let leadFlags = 0
  for (let s = 0; s < seconds; s++) {
    const from = s * FS
    const to = Math.min(n, from + FS)
    let sumSq = 0
    let leadOffCount = 0
    let rldCount = 0
    let saturatedCount = 0
    for (let i = from; i < to; i++) {
      sumSq += artifact[i] * artifact[i]
      if (flags[i] & FLAG_LEAD_OFF) leadOffCount++
      if (flags[i] & FLAG_RLD_OFF) rldCount++
      if (flags[i] & FLAG_ADC_SATURATED) saturatedCount++
    }
    const rms = Math.sqrt(sumSq / Math.max(1, to - from))
    state.sqi = classifySqi(state.sqi, profile.rAmplitudeUV / Math.max(1, 4 * rms))
    for (let i = from; i < to; i++) {
      let level = state.sqi
      if (flags[i] & (FLAG_LEAD_OFF | FLAG_ADC_SATURATED)) level = SQ_BAD
      else if (flags[i] & FLAG_RLD_OFF && level === SQ_GOOD) level = SQ_MARGINAL
      flags[i] |= level << FLAG_SQI_SHIFT
      if (worstSqi === 0 || level < worstSqi) worstSqi = level
    }
    if (leadOffCount * 2 > to - from) secondStatus[s] = 1
    else if (state.sqi === SQ_BAD || saturatedCount * 2 > to - from) secondStatus[s] = 2
    if (leadOffCount > 0) leadFlags |= STATUS_LEAD_RA_OFF | STATUS_LEAD_SIGNAL_SUSPECT
    if (rldCount > 0) leadFlags |= STATUS_LEAD_RLD_OFF
    if (saturatedCount > 0) leadFlags |= STATUS_LEAD_ADC_SATURATED
  }

  // 6. Muestras. `millis()` es de 32 bits: el timestamp se escribe módulo 2³².
  const samples: EcgSample[] = new Array(n)
  for (let i = 0; i < n; i++) {
    samples[i] = {
      timestampMs: (request.startT0Ms + i * STEP_MS) % 0x1_0000_0000,
      // `| 0` y no solo `Math.round`: `Math.round(-0.3)` da `-0`.
      rawUV: [Math.round(values[i]) | 0],
      flags: flags[i],
    }
  }

  state.t = gEnd
  state.beats = beats.filter((beat) => beat.r + BEAT_AFTER_SEC > gEnd)
  state.pausesDone = state.pausesDone.filter((start) => start > gEnd - 60)
  state.carried = episodes.filter((e) => e.end > gEnd || e.start >= gEnd)
  state.rng = rng.state

  return { samples, beats: beatCount, state, secondStatus, leadFlags, worstSqi }
}
