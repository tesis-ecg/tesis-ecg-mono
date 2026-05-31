import { useMutation, useQueryClient } from '@tanstack/react-query'

import { reassignHolter } from '../api/devicesApi'
import type { ReassignHolterInput } from '../types'

export function useReassignHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ReassignHolterInput) => reassignHolter(input),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['devices'] })
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
