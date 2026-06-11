import { useQuery } from '@tanstack/react-query'

import { getDashboardAlerts } from '../api/dashboardApi'

export function useDashboardAlerts() {
  return useQuery({
    queryKey: ['dashboard', 'alerts'],
    queryFn: getDashboardAlerts,
  })
}
