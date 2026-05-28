import { useMutation, useQueryClient } from '@tanstack/react-query'

import { updatePatient } from '../api/patientsApi'
import type { UpdatePatientInput } from '../types'

export function useUpdatePatient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: UpdatePatientInput }) =>
      updatePatient(id, input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
