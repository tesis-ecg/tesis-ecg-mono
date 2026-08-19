import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deletePatient } from '../api/patientsApi'
import { invalidateClinicalData } from '@/lib/queryKeys'

export function useDeletePatient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deletePatient(id),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
