import { useQuery } from '@tanstack/react-query'

import { getHolterHealth } from '../api/devicesApi'

/**
 * Estado de salud del Holter. Polling cada 30 s mientras la página esté
 * abierta — placeholder hasta tener un canal websocket/SSE (ver TES-31).
 */
export function useHolterHealth(id: string | undefined) {
  return useQuery({
    queryKey: ['devices', id, 'health'],
    queryFn: () => getHolterHealth(id!),
    enabled: Boolean(id),
    refetchInterval: 30_000,
  })
}
