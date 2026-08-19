import { useQuery } from '@tanstack/react-query'

import { getDashboardOverview } from '../api/dashboardApi'

export function useRunningStudies() {
  return useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: getDashboardOverview,
    select: (overview) => overview.runningStudies,
  })
}
