import { useMutation, useQueryClient } from '@tanstack/react-query'

import { invalidateClinicalData } from '@/lib/queryKeys'

import {
  createPatientAppAccount,
  regeneratePatientAppPassword,
  sendPatientPasswordReset,
} from '../api/patientsApi'

/**
 * Acciones sobre el acceso del paciente a la app móvil.
 *
 * Las dos primeras devuelven la contraseña en claro **una sola vez**: quien las
 * llame tiene que mostrarla en el momento, porque no hay forma de volver a
 * leerla (Auth0 guarda el hash).
 */
export function useCreatePatientAppAccount() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => createPatientAppAccount(id),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}

export function useRegeneratePatientAppPassword() {
  return useMutation({
    mutationFn: (id: string) => regeneratePatientAppPassword(id),
  })
}

export function useSendPatientPasswordReset() {
  return useMutation({
    mutationFn: (id: string) => sendPatientPasswordReset(id),
  })
}
