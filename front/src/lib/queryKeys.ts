import type { QueryClient } from '@tanstack/react-query'

export const queryKeys = {
  alerts: ['alerts'] as const,
  dashboard: ['dashboard'] as const,
  devices: ['devices'] as const,
  patients: ['patients'] as const,
  studies: ['studies'] as const,
  users: ['users'] as const,
  doctors: ['doctors'] as const,
  ecg: ['ecg'] as const,
}

export async function invalidateClinicalData(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.devices }),
    queryClient.invalidateQueries({ queryKey: queryKeys.patients }),
    queryClient.invalidateQueries({ queryKey: queryKeys.studies }),
    queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
  ])
}
