import { useQuery } from '@tanstack/react-query'

import { getDashboardOverview } from '../api/dashboardApi'

export function useDashboardKpis() {
  return useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: getDashboardOverview,
    select: (overview) => overview.kpis,
  })
}
