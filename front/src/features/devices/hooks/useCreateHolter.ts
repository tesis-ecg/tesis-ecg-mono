import { useMutation, useQueryClient } from '@tanstack/react-query'

import { createHolter } from '../api/devicesApi'
import { invalidateClinicalData } from '@/lib/queryKeys'
import type { CreateHolterInput } from '../types'

export function useCreateHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: CreateHolterInput) => createHolter(input),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
