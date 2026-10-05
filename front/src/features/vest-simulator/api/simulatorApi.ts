/**
 * Cliente del endpoint de ingesta.
 *
 * Usa `fetch` crudo y no el cliente axios del portal a propósito: el chaleco no
 * tiene sesión. Se autentica con `X-Device-Serial` + bearer, exactamente como
 * lo hará el co-procesador WiFi, y sin `credentials` para que la cookie del
 * médico no viaje por accidente.
 */

import { api } from '@/lib/api'

export interface IngestAck {
  framesReceived: number
  framesAccepted: number
  framesRejected: number
  framesDuplicate: number
  lastAcceptedSeq: number | null
  batchId: string | null
  studyId: string
  serverTime: string
}

/** Versión del firmware del equipo (`config.h:358-360`), como la manda el puente. */
export const FIRMWARE_VERSION = '2.1.0'

export interface IngestHeaders {
  serial: string
  apiKey: string
  /** `null` solo para simular la falla de omitirlo. */
  uptimeMs: number | null
  /**
   * Epoch UTC del puente, leído en el MISMO instante que `uptimeMs` y del mismo
   * arranque (`bootId`). Sale de `bridgeTimeForPost`: es la hora real, y el
   * uptime es el de ese arranque a esta hora.
   */
  bridgeEpochMs: number
  /** Arranque al que pertenece el par epoch/uptime (`X-Device-Boot-Id`). */
  bootId: number
  timeSource: 'ntp' | 'none'
  timeUncertaintyMs: number
  firmwareVersion: string
  /** Solo si la batería está medida; si no, el puente no la manda. */
  batteryPct: number | null
  /** Cabeceras `X-Device-*` ya armadas (`diagHeaders`). */
  diag: Record<string, string>
}

/**
 * Las cabeceras que manda el ESP32-C3 real (`addIngestHeaders`,
 * `esp32_wifi_bridge.cpp:1013-1143`), iguales para la ingesta y para el canal
 * corto. La regla que importa es la de la hora (§3 de
 * `docs/integracion-ingesta-con-horario.md`): epoch y uptime describen el mismo
 * instante del mismo arranque, así que su resta da el arranque exacto.
 */
function deviceHeaders(headers: IngestHeaders, contentType: string): Record<string, string> {
  const out: Record<string, string> = {
    'Content-Type': contentType,
    Authorization: `Bearer ${headers.apiKey}`,
    'X-Device-Serial': headers.serial,
    'X-Device-Boot-Id': String(headers.bootId),
    'X-Bridge-Epoch-Ms': String(Math.round(headers.bridgeEpochMs)),
    'X-Time-Sync-Source': headers.timeSource,
    'X-Time-Sync-Uncertainty-Ms': String(Math.round(headers.timeUncertaintyMs)),
    'X-Firmware-Version': headers.firmwareVersion,
  }
  if (headers.uptimeMs !== null) out['X-Device-Uptime-Ms'] = String(Math.round(headers.uptimeMs))
  if (headers.batteryPct !== null) out['X-Battery-Pct'] = String(Math.round(headers.batteryPct))
  return { ...out, ...headers.diag }
}

export interface IngestResult {
  ok: boolean
  status: number
  ack: IngestAck | null
  errorCode: string | null
  errorMessage: string | null
}

function wait(ms: number, signal: AbortSignal): Promise<void> {
  signal.throwIfAborted()
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms)
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(signal.reason)
      },
      { once: true },
    )
  })
}

export async function postFrames(
  body: Uint8Array,
  headers: IngestHeaders,
  signal?: AbortSignal,
): Promise<IngestResult> {
  const response = await fetch('/api/ingest/ecg-frames', {
    method: 'POST',
    headers: deviceHeaders(headers, 'application/octet-stream'),
    body: body as BodyInit,
    signal,
  })

  const payload = await response.json().catch(() => null)
  if (response.ok) {
    return {
      ok: true,
      status: response.status,
      ack: payload as IngestAck,
      errorCode: null,
      errorMessage: null,
    }
  }
  // El backend manda los errores como `detail: {code, message}`; algunos
  // proxies los aplanan. Se aceptan las dos formas.
  const detail = (payload?.detail ?? payload) as { code?: string; message?: string } | null
  return {
    ok: false,
    status: response.status,
    ack: null,
    errorCode: detail?.code ?? null,
    errorMessage: detail?.message ?? `HTTP ${response.status}`,
  }
}

/** `BRIDGE_BACKEND_GRACIA_MS`: cuánto insiste el puente ante un 5xx o un timeout. */
export const BRIDGE_GRACE_MS = 60_000

/** Pausa mínima entre reintentos. El puente reintenta en la vuelta siguiente del loop. */
const RETRY_PAUSE_MS = 250

/**
 * Manda un POST como el puente: un 5xx o una falla de red es pasajera y el
 * mismo lote se reintenta enseguida, mientras la racha —medida desde el inicio
 * del primer intento fallido— no pase la gracia. Un 3xx/4xx es permanente y
 * vuelve al toque (`bridgeFalloEsPasajero`, `esp32_wifi_bridge.cpp:278-311`).
 *
 * Los aborts nunca se absorben.
 */
