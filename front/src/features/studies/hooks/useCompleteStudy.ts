import { useMutation, useQueryClient } from '@tanstack/react-query'

import { invalidateClinicalData } from '@/lib/queryKeys'

import { completeStudy } from '../api/studiesApi'

/**
 * Cerrar un estudio mueve el estado del paciente, el del equipo y los contadores
 * del dashboard, así que invalida todo el bloque clínico y no solo `studies`.
 */
export function useCompleteStudy() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (studyId: string) => completeStudy(studyId),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
