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

export interface IngestHeaders {
  serial: string
  apiKey: string
  uptimeMs: number | null
  firmwareVersion: string
  batteryPct: number | null
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
  const requestHeaders: Record<string, string> = {
    'Content-Type': 'application/octet-stream',
    Authorization: `Bearer ${headers.apiKey}`,
    'X-Device-Serial': headers.serial,
    'X-Firmware-Version': headers.firmwareVersion,
  }
  if (headers.uptimeMs !== null) {
    requestHeaders['X-Device-Uptime-Ms'] = String(Math.round(headers.uptimeMs))
  }
  if (headers.batteryPct !== null) {
    requestHeaders['X-Battery-Pct'] = String(Math.round(headers.batteryPct))
  }

  const response = await fetch('/api/ingest/ecg-frames', {
    method: 'POST',
    headers: requestHeaders,
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
  return {
    ok: false,
    status: response.status,
    ack: null,
    errorCode: (payload?.code as string) ?? null,
    errorMessage: (payload?.message as string) ?? `HTTP ${response.status}`,
  }
}

/** Reintenta únicamente fallas transitorias; los aborts nunca se absorben. */
export async function uploadWithRetries(
  body: Uint8Array,
  headers: IngestHeaders,
  maxRetries: number,
  signal: AbortSignal,
  onRetry: (message: string) => void,
): Promise<IngestResult> {
  let attempt = 0
  for (;;) {
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

    const retriable = !result.ok && (result.status === 0 || result.status >= 500)
    if (result.ok || !retriable || attempt >= maxRetries) return result

    attempt++
    const backoffMs = Math.min(8000, 250 * 2 ** attempt)
    const reason = result.status === 0 ? 'error de red' : `HTTP ${result.status}`
    onRetry(`Reintento ${attempt}/${maxRetries} en ${backoffMs} ms (${reason})`)
    await wait(backoffMs, signal)
  }
}

/** Lo que el chaleco puede reportar fuera del ciclo de envío. */
export type VestStatusEvent = 'signal_quality_bad' | 'lead_off' | 'signal_recovered'

export interface DeviceStatusAck {
  notified: boolean
  alertId: string | null
  serverTime: string
}

/**
 * Canal corto del chaleco: `POST /ingest/device-status`.
 *
 * Va por `fetch` y con credencial de equipo por el mismo motivo que
 * `postFrames`: es lo que va a mandar el co-procesador WiFi, sin cookie de por
 * medio. Lo único distinto es el `Content-Type`, que acá es JSON.
 */
export async function postDeviceStatus(
  event: VestStatusEvent,
  headers: IngestHeaders,
  durationSeconds: number,
  signal?: AbortSignal,
): Promise<DeviceStatusAck> {
  const requestHeaders: Record<string, string> = {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${headers.apiKey}`,
    'X-Device-Serial': headers.serial,
    'X-Firmware-Version': headers.firmwareVersion,
  }
  if (headers.uptimeMs !== null) {
    requestHeaders['X-Device-Uptime-Ms'] = String(Math.round(headers.uptimeMs))
  }
  if (headers.batteryPct !== null) {
    requestHeaders['X-Battery-Pct'] = String(Math.round(headers.batteryPct))
  }

  const response = await fetch('/api/ingest/device-status', {
    method: 'POST',
    headers: requestHeaders,
    body: JSON.stringify({ event, durationSeconds }),
    signal,
  })

  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const message = (payload?.message as string) ?? `HTTP ${response.status}`
    throw new Error(message)
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
