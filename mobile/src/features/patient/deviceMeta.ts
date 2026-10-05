import {
  Activity,
  AudioWaveform,
  BatteryWarning,
  CircleCheck,
  CircleHelp,
  CloudOff,
  HeartPulse,
  Power,
  Shirt,
  type LucideIcon,
} from 'lucide-react-native'

import type { VestAuraTone } from '@/lib/gradients'

import type { AlertSeverity, DeviceState } from './types'

/** Los tonos semánticos compartidos con `Badge`. */
export type DeviceTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info'

interface DeviceStateMeta {
  label: string
  /** Explicación en primera persona, sin jerga: la lee el paciente, no un técnico. */
  description: string
  tone: DeviceTone
  icon: LucideIcon
}

export const DEVICE_STATE: Record<DeviceState, DeviceStateMeta> = {
  none: {
    label: 'Sin chaleco',
    description: 'Todavía no tenés un chaleco asignado. Tu médico te lo va a entregar.',
    tone: 'neutral',
    icon: CircleHelp,
  },
  never_connected: {
    label: 'Sin encender',
    description: 'Tu chaleco todavía no se conectó. Encendelo y esperá unos minutos.',
    tone: 'warning',
    icon: Power,
  },
  recording: {
    label: 'Grabando',
    description: 'Tu chaleco está registrando y enviando los datos a tu médico.',
    tone: 'success',
    icon: Activity,
  },
  idle: {
    label: 'Conectado',
    description: 'El chaleco se comunicó con el sistema, pero todavía no hay un estudio en curso.',
    tone: 'info',
    icon: CircleCheck,
  },
  stale: {
    label: 'Sin enviar datos',
    description:
      'Hace varias horas que no recibimos datos. Revisá que el chaleco esté puesto, cargado y cerca del WiFi de tu casa.',
    tone: 'danger',
    icon: CloudOff,
  },
}

/**
 * El color del halo que se apoya detrás de la foto del chaleco.
 *
 * No reusa el `tone` de arriba y no es un descuido. La pastilla tiene que
 * distinguir los cinco estados, porque cada uno se explica distinto; el aura
 * contesta una sola pregunta —¿el estudio está corriendo?— y con cinco colores
 * dejaría de contestarla. De ahí las dos diferencias con la pastilla:
 *
 * - `never_connected` es `warning` arriba y azul acá: el chaleco todavía no
 *   arrancó, igual que en `idle`, y el matiz de por qué lo dice el texto.
 * - `stale` es `danger` arriba y ámbar acá, para que el rojo del aura quede
 *   reservado al chaleco mal colocado. Es lo único de la app que pide una
 *   acción física en el momento; si el rojo del fondo también apareciera
 *   cuando el WiFi se cortó, dejaría de significar "andá y acomodátelo".
 */
export const DEVICE_AURA: Record<DeviceState, VestAuraTone> = {
  // Sin chaleco la pantalla muestra el estado vacío y no la foto, así que este
  // valor no llega a dibujarse nunca. Está para que el mapa siga siendo total.
  none: 'waiting',
  never_connected: 'waiting',
  idle: 'waiting',
  recording: 'live',
  stale: 'attention',
}

/** Los tipos de aviso que puede recibir el paciente, en su idioma. */
export const ALERT_KIND_LABEL: Record<string, string> = {
  vest_misplaced: 'Chaleco mal colocado',
  battery_low: 'Batería baja del chaleco',
  battery_critical: 'Batería crítica del chaleco',
  tachycardia: 'Latidos más rápidos de lo habitual',
  bradycardia: 'Latidos más lentos de lo habitual',
  afib: 'Ritmo irregular',
  pvc: 'Latido adelantado',
  pause: 'Pausa en el ritmo',
  noise: 'La señal se registró con ruido',
  other: 'Revisemos este momento',
}

export function alertLabel(kind: string): string {
  return ALERT_KIND_LABEL[kind] ?? ALERT_KIND_LABEL.other
}

interface AlertMeta {
  label: string
  /**
   * Qué tan fuerte se pinta el aviso.
   *
   * Son dos y no más porque son dos las cosas que el paciente puede hacer:
   * - `danger`: el chaleco está mal puesto. Mientras siga así no se registra
   *   nada, así que es lo único de la app que pide una acción física ya.
   * - `info`: encontramos algo en el latido y queremos que nos cuente cómo se
   *   sentía. Importa, pero no es una urgencia y no puede gritar igual que lo
   *   anterior: si todo es rojo, el rojo deja de significar algo.
   */
  tone: 'danger' | 'info'
  icon: LucideIcon
}

const ALERT_TONE: Record<string, AlertMeta['tone']> = {
  vest_misplaced: 'danger',
  battery_low: 'danger',
  battery_critical: 'danger',
}

/**
 * Los avisos que no hablan del ritmo llevan su propio ícono: la batería, el
 * chaleco y la señal con ruido se resuelven con una acción del paciente, no
 * contándonos cómo se sentía, y el ícono es lo primero que se lee de la fila.
 * Todo lo demás salió de algo que el sistema vio en el latido.
 */
const ALERT_ICON: Record<string, LucideIcon> = {
  battery_low: BatteryWarning,
  battery_critical: BatteryWarning,
  vest_misplaced: Shirt,
  noise: AudioWaveform,
}

/** Cómo se dibuja un aviso: su texto, su color y su ícono. */
export function alertMeta(kind: string): AlertMeta {
  const tone = ALERT_TONE[kind] ?? 'info'
  return {
    label: alertLabel(kind),
    tone,
    icon: ALERT_ICON[kind] ?? HeartPulse,
  }
}

/** Qué tan urgente se pinta un aviso en la pila de Inicio. */
export type AlertUrgency = 'info' | 'warning' | 'danger'

/**
 * El color de un aviso según su severidad: azul lo que importa sin apuro,
 * amarillo lo que conviene mirar pronto y rojo lo crítico.
 *
 * Los que `alertMeta` ya marca como `danger` por su tipo —chaleco mal colocado,
 * batería— siguen en rojo aunque el backend los mande con otra severidad: piden
 * una acción física y no pueden quedar pintados como un hallazgo más.
 */
export function alertUrgency(kind: string, severity: AlertSeverity | undefined): AlertUrgency {
  if (severity === 'critical' || alertMeta(kind).tone === 'danger') return 'danger'
  if (severity === 'high') return 'warning'
  return 'info'
}

const SEVERITIES: readonly AlertSeverity[] = ['low', 'medium', 'high', 'critical']

/**
 * La severidad que llega como parámetro de ruta o en el `data` de un push.
 *
 * Viene como texto suelto: un valor que no se reconoce cae en `undefined` y el
 * aviso se pinta por su tipo, en vez de reventar o de pintarse con un color
 * que no le corresponde.
 */
export function parseSeverity(value: string | undefined): AlertSeverity | undefined {
  return SEVERITIES.find((severity) => severity === value)
}

/**
 * El ícono y el borde de cada urgencia, sobre los fondos claros de
 * `alertGradient`. Los comparten la pila de Inicio y el encabezado del
 * formulario, para que el aviso se vea igual en los dos lugares.
 *
 * El borde teñido es además lo que separa una card de la de abajo cuando están
 * apiladas: sobre fondos tan claros, un borde del color de la pantalla no
 * alcanzaba.
 */
export const ALERT_URGENCY_INK: Record<AlertUrgency, { icon: string; border: string }> = {
  info: { icon: '#294dec', border: 'border-info-300' },
  warning: { icon: '#b86a16', border: 'border-warning-500' },
  danger: { icon: '#c93b2b', border: 'border-error-300' },
}
