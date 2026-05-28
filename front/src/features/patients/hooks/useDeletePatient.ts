import { useMutation, useQueryClient } from '@tanstack/react-query'

import { deletePatient } from '../api/patientsApi'

export function useDeletePatient() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deletePatient(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
