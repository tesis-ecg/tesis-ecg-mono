import { useQuery } from '@tanstack/react-query'

import { getDashboardOverview } from '../api/dashboardApi'

export function useDeviceWatchdog() {
  return useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: getDashboardOverview,
    select: (overview) => overview.deviceWatchdog,
  })
}
