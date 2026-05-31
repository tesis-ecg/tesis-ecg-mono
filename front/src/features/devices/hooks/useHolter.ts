import { useQuery } from '@tanstack/react-query'

import { getHolter } from '../api/devicesApi'

export function useHolter(id: string | undefined) {
  return useQuery({
    queryKey: ['devices', id],
    queryFn: () => getHolter(id!),
    enabled: Boolean(id),
  })
}
