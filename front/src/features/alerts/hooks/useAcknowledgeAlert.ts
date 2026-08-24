import { useMutation, useQueryClient } from '@tanstack/react-query'

import { queryKeys } from '@/lib/queryKeys'

import { acknowledgeAlert } from '../api/alertsApi'

/**
 * Atender una alerta cambia el listado y el KPI de alertas pendientes del
 * dashboard, así que invalida los dos.
 */
export function useAcknowledgeAlert() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (alertId: string) => acknowledgeAlert(alertId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.alerts })
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard })
    },
  })
}
