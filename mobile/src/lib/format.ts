const LOCALE = 'es-AR'

/**
 * `mié 30 ago, 23:37` — el formato que el paciente lee de un vistazo.
 *
 * `hour12: false` explícito: el ICU de Hermes en iOS resolvía es-AR a 12 h y
 * mostraba "11:37 p. m.". En Argentina la hora se escribe de 0 a 23, y en un
 * registro clínico la ambigüedad de am/pm es justo lo que no se quiere.
 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(LOCALE, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat(LOCALE, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(date)
}

const UNITS: { limit: number; seconds: number; one: string; many: string }[] = [
  { limit: 3600, seconds: 60, one: 'minuto', many: 'minutos' },
  { limit: 86_400, seconds: 3600, one: 'hora', many: 'horas' },
  { limit: Infinity, seconds: 86_400, one: 'día', many: 'días' },
]

/**
 * "hace 5 minutos", "en 2 horas".
 *
 * Escrito a mano y no con `Intl.RelativeTimeFormat`: **Hermes no lo trae**. En
 * el simulador de iOS la pantalla de Inicio reventaba con "undefined cannot be
 * used as a constructor" apenas se mostraba el último envío del chaleco.
 * `Intl.DateTimeFormat`, que sí existe, se sigue usando en el resto del módulo.
 *
 * Se escala por unidad en vez de saltar siempre a la más grande: "hace 1 día"
 * para algo de hace 25 horas confunde justo a quien está mirando si su chaleco
 * viene transmitiendo bien.
 */
export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'Sin datos'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'Sin datos'

  const diffSeconds = Math.round((date.getTime() - Date.now()) / 1000)
  const abs = Math.abs(diffSeconds)
  if (abs < 60) return 'recién'

  const unit = UNITS.find((candidate) => abs < candidate.limit) ?? UNITS[UNITS.length - 1]
  const amount = Math.round(abs / unit.seconds)
  const noun = amount === 1 ? unit.one : unit.many
  return diffSeconds < 0 ? `hace ${amount} ${noun}` : `en ${amount} ${noun}`
}

/**
 * Edad en años cumplidos.
 *
 * La fecha se parte a mano en vez de usar `new Date(iso)`: un `YYYY-MM-DD` se
 * parsea como medianoche **UTC**, y compararlo contra los componentes locales
 * corre la fecha un día en cualquier huso al oeste de Greenwich — Argentina
 * incluida. El resultado era una edad de más justo alrededor del cumpleaños.
 */
export function calculateAge(birthDate: string | null): number | null {
  if (!birthDate) return null
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(birthDate)
  if (!match) return null
  const [, year, month, day] = match.map(Number)

  const now = new Date()
  let age = now.getFullYear() - year
  const monthDiff = now.getMonth() + 1 - month
  if (monthDiff < 0 || (monthDiff === 0 && now.getDate() < day)) age -= 1
  return age >= 0 ? age : null
}
