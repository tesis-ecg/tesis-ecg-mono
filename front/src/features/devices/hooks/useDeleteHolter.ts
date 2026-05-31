import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deleteHolter } from '../api/devicesApi'

/**
 * Soft-delete del Holter (status → 'retired'). Si estaba asignado, también
 * limpia al paciente. Invalida ambos namespaces.
 */
export function useDeleteHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteHolter(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['devices'] })
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
