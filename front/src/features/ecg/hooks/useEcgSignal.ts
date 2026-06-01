import { useQuery } from '@tanstack/react-query'

import { getStudyEcg } from '../api/ecgApi'

/**
 * Trae la señal ECG de un estudio.
 *
 * `staleTime: Infinity` porque la señal histórica es inmutable para un estudio
 * dado — no tiene sentido refetchearla automáticamente. (Los estudios
 * `in_progress` serán otro endpoint o streaming.)
 *
 * `gcTime` agresivo (5 min) para que el Float32Array se libere cuando el
 * componente se desmonta y no quedan otros consumers.
 */
export function useEcgSignal(studyId: string | undefined) {
  return useQuery({
    queryKey: ['ecg', studyId],
    queryFn: () => getStudyEcg(studyId!),
    enabled: Boolean(studyId),
    staleTime: Infinity,
    gcTime: 5 * 60 * 1000,
  })
}
