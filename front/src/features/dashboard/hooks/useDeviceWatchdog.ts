import { useQuery } from '@tanstack/react-query'

import { getDeviceWatchdog } from '../api/dashboardApi'

export function useDeviceWatchdog() {
  return useQuery({
    queryKey: ['dashboard', 'device-watchdog'],
    queryFn: getDeviceWatchdog,
  })
}
