import { AlertTriangle, Check, X } from 'lucide-react'
import { useEffect, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TablePagination,
  TableRow,
} from '@/components/ui/table'
import { AlertSeverityBadge } from '@/features/alerts/components/AlertSeverityBadge'
import { ALERT_KIND_LABEL } from '@/features/alerts/labels'
import { useAcknowledgeAlert } from '@/features/alerts/hooks/useAcknowledgeAlert'
import { useAlerts } from '@/features/alerts/hooks/useAlerts'
import type { AlertSeverity } from '@/features/alerts/types'
import { unwrapError } from '@/lib/api'
import { formatDateTime, formatRelativeTime } from '@/lib/time'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 20

const SEVERITY_OPTIONS: { value: AlertSeverity; label: string }[] = [
  { value: 'critical', label: 'Críticas' },
  { value: 'high', label: 'Altas' },
  { value: 'medium', label: 'Medias' },
  { value: 'low', label: 'Bajas' },
]

type View = 'pending' | 'acknowledged' | 'all'

const VIEW_OPTIONS: { value: View; label: string }[] = [
  { value: 'pending', label: 'Pendientes' },
  { value: 'acknowledged', label: 'Atendidas' },
  { value: 'all', label: 'Todas' },
]

function parseSeverityParam(value: string | null): AlertSeverity[] {
  if (!value) return []
  const valid = new Set<AlertSeverity>(['critical', 'high', 'medium', 'low'])
  return value.split(',').filter((v): v is AlertSeverity => valid.has(v as AlertSeverity))
}

/**
 * Bandeja de alertas.
 *
 * Existe porque la ingesta venía generando alertas que solo se veían en un
 * widget del dashboard, sin forma de marcarlas como atendidas: la columna
 * `acknowledged_at` de la base no la escribía nadie. El default es
 * "Pendientes" — es la única vista que representa trabajo por hacer.
 */
