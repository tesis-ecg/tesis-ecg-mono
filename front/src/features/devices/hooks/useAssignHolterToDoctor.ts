import { useMutation, useQueryClient } from '@tanstack/react-query'

import { assignHolterToDoctor } from '../api/devicesApi'
import { invalidateClinicalData } from '@/lib/queryKeys'
import type { AssignHolterToDoctorInput } from '../types'

export function useAssignHolterToDoctor() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: AssignHolterToDoctorInput) => assignHolterToDoctor(input),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
