import { useQuery } from '@tanstack/react-query'

import { getStudyFindings } from '../api/studiesApi'

/** Misma cadencia que `useEcgSignal`: lo que crece con la señal se pide junto con ella. */
const IN_PROGRESS_POLL_MS = 60_000

/**
 * Hallazgos del motor de detección y resumen de calidad de un estudio.
 *
 * Mientras el estudio está en curso el motor sigue analizando bloques de 5
 * minutos a medida que llegan los lotes, así que la respuesta cambia con la
 * pantalla abierta: se vuelve a pedir con la misma cadencia que el visor. Un
 * estudio cerrado ya no cambia y no se refresca solo.
 */
export function useStudyFindings(id: string | undefined, isInProgress = false) {
  return useQuery({
    queryKey: ['studies', id, 'findings'],
    queryFn: () => getStudyFindings(id!),
    enabled: Boolean(id),
    refetchInterval: isInProgress ? IN_PROGRESS_POLL_MS : false,
  })
}
