import { useMutation, useQueryClient } from '@tanstack/react-query'

import { reassignHolter } from '../api/devicesApi'
import type { ReassignHolterInput } from '../types'
import { invalidateClinicalData } from '@/lib/queryKeys'

export function useReassignHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ReassignHolterInput) => reassignHolter(input),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
