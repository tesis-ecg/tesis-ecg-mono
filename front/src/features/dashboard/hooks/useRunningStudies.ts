import { useQuery } from '@tanstack/react-query'

import { getRunningStudies } from '../api/dashboardApi'

export function useRunningStudies() {
  return useQuery({
    queryKey: ['dashboard', 'running-studies'],
    queryFn: getRunningStudies,
  })
}
