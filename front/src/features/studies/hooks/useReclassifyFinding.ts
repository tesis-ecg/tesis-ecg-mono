import { useMutation, useQueryClient } from '@tanstack/react-query'

import { queryKeys } from '@/lib/queryKeys'

import { createManualFinding, reclassifyFinding } from '../api/studiesApi'
import type { Reclassification, ReclassificationRequest } from '../types'

interface ReclassifyVariables {
  findingId: string
  body: ReclassificationRequest
}

/**
 * Reclasificar un hallazgo del motor.
 *
 * La respuesta trae las métricas Holter recalculadas, así que se escriben
 * directo en la caché: el médico ve las cifras nuevas sin esperar otro pedido.
 * Si el recálculo falló en el servidor (`status: 'pending'`) la corrección ya
 * quedó guardada, pero esas cifras no sirven: se vuelven a pedir.
 *
 * Los hallazgos, la señal del visor (sus bandas llevan el estado de la
 * corrección) y el informe clínico (borrador y vista previa) dependen de lo
 * mismo, y se refrescan. Los informes ya finalizados no cambian.
 */
export function useReclassifyFinding(studyId: string) {
  const onSuccess = useFindingChangeSuccess(studyId)
  return useMutation({
    mutationFn: ({ findingId, body }: ReclassifyVariables) =>
      reclassifyFinding(studyId, findingId, body),
    onSuccess,
  })
}

export function useCreateManualFinding(studyId: string) {
  const queryClient = useQueryClient()
  const onSuccess = useFindingChangeSuccess(studyId)
  return useMutation({
    mutationFn: (body: import('@/generated/openapi').ManualFindingRequest) =>
      createManualFinding(studyId, body),
    onSuccess: (result) => {
      onSuccess(result)
      void queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] === 'studies' && query.queryKey.length === 2,
      })
    },
  })
}

function useFindingChangeSuccess(studyId: string) {
  const queryClient = useQueryClient()
  return (result: Reclassification) => {
    const metricsKey = ['studies', studyId, 'holter-metrics']
    if (result.holterMetrics.status === 'ok') {
      queryClient.setQueryData(metricsKey, result.holterMetrics)
    } else {
      void queryClient.invalidateQueries({ queryKey: metricsKey })
    }
    void queryClient.invalidateQueries({ queryKey: ['studies', studyId, 'findings'] })
    void queryClient.invalidateQueries({ queryKey: [...queryKeys.ecg, studyId] })
    void queryClient.invalidateQueries({ queryKey: [...queryKeys.clinicalReports, studyId] })
  }
}