export function Alerts() {
  const [searchParams, setSearchParams] = useSearchParams()

  const viewParam = searchParams.get('view') as View | null
  const view: View =
    viewParam && VIEW_OPTIONS.some((o) => o.value === viewParam) ? viewParam : 'pending'
  const severity = useMemo(() => parseSeverityParam(searchParams.get('severity')), [searchParams])
  const page = Math.max(1, Number(searchParams.get('page')) || 1)
  const offset = (page - 1) * PAGE_SIZE

  const { data, isLoading, isError, error, refetch, isFetching } = useAlerts({
    acknowledged: view === 'all' ? undefined : view === 'acknowledged',
    severity: severity.length > 0 ? severity : undefined,
    limit: PAGE_SIZE,
    offset,
  })
  const acknowledge = useAcknowledgeAlert()

  const setParam = (key: string, value: string | null) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (value) next.set(key, value)
        else next.delete(key)
        next.delete('page')
        return next
      },
      { replace: true },
    )
  }

  const toggleSeverity = (value: AlertSeverity) => {
    const updated = severity.includes(value)
      ? severity.filter((s) => s !== value)
      : [...severity, value]
    setParam('severity', updated.length > 0 ? updated.join(',') : null)
  }

  const goToPage = (target: number) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (target === 1) next.delete('page')
        else next.set('page', String(target))
        return next
      },
      { replace: false },
    )
  }

  useEffect(() => {
    if (isError && error) {
      toast.error(unwrapError(error), {
        id: 'alerts-list-error',
        action: { label: 'Reintentar', onClick: () => void refetch() },
      })
    }
  }, [isError, error, refetch])

  const handleAcknowledge = (id: string, patientName: string) => {
    acknowledge.mutate(id, {
      onSuccess: () => toast.success(`Alerta de ${patientName} marcada como atendida.`),
      onError: (mutationError) => toast.error(unwrapError(mutationError)),
    })
  }

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const items = data?.items ?? []
  const hasActiveFilters = severity.length > 0 || view !== 'pending'
  const colCount = 6

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Alertas</h1>
        <p className="text-body2 text-gray-600">
          Hallazgos detectados sobre la señal de los estudios en curso.
          {data ? ` ${data.pendingTotal} pendiente${data.pendingTotal === 1 ? '' : 's'}.` : ''}
        </p>
      </header>

      <Card className="flex flex-col gap-0 overflow-hidden p-0">
        <div className="flex flex-col gap-3 px-6 pt-6 pb-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Vista">
            {VIEW_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => setParam('view', opt.value === 'pending' ? null : opt.value)}
                aria-pressed={view === opt.value}
                className={cn(
                  'cursor-pointer rounded-full border px-3 py-1 text-body3 transition-colors',
                  view === opt.value
                    ? 'border-primary-500 bg-primary-50 text-primary-500'
                    : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50',
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>

          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Filtrar por severidad"
          >
            {hasActiveFilters && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSearchParams({}, { replace: true })}
                className="text-gray-600"
              >
                <X className="mr-1 size-4" aria-hidden />
                Limpiar
              </Button>
            )}
            {SEVERITY_OPTIONS.map((opt) => {
              const active = severity.includes(opt.value)
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggleSeverity(opt.value)}
                  aria-pressed={active}
                  className={cn(
                    'cursor-pointer rounded-full border px-3 py-1 text-body3 transition-colors',
                    active
                      ? 'border-primary-500 bg-primary-50 text-primary-500'
                      : 'border-gray-200 bg-white text-gray-700 hover:bg-gray-50',
                  )}
                >
                  {opt.label}
                </button>
              )
            })}
          </div>
        </div>

        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Severidad</TableHead>
              <TableHead>Paciente</TableHead>
              <TableHead className="hidden md:table-cell">Hallazgo</TableHead>
              <TableHead className="hidden sm:table-cell">Detectada</TableHead>
              <TableHead className="hidden lg:table-cell">Estado</TableHead>
              <TableHead className="w-44">
                <span className="sr-only">Acciones</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <TableRow key={`sk-${i}`}>
                  {Array.from({ length: colCount }).map((__, j) => (
                    <TableCell key={`sk-${i}-${j}`}>
                      <Skeleton className="h-4 w-full max-w-32" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={colCount} className="border-b-0 p-0">
                  <EmptyState
                    icon={AlertTriangle}
                    title={view === 'pending' ? 'Sin alertas pendientes' : 'No hay alertas'}
                    description={
                      view === 'pending'
                        ? 'Todo lo detectado hasta ahora ya fue revisado.'
                        : 'No hay resultados para los filtros aplicados.'
                    }
                  />
                </TableCell>
              </TableRow>
            ) : (
              items.map((alert) => (
                <TableRow key={alert.id}>
                  <TableCell>
                    <AlertSeverityBadge severity={alert.severity} />
                  </TableCell>
                  <TableCell className="font-medium text-gray-900">
                    <Link
                      to={`/patients/${alert.patientId}`}
                      className="text-primary-500 hover:underline"
                    >
                      {alert.patientName}
                    </Link>
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <span className="text-gray-900">
                      {ALERT_KIND_LABEL[alert.kind] ?? 'Hallazgo'}
                    </span>
                    <span className="block text-body3 text-gray-600">{alert.message}</span>
                  </TableCell>
                  <TableCell
                    className="hidden sm:table-cell text-gray-600"
                    title={formatDateTime(alert.detectedAt)}
                  >
                    {formatRelativeTime(alert.detectedAt)}
                  </TableCell>
                  <TableCell className="hidden lg:table-cell text-body3 text-gray-600">
                    {alert.acknowledgedAt ? (
                      <>
                        Atendida
                        {alert.acknowledgedByName ? ` por ${alert.acknowledgedByName}` : ''}
                      </>
                    ) : (
                      <span className="text-amber-700">Pendiente</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-2">
                      {alert.studyId && (
                        <Button variant="ghost" size="sm" asChild>
                          <Link to={`/studies/${alert.studyId}`}>Ver señal</Link>
                        </Button>
                      )}
                      {!alert.acknowledgedAt && (
                        <Button
                          size="sm"
                          variant="outline"
                          // Solo la fila en vuelo, no todas: `isPending` a secas
                          // congelaba la tabla entera en cada click.
                          disabled={acknowledge.isPending && acknowledge.variables === alert.id}
                          onClick={() => handleAcknowledge(alert.id, alert.patientName)}
                        >
                          <Check className="mr-1 size-4" aria-hidden />
                          {acknowledge.isPending && acknowledge.variables === alert.id
                            ? 'Guardando…'
                            : 'Atender'}
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>

        <TablePagination
          page={page}
          totalPages={totalPages}
          total={total}
          pageSize={PAGE_SIZE}
          onPageChange={goToPage}
          isFetching={isFetching && !isLoading}
        />
      </Card>
    </div>
  )
}
