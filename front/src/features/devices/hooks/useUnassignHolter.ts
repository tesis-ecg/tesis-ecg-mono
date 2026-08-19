import { useMutation, useQueryClient } from '@tanstack/react-query'

import { unassignHolter } from '../api/devicesApi'
import { invalidateClinicalData } from '@/lib/queryKeys'

export function useUnassignHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (holterId: string) => unassignHolter(holterId),
    onSuccess: () => {
      void invalidateClinicalData(queryClient)
    },
  })
}
