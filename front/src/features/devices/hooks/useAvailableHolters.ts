import { useQuery } from '@tanstack/react-query'

import { listHolters } from '../api/devicesApi'

const AVAILABLE_PARAMS = { status: ['available' as const], limit: 100, offset: 0 }

/**
 * Lista los Holters libres para poblar el Select de asignación.
 * Usa una query-key fija (sin parámetros volátiles) para que las mutaciones
 * de assign/unassign la puedan invalidar de forma simple vía `['devices']`.
 */
export function useAvailableHolters() {
  return useQuery({
    queryKey: ['devices', AVAILABLE_PARAMS],
    queryFn: () => listHolters(AVAILABLE_PARAMS),
  })
}
