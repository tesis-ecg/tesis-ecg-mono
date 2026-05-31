import { useQuery } from '@tanstack/react-query'

import { listHolters } from '../api/devicesApi'
import type { HolterListParams } from '../types'

export function useHolters(params: HolterListParams) {
  return useQuery({
    queryKey: ['devices', params],
    queryFn: () => listHolters(params),
  })
}
