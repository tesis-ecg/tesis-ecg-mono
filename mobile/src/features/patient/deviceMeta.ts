import {
  Activity,
  CircleCheck,
  CircleHelp,
  CloudOff,
  HeartPulse,
  Power,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react-native'

import type { DeviceState } from './types'

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

/** Los tipos de aviso que puede recibir el paciente, en su idioma. */
export const ALERT_KIND_LABEL: Record<string, string> = {
  vest_misplaced: 'Chaleco mal colocado',
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
}

/** Cómo se dibuja un aviso: su texto, su color y su ícono. */
export function alertMeta(kind: string): AlertMeta {
  const tone = ALERT_TONE[kind] ?? 'info'
  return {
    label: alertLabel(kind),
    tone,
    // El latido y no un signo de información genérico: todos los avisos que no
    // son el del chaleco salen de algo que el sistema vio en el ritmo, y el
    // ícono es lo primero que se lee de la fila.
    icon: tone === 'danger' ? TriangleAlert : HeartPulse,
  }
}
