/**
 * Formato de timestamp para el ECG: `HH:MM:SS.mmm`. Acepta cualquier
 * timestamp en ms (relativo al estudio o absoluto epoch).
 *
 * Pensado para mostrar tiempo transcurrido relativo al inicio del estudio
 * — pasar `ms - signal.startTimestamp` al llamarlo desde el viewer.
 */
export function formatTimestampMs(ms: number): string {
  const sign = ms < 0 ? '-' : ''
  const abs = Math.abs(Math.floor(ms))
  const h = Math.floor(abs / 3600000)
  const m = Math.floor((abs % 3600000) / 60000)
  const s = Math.floor((abs % 60000) / 1000)
  const millis = abs % 1000
  const pad = (n: number, len = 2) => String(n).padStart(len, '0')
  return `${sign}${pad(h)}:${pad(m)}:${pad(s)}.${pad(millis, 3)}`
}

/** Formato más compacto para el eje (sin ms): `HH:MM:SS`. */
export function formatTimestampShort(ms: number): string {
  const abs = Math.max(0, Math.floor(ms))
  const h = Math.floor(abs / 3600000)
  const m = Math.floor((abs % 3600000) / 60000)
  const s = Math.floor((abs % 60000) / 1000)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(h)}:${pad(m)}:${pad(s)}`
}

/**
 * Hora de pared local, `HH:MM:SS`. Para el eje del ECG.
 *
 * El backend manda todo en UTC; la conversión a la zona del médico la hace el
 * navegador. No se fuerza ninguna zona: un estudio se lee donde se lee.
 */
export function formatWallClockShort(epochMs: number): string {
  return new Date(epochMs).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

/** Hora de pared local con milisegundos, para el cursor: `HH:MM:SS.mmm`. */
export function formatWallClock(epochMs: number): string {
  const millis = String(Math.abs(Math.floor(epochMs)) % 1000).padStart(3, '0')
  return `${formatWallClockShort(epochMs)}.${millis}`
}

/** Fecha y hora completas, para tooltips donde el día importa. */
export function formatWallClockDateTime(epochMs: number): string {
  return new Date(epochMs).toLocaleString(undefined, {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}
