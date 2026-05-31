import { useMutation, useQueryClient } from '@tanstack/react-query'

import { updateHolter } from '../api/devicesApi'
import type { UpdateHolterInput } from '../types'

/**
 * Edita parcialmente un Holter. Invalida `['devices']` + `['patients']` porque
 * si el holter está assigned, la card del paciente también puede mostrar info
 * derivada (modelo/firmware).
 */
export function useUpdateHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdateHolterInput }) =>
      updateHolter(id, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['devices'] })
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
