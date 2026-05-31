import { useMutation, useQueryClient } from '@tanstack/react-query'

import { unassignHolter } from '../api/devicesApi'

export function useUnassignHolter() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (holterId: string) => unassignHolter(holterId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['devices'] })
      void queryClient.invalidateQueries({ queryKey: ['patients'] })
    },
  })
}
