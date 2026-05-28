import { useMutation, useQueryClient } from '@tanstack/react-query'

import { createPatient } from '../api/patientsApi'
import type { CreatePatientInput } from '../types'

export function useCreatePatient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreatePatientInput) => createPatient(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