export async function uploadWithGrace(
  body: Uint8Array,
  headers: IngestHeaders,
  graceMs: number,
  signal: AbortSignal,
  onRetry: (message: string) => void,
): Promise<IngestResult> {
  let streakStart: number | null = null
  let attempt = 0
  for (;;) {
    const startedAt = Date.now()
    let result: IngestResult
    try {
      result = await postFrames(body, headers, signal)
    } catch (error) {
      if (signal.aborted || (error instanceof DOMException && error.name === 'AbortError')) {
        throw error
      }
      result = {
        ok: false,
        status: 0,
        ack: null,
        errorCode: 'NETWORK_ERROR',
        errorMessage: error instanceof Error ? error.message : 'Error de red',
      }
    }

    const transient = !result.ok && (result.status === 0 || result.status >= 500)
    if (result.ok || !transient) return result
    streakStart ??= startedAt
    if (Date.now() - streakStart >= graceMs) return result

    attempt++
    const reason = result.status === 0 ? 'error de red' : `HTTP ${result.status}`
    onRetry(
      `Reintento ${attempt} del mismo POST (${reason}); gracia de ${Math.round(graceMs / 1000)} s`,
    )
    await wait(RETRY_PAUSE_MS, signal)
  }
}

/** Lo que el chaleco puede reportar fuera del ciclo de envío. */
export type VestStatusEvent = 'signal_quality_bad' | 'lead_off' | 'signal_recovered' | 'alive'

export interface DeviceStatusAck {
  notified: boolean
  alertId: string | null
  serverTime: string
}

/**
 * Canal corto del chaleco: `POST /ingest/device-status`.
 *
 * Va por `fetch` y con credencial de equipo por el mismo motivo que
 * `postFrames`: es lo que manda el co-procesador WiFi, sin cookie de por medio.
 * El cuerpo es el del puente (`BridgeUplink.h:432-451, 625-649`): batería y SQI
 * solo cuando se conocen.
 */
export async function postDeviceStatus(
  event: VestStatusEvent,
  headers: IngestHeaders,
  durationSeconds: number,
  extra: { sqi?: number | null } = {},
  signal?: AbortSignal,
): Promise<DeviceStatusAck> {
  const body: Record<string, number | string> = { event, durationSeconds }
  if (headers.batteryPct !== null) body.batteryPct = Math.min(100, Math.round(headers.batteryPct))
  if (extra.sqi && extra.sqi >= 1 && extra.sqi <= 3) body.sqi = extra.sqi

  const response = await fetch('/api/ingest/device-status', {
    method: 'POST',
    headers: deviceHeaders(headers, 'application/json'),
    body: JSON.stringify(body),
    signal,
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = (payload?.detail ?? payload) as { message?: string } | null
    throw new Error(detail?.message ?? `HTTP ${response.status}`)
  }
  return payload as DeviceStatusAck
}

export type SimulatedAnomalyType = 'tachycardia' | 'bradycardia' | 'afib' | 'pvc' | 'pause'

export interface SimulateAnomalyBody {
  eventType: SimulatedAnomalyType
  severity: 'high' | 'critical'
  durationSeconds?: number
  secondsBeforeEnd?: number
}

export interface SimulatedAnomaly {
  alertId: string
  eventId: string
  occurredAt: string
  offsetMs: number
}

/**
 * Fabrica un hallazgo clínico sobre la señal ya subida y notifica al paciente.
 *
 * Va por el cliente axios del portal y no con la credencial del equipo: no es
 * algo que el chaleco reporte, es lo que produciría el pipeline de análisis
 * —hoy stub— del lado del backend. Requiere sesión de admin.
 */
export async function simulateAnomaly(
  studyId: string,
  body: SimulateAnomalyBody,
): Promise<SimulatedAnomaly> {
  const { data } = await api.post<SimulatedAnomaly>(`/studies/${studyId}/simulate-anomaly`, body)
  return data
}

export interface SimulatorDevice {
  id: string
  serial: string
  model: string
  status: string
  patientName: string | null
}

interface HolterListItem {
  id: string
  serial: string
  model: string
  status: string
  /**
   * Antes acá decía `patientName`, un campo que `HolterOut` nunca devolvió: el
   * selector mostraba "sin paciente" para todos los equipos y no había forma de
   * elegir uno asignado. El backend expone `assignedPatientName`.
   */
  assignedPatientName?: string | null
}

/** Equipos disponibles. Requiere sesión de admin (va por el cliente del portal). */
export async function listDevices(): Promise<SimulatorDevice[]> {
  const { data } = await api.get<{ items: HolterListItem[] }>('/devices', {
    params: { limit: 100 },
  })
  return data.items.map((item) => ({
    id: item.id,
    serial: item.serial,
    model: item.model,
    status: item.status,
    patientName: item.assignedPatientName ?? null,
  }))
}

/** Rota la API key del equipo. El texto plano se devuelve una sola vez. */
export async function rotateApiKey(deviceId: string): Promise<string> {
  const { data } = await api.post<{ apiKey: string }>(`/devices/${deviceId}/api-key`)
  return data.apiKey
}
