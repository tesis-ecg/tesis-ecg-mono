import { useQuery } from '@tanstack/react-query'

import { getAttentionPatients } from '../api/dashboardApi'

export function useAttentionPatients() {
  return useQuery({
    queryKey: ['dashboard', 'attention-patients'],
    queryFn: getAttentionPatients,
  })
}
