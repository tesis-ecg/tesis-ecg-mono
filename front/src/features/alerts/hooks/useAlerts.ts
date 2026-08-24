import { useQuery } from '@tanstack/react-query'

import { queryKeys } from '@/lib/queryKeys'

import { listAlerts } from '../api/alertsApi'
import type { AlertListParams } from '../types'

export function useAlerts(params: AlertListParams = {}) {
  return useQuery({
    queryKey: [...queryKeys.alerts, params],
    queryFn: () => listAlerts(params),
  })
}

/**
 * Solo el contador de pendientes, para el badge del menú.
 *
 * Pide una página de tamaño 1: `pendingTotal` no depende del filtro ni del
 * limit, así que traer más filas sería puro peso.
 */
export function usePendingAlertCount() {
  return useQuery({
    queryKey: [...queryKeys.alerts, 'pending-count'],
    queryFn: () => listAlerts({ acknowledged: false, limit: 1 }),
    select: (data) => data.pendingTotal,
    staleTime: 60_000,
  })
}
