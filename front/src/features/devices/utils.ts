import type { HolterFormValues } from './holterSchema'
import type { CreateHolterInput, HolterStatus } from './types'

/**
 * Convierte los valores del form (todos strings) al payload de API,
 * normalizando `firmwareVersion` vacío a `null`.
 */
export function holterFormToInput(values: HolterFormValues): CreateHolterInput {
  const firmware = values.firmwareVersion.trim()
  return {
    serial: values.serial.trim(),
    model: values.model.trim(),
    firmwareVersion: firmware === '' ? null : firmware,
  }
}

export const HOLTER_STATUS_LABEL: Record<HolterStatus, string> = {
  available: 'Disponible',
  assigned: 'Asignado',
  maintenance: 'Mantenimiento',
  retired: 'Retirado',
}
