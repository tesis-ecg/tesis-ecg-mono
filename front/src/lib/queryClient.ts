import { QueryClient } from '@tanstack/react-query'

/**
 * QueryClient global de la app. Defaults conservadores:
 * - `staleTime` 30s: balance entre frescura y evitar refetch agresivo
 * - `retry: 1`: el cliente HTTP (apiRetry.ts) ya retenta GETs sobre 5xx y network;
 *   acá un sólo reintento adicional cubre el caso de race conditions del cache.
 *   Excepción: errores 4xx (`UNAUTHORIZED`, `NOT_FOUND`, `VALIDATION`) no se reintentan.
 * - `refetchOnWindowFocus: false`: evita ruido en una app de dashboard que tiene
 *   muchos tabs abiertos. Para datos en vivo (señal del dispositivo, etc.) usar
 *   `refetchInterval` puntual desde el hook.
 * - `gcTime` 5min: tiempo en cache después de quedar sin observers.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30 * 1000,
      gcTime: 5 * 60 * 1000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        const code = (error as { code?: string } | null | undefined)?.code
        if (
          code === 'UNAUTHORIZED' ||
          code === 'FORBIDDEN' ||
          code === 'NOT_FOUND' ||
          code === 'VALIDATION'
        ) {
          return false
        }
        return failureCount < 1
      },
    },
  },
})
