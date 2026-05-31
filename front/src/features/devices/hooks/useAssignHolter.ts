import { useMutation, useQueryClient } from '@tanstack/react-query'

import { assignHolter } from '../api/devicesApi'
import type { AssignHolterInput } from '../types'

/**
 * Asigna un Holter a un paciente. Invalida tanto `['devices']` (cambia el
 * status del Holter) como `['patients']` (cambia `assignedDeviceId` y la
 * salud del dispositivo en el tab del paciente).
 */
export function useAssignHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: AssignHolterInput) => assignHolter(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['devices'] })
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
