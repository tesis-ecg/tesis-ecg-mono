import { useQuery } from '@tanstack/react-query'

import { getDashboardKpis } from '../api/dashboardApi'

export function useDashboardKpis() {
  return useQuery({
    queryKey: ['dashboard', 'kpis'],
    queryFn: getDashboardKpis,
  })
}
