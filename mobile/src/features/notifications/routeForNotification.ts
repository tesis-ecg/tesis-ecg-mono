export interface NotificationData {
  type?: string
  alertId?: string
  occurredAt?: string
}

export type NotificationRoute =
  | { pathname: '/report'; params: { alertId: string; occurredAt: string } }
  | { pathname: '/(tabs)/device' }
  | { pathname: '/notifications' }

/**
 * A dónde lleva tocar una notificación.
 *
 * Función pura porque es la parte que más fácil se rompe en silencio: si un
 * aviso lleva al lugar equivocado, la app "funciona" y el paciente igual no
 * hace lo que le pedimos.
 *
 * - `vest_misplaced`: no hay formulario que completar, lo que tiene que hacer
 *   es acomodarse el chaleco. Va a "Dispositivo", donde ve el estado.
 * - `report_request`: abre el formulario ya anclado al momento del aviso.
 * - Cualquier otra cosa cae en el centro de avisos: el tap nunca queda mudo y
 *   un push futuro tampoco puede mandar a una ruta inexistente.
 */
export function routeForNotification(data: NotificationData | null | undefined): NotificationRoute {
  if (data?.type === 'vest_misplaced') return { pathname: '/(tabs)/device' }
  if (data?.type === 'report_request' && data.alertId && data.occurredAt) {
    return {
      pathname: '/report',
      params: {
        alertId: data.alertId,
        occurredAt: data.occurredAt,
      },
    }
  }
  return { pathname: '/notifications' }
}
