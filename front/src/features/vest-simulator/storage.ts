/**
 * Persistencia de la flota de chalecos simulados.
 *
 * Existe por un problema concreto: la API key en claro de un equipo **se
 * devuelve una sola vez**, al rotarla. Con la flota solo en memoria, recargar
 * la página la perdía —pero en la base ya había quedado rotada— y el chaleco
 * quedaba con una credencial muerta: 401 en cada envío, sin forma de recuperarla
 * más que volviendo a rotar.
 *
 * Guardar la key en `localStorage` es una decisión consciente y acotada:
 * `/__sim/vest` es admin-only, la key solo habilita a subir señal marcada como
 * dato simulado, y se revoca rotándola de nuevo.
 *
 * Se persisten `config` y el **reloj** del equipo (`seq`, `bootId`, ancla de
 * hora, estado del generador). La flash va aparte, en IndexedDB
 * (`flashStore.ts`). Las stats y el log son de la corrida: restaurarlos
 * mostraría un "12 lotes enviados" que no pasó en esta sesión.
 */

import {
  DEFAULT_SIGNAL_PROFILE,
  EPISODE_META,
  isGeneratorState,
  type Episode,
  type SignalProfile,
} from './codec/signal'
import type { DeviceClock } from './deviceClock'
import { DEFAULT_NETWORK } from './defaults'
import { EMPTY_DIAG, INITIAL_BACKOFF, INITIAL_PLACEMENT } from './transmission'
import type { VestConfig } from './types'

const STORAGE_KEY = 'holter:vest-fleet'
const CLOCKS_KEY = 'holter:vest-clocks'

function isVestConfig(value: unknown): value is VestConfig {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.id === 'string' &&
    typeof candidate.label === 'string' &&
    typeof candidate.serial === 'string' &&
    typeof candidate.apiKey === 'string' &&
    typeof candidate.signal === 'object' &&
    typeof candidate.frames === 'object' &&
    typeof candidate.network === 'object'
  )
}

/** Configs guardadas, o `[]` si no hay nada o lo que hay no se puede leer. */
export function loadFleet(): VestConfig[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Se filtra en vez de descartar todo: una config vieja e incompleta no
    // debería tirar abajo las que sí sirven. Los campos agregados después
    // toman su default acá y no en el consumidor, que si no tendría que
    // defenderse del `undefined` en cada lectura.
    return parsed.filter(isVestConfig).map(migrateConfig)
  } catch {
    // localStorage puede fallar entero (modo privado de Safari, cuota llena).
    // El simulador tiene que seguir andando sin persistencia.
    return []
  }
}

function isEpisode(value: unknown): value is Episode {
  if (typeof value !== 'object' || value === null) return false
  const e = value as Record<string, unknown>
  return (
    typeof e.id === 'string' &&
    typeof e.kind === 'string' &&
    e.kind in EPISODE_META &&
    typeof e.batch === 'number' &&
    typeof e.startSec === 'number' &&
    typeof e.durationSec === 'number' &&
    typeof e.value === 'number'
  )
}

interface LegacySpan {
  startSec: number
  durationSec: number
}

/**
 * La señal vieja (`legacySignal.ts`) tenía tramos de anomalía que se repetían
 * en cada lote. Pasan a episodios del primer lote: es lo más parecido que se
 * puede expresar sin inventar episodios que el usuario no agendó.
 */
function legacyEpisodes(signal: Record<string, unknown>): Episode[] {
  const spans = (key: string): LegacySpan[] =>
    Array.isArray(signal[key]) ? (signal[key] as LegacySpan[]) : []
  const make = (kind: Episode['kind'], span: LegacySpan, i: number): Episode => ({
    id: `legacy-${kind}-${i}`,
    kind,
    batch: 1,
    startSec: span.startSec,
    durationSec: span.durationSec,
    value: EPISODE_META[kind].defaultValue,
  })
  const markers = Array.isArray(signal.symptomMarkersSec)
    ? (signal.symptomMarkersSec as number[])
    : []
  return [
    ...spans('leadOffSpans').map((span, i) => make('lead_off', span, i)),
    ...spans('rldOffSpans').map((span, i) => make('rld_off', span, i)),
    ...spans('saturatedSpans').map((span, i) => make('saturation', span, i)),
    ...spans('unanalyzableSpans').map((span, i) => make('motion', span, i)),
    ...markers.map((sec, i) => make('symptom', { startSec: sec, durationSec: 0 }, i)),
  ]
}

function migrateSignal(signal: Record<string, unknown>): SignalProfile {
  if (typeof signal.electrode === 'string') {
    return { ...DEFAULT_SIGNAL_PROFILE, ...(signal as Partial<SignalProfile>) }
  }
  // Config de antes del modelo nuevo: se conservan la semilla y la FC.
  return {
    ...DEFAULT_SIGNAL_PROFILE,
    seed: typeof signal.seed === 'number' ? signal.seed : DEFAULT_SIGNAL_PROFILE.seed,
    baseBpm: typeof signal.baseBpm === 'number' ? signal.baseBpm : DEFAULT_SIGNAL_PROFILE.baseBpm,
  }
}

