import { useQuery } from '@tanstack/react-query'

import { getStudyPatientReports } from '../api/studiesApi'

/**
 * Registros del paciente de un estudio.
 *
 * Se refresca solo mientras el estudio está en curso, con la misma cadencia que
 * `useEcgSignal`: el paciente puede cargar un síntoma desde el celular mientras
 * el médico tiene la pantalla abierta.
 */
export function useStudyPatientReports(id: string | undefined, isInProgress = false) {
  return useQuery({
    queryKey: ['studies', id, 'patient-reports'],
    queryFn: () => getStudyPatientReports(id!),
    enabled: Boolean(id),
    refetchInterval: isInProgress ? 60_000 : false,
  })
}
