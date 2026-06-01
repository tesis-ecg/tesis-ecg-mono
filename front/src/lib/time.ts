/**
 * Devuelve una representación relativa en es-AR. Para el dashboard médico es
 * suficiente con minutos/horas/días — no nos importa precisión sub-minuto.
 */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'

  const diffMs = Date.now() - date.getTime()
  const future = diffMs < 0
  const absMs = Math.abs(diffMs)

  const minutes = Math.round(absMs / 60_000)
  if (minutes < 1) return future ? 'en instantes' : 'hace instantes'
  if (minutes < 60) return future ? `en ${minutes} min` : `hace ${minutes} min`

  const hours = Math.round(absMs / 3_600_000)
  if (hours < 24) return future ? `en ${hours} h` : `hace ${hours} h`

  const days = Math.round(absMs / 86_400_000)
  if (days < 30) return future ? `en ${days} d` : `hace ${days} d`

  const months = Math.round(days / 30)
  if (months < 12) return future ? `en ${months} meses` : `hace ${months} meses`

  const years = Math.round(months / 12)
  return future ? `en ${years} años` : `hace ${years} años`
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit', year: 'numeric' })
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('es-AR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Duración legible: `48h 32m` para ≥1 h, `15m 30s` para <1 h, `30 s` para <1 min.
 * Usado en la metadata de estudios donde la duración suele ser de varias horas.
 */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms) || ms < 0) return '—'
  const totalSec = Math.floor(ms / 1000)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  if (h > 0) return m > 0 ? `${h} h ${m} min` : `${h} h`
  if (m > 0) return s > 0 ? `${m} min ${s} s` : `${m} min`
  return `${s} s`
}
