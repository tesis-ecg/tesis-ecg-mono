import { useQuery } from '@tanstack/react-query'

import { getDashboardOverview } from '../api/dashboardApi'

/**
 * Series y totales de los gráficos.
 *
 * Misma `queryKey` que el resto de los widgets: la home entera se resuelve con
 * un solo request y cada hook se queda con la parte que dibuja.
 */
export function useDashboardActivity() {
  return useQuery({
    queryKey: ['dashboard', 'overview'],
    queryFn: getDashboardOverview,
    select: (overview) => overview.activity,
  })
}
