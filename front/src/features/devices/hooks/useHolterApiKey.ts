import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { getHolterApiKey, rotateHolterApiKey } from '../api/devicesApi'
import type { HolterApiKey } from '../types'

function apiKeyQueryKey(id: string | undefined) {
  return ['devices', id, 'api-key'] as const
}

/**
 * La API key del equipo, pedida **solo cuando el admin la pide**.
 *
 * El `enabled` no es una optimización: el backend audita cada lectura, así que
 * traerla al montar dejaría un evento "vio la API key" por cada visita al
 * detalle del dispositivo. Con esto hay un evento por revelación deliberada, y
 * la key no viaja hasta entonces.
 */
export function useHolterApiKey(id: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: apiKeyQueryKey(id),
    queryFn: () => getHolterApiKey(id!),
    enabled: Boolean(id) && enabled,
    // La key no cambia sola: solo la cambia una rotación, y esa escribe la caché.
    staleTime: Infinity,
    retry: false,
  })
}

export function useRotateHolterApiKey() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => rotateHolterApiKey(id),
    onSuccess: (data: HolterApiKey) => {
      queryClient.setQueryData(apiKeyQueryKey(data.deviceId), data)
    },
  })
}