function migrateConfig(config: VestConfig): VestConfig {
  const raw = config as unknown as Record<string, unknown>
  const signal = raw.signal as Record<string, unknown>
  const network = (raw.network ?? {}) as Record<string, unknown>
  const episodes = Array.isArray(raw.episodes)
    ? (raw.episodes as unknown[]).filter(isEpisode)
    : legacyEpisodes(signal)
  return {
    ...config,
    placementOk: config.placementOk ?? true,
    signal: migrateSignal(signal),
    episodes,
    pendingInjections: Array.isArray(raw.pendingInjections)
      ? (raw.pendingInjections as unknown[]).filter(isEpisode)
      : [],
    network: {
      ...DEFAULT_NETWORK,
      ...Object.fromEntries(Object.entries(network).filter(([key]) => key in DEFAULT_NETWORK)),
    } as VestConfig['network'],
  }
}

export function saveFleet(configs: VestConfig[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(configs))
  } catch {
    // Ídem: perder la persistencia no puede romper la corrida en curso.
  }
}

/**
 * Lee un reloj guardado y lo trae al formato actual, o `null` si no sirve.
 *
 * El formato viejo guardaba un `uptimeMs` simulado que avanzaba un lote por
 * envío, y con él se mandaba `X-Bridge-Epoch-Ms = bootEpochMs + uptimeMs`: eso
 * es lo que terminaba en 422 al volver a usar el chaleco horas después. Se
 * descarta. Lo que sí se conserva es `bootEpochMs + t0Ms`, que con el modelo
 * viejo también era la hora de la próxima muestra (`epoch − uptime` daba
 * exactamente `bootEpochMs`), así que la señal sigue pegada a lo ya ingerido.
 */
function normalizeClock(value: unknown): DeviceClock | null {
  if (typeof value !== 'object' || value === null) return null
  const c = value as Record<string, unknown>
  if (
    typeof c.bootId !== 'number' ||
    typeof c.nextSeq !== 'number' ||
    typeof c.t0Ms !== 'number' ||
    typeof c.batteryPct !== 'number'
  ) {
    return null
  }
  const bootEpochMs =
    typeof c.bootEpochMs === 'number'
      ? c.bootEpochMs
      : // Reloj de antes de que existiera el ancla: se reconstruye en vez de
        // descartarlo, porque perderlo devolvería el equipo a `seq 0`.
        Date.now() - (typeof c.uptimeMs === 'number' ? c.uptimeMs : 0)
  const anchors =
    typeof c.bootAnchors === 'object' && c.bootAnchors !== null
      ? (c.bootAnchors as Record<string, number>)
      : {}
  return {
    bootId: c.bootId,
    nextSeq: c.nextSeq,
    t0Ms: c.t0Ms,
    bootEpochMs,
    bootAnchors: { ...anchors, [c.bootId]: bootEpochMs },
    batteryPct: c.batteryPct,
    genState: isGeneratorState(c.genState) ? c.genState : null,
    // Un reloj guardado ya grabó (o al menos ya se usó): no se re-ancla.
    fresh: c.fresh === true && c.nextSeq === 0,
    backoff: { ...INITIAL_BACKOFF, ...(c.backoff as object | undefined) },
    diag: { ...EMPTY_DIAG, ...(c.diag as object | undefined) },
    placement: { ...INITIAL_PLACEMENT, ...(c.placement as object | undefined) },
  }
}

/**
 * Relojes guardados, por `id` de chaleco.
 *
 * Se persisten por el mismo motivo que las keys: sin esto, un F5 devolvía el
 * equipo a `seq 0 / bootId 0`, un estado que el hardware no puede producir. El
 * backend lo leía como una retransmisión completa —y el estudio dejaba de
 * crecer— o, si el `bootId` del estudio era otro, aceptaba desde `seq 0` y
 * **sobreescribía** en S3 los segmentos ya archivados, porque se nombran con el
 * `first_seq` del lote.
 *
 * La flash (`pending`) no va acá: son megabytes de binario. Va en IndexedDB.
 */
export function loadClocks(): Record<string, DeviceClock> {
  try {
    const raw = window.localStorage.getItem(CLOCKS_KEY)
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {}
    const clocks: Record<string, DeviceClock> = {}
    for (const [id, value] of Object.entries(parsed as Record<string, unknown>)) {
      const clock = normalizeClock(value)
      if (clock) clocks[id] = clock
    }
    return clocks
  } catch {
    return {}
  }
}

export function saveClocks(clocks: Record<string, DeviceClock>): void {
  try {
    window.localStorage.setItem(CLOCKS_KEY, JSON.stringify(clocks))
  } catch {
    // Ídem.
  }
}
