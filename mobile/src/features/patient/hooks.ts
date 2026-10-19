import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import * as api from './api'
import { isVestMisplaced } from './vestStatus'
import type { ReportInput } from './types'

export const patientKeys = {
  device: ['device'] as const,
  alerts: ['alerts'] as const,
  alertList: (params: api.AlertQuery) => ['alerts', params] as const,
  reports: ['reports'] as const,
  report: (reportId: string) => ['reports', reportId] as const,
  catalogs: ['catalogs'] as const,
}

/**
 * Estado del chaleco.
 *
 * Se refresca cada 2 minutos mientras la pantalla está abierta: es la que el
 * paciente mira justamente cuando quiere confirmar que el equipo está andando.
 */
export function useDevice() {
  return useQuery({
    queryKey: patientKeys.device,
    queryFn: api.getDevice,
    refetchInterval: 120_000,
  })
}

export function useAlerts(query: api.AlertQuery = {}) {
  return useQuery({ queryKey: patientKeys.alertList(query), queryFn: () => api.getAlerts(query) })
}

export function usePendingAlerts() {
  return useAlerts({ limit: 3, offset: 0, status: 'pending' })
}

/**
 * Si el chaleco está mal colocado ahora mismo.
 *
 * Sale de `GET /mobile/device`, que expone la última colocación reportada por
 * el equipo. Antes se deducía de los avisos con una ventana de una hora, porque
 * el backend no guardaba el estado: acomodarse el chaleco no apagaba el cartel
 * hasta que esa hora pasara. Además comparte query con la pantalla que la usa,
 * así que el push que la invalida refresca las dos cosas de una.
 */
export function useVestMisplaced(): boolean {
  const device = useDevice()
  return isVestMisplaced(device.data)
}

const ALERT_PAGE_SIZE = 20

/**
 * La lista paginada del centro de notificaciones.
 *
 * El filtro va en la query y no se aplica sobre lo ya traído: con paginación,
 * filtrar en el cliente deja páginas enteras sin ninguna fila que pase el
 * filtro —la lista se ve vacía con un "Cargar más" abajo— y el contador del
 * total deja de coincidir con lo que se dibuja.
 */
export function useInfiniteAlerts(status: api.AlertStatus = 'all') {
  return useInfiniteQuery({
    queryKey: patientKeys.alertList({ limit: ALERT_PAGE_SIZE, status }),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      api.getAlerts({ limit: ALERT_PAGE_SIZE, offset: pageParam, status }),
    getNextPageParam: (lastPage) => {
      const next = lastPage.offset + lastPage.items.length
      return next < lastPage.total ? next : undefined
    },
  })
}

export function useReports() {
  return useQuery({ queryKey: patientKeys.reports, queryFn: () => api.getReports() })
}

export function useReport(reportId: string | undefined) {
  return useQuery({
    queryKey: patientKeys.report(reportId ?? ''),
    queryFn: () => api.getReport(reportId as string),
    enabled: Boolean(reportId),
  })
}

/** Los catálogos casi no cambian; se cachean todo lo que dura la sesión. */
export function useCatalogs() {
  return useQuery({
    queryKey: patientKeys.catalogs,
    queryFn: api.getCatalogs,
    staleTime: Infinity,
  })
}

export function useCreateReport() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (input: ReportInput) => api.createReport(input),
    onSuccess: () => {
      // Los avisos también: responder uno lo saca de "pendiente" en Inicio.
      void queryClient.invalidateQueries({ queryKey: patientKeys.reports })
      void queryClient.invalidateQueries({ queryKey: patientKeys.alerts })
    },
  })
}
