import { useMutation, useQueryClient } from '@tanstack/react-query'

import { queryKeys } from '@/lib/queryKeys'

import { reclassifyFinding } from '../api/studiesApi'
import type { ReclassificationRequest } from '../types'

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
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ findingId, body }: ReclassifyVariables) =>
      reclassifyFinding(studyId, findingId, body),
    onSuccess: (result) => {
      const metricsKey = ['studies', studyId, 'holter-metrics']
      if (result.holterMetrics.status === 'ok') {
        queryClient.setQueryData(metricsKey, result.holterMetrics)
      } else {
        void queryClient.invalidateQueries({ queryKey: metricsKey })
      }
      void queryClient.invalidateQueries({ queryKey: ['studies', studyId, 'findings'] })
      void queryClient.invalidateQueries({ queryKey: [...queryKeys.ecg, studyId] })
      void queryClient.invalidateQueries({ queryKey: [...queryKeys.clinicalReports, studyId] })
    },
  })
}
