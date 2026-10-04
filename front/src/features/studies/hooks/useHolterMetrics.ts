import { useQuery } from '@tanstack/react-query'

import { getStudyHolterMetrics } from '../api/studiesApi'

/** Misma cadencia que `useEcgSignal`: lo que crece con la señal se pide junto con ella. */
const IN_PROGRESS_POLL_MS = 60_000

/**
 * Métricas Holter de un estudio: FC, pausas, VFC y ST.
 *
 * El backend las recalcula en cada pedido desde los latidos ya analizados, así
 * que en un estudio en curso avanzan con cada lote; se refrescan con la misma
 * cadencia que el visor. Son las mismas que el informe congela al emitirse: lo
 * que el médico lee en la solapa y en el PDF sale de una sola fuente.
 */
export function useHolterMetrics(id: string | undefined, isInProgress = false) {
  return useQuery({
    queryKey: ['studies', id, 'holter-metrics'],
    queryFn: () => getStudyHolterMetrics(id!),
    enabled: Boolean(id),
    refetchInterval: isInProgress ? IN_PROGRESS_POLL_MS : false,
  })
}
