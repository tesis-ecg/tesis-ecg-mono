import { Search, Users, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { EmptyState } from '@/components/EmptyState'
import { Badge } from '@/components/ui/badge'
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
import { PatientStatusBadge } from '@/features/patients/components/PatientStatusBadge'
import { usePatients } from '@/features/patients/hooks/usePatients'
import type { PatientStudyStatus } from '@/features/patients/types'
import { useDebouncedValue } from '@/hooks/useDebouncedValue'
import { unwrapError } from '@/lib/api'
import { formatRelativeTime } from '@/lib/time'
import { cn } from '@/lib/utils'

const PAGE_SIZE = 20

const STATUS_OPTIONS: { value: PatientStudyStatus; label: string }[] = [
  { value: 'active', label: 'Activo' },
  { value: 'paused', label: 'Pausado' },
  { value: 'completed', label: 'Completado' },
  { value: 'none', label: 'Sin estudio' },
]

function parseStatusParam(value: string | null): PatientStudyStatus[] {
  if (!value) return []
  const valid = new Set<PatientStudyStatus>(['active', 'paused', 'completed', 'none'])
  return value.split(',').filter((v): v is PatientStudyStatus => valid.has(v as PatientStudyStatus))
}

export function Patients() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const q = searchParams.get('q') ?? ''
  const status = useMemo(() => parseStatusParam(searchParams.get('status')), [searchParams])
  const page = Math.max(1, Number(searchParams.get('page')) || 1)
  const offset = (page - 1) * PAGE_SIZE

  const [searchInput, setSearchInput] = useState(q)
  const debouncedSearch = useDebouncedValue(searchInput, 300)

  useEffect(() => {
    if (debouncedSearch === q) return
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (debouncedSearch) next.set('q', debouncedSearch)
        else next.delete('q')
        next.delete('page')
        return next
      },
      { replace: true },
    )
  }, [debouncedSearch, q, setSearchParams])

  const queryParams = {
    q: debouncedSearch || undefined,
    status: status.length > 0 ? status : undefined,
    limit: PAGE_SIZE,
    offset,
  }

  const { data, isLoading, isError, error, refetch, isFetching } = usePatients(queryParams)

  const toggleStatus = (value: PatientStudyStatus) => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        const current = parseStatusParam(prev.get('status'))
        const updated = current.includes(value)
          ? current.filter((s) => s !== value)
          : [...current, value]
        if (updated.length === 0) next.delete('status')
        else next.set('status', updated.join(','))
        next.delete('page')
        return next
      },
      { replace: true },
    )
  }

  const clearFilters = () => {
    setSearchInput('')
    setSearchParams({}, { replace: true })
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
        id: 'patients-list-error',
        action: { label: 'Reintentar', onClick: () => void refetch() },
      })
    }
  }, [isError, error, refetch])

  const total = data?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const items = data?.items ?? []
  const hasActiveFilters = Boolean(debouncedSearch) || status.length > 0

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Pacientes</h1>
        <p className="text-body2 text-gray-600">Listado y gestión de pacientes monitoreados.</p>
      </header>

      <Card className="flex flex-col gap-0 overflow-hidden p-0">
        {/* Header section (estilo ican: 24px 24px 16px) */}
        <div className="flex flex-col gap-3 px-6 pt-6 pb-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex w-full max-w-sm items-center gap-3 border-b border-gray-200 pb-3">
            <Search className="size-4 shrink-0 text-gray-800" aria-hidden />
            <input
              type="search"
              placeholder="Buscar por nombre o DNI"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              aria-label="Buscar pacientes"
              className="w-full border-none bg-transparent text-body2 text-black outline-none placeholder:text-gray-400"
            />
          </div>

          <div
            className="flex flex-wrap items-center gap-2"
            role="group"
            aria-label="Filtrar por estado"
          >
            {hasActiveFilters && (
              <Button variant="ghost" size="sm" onClick={clearFilters} className="text-gray-600">
                <X className="mr-1 size-4" aria-hidden />
                Limpiar
              </Button>
            )}
            {STATUS_OPTIONS.map((opt) => {
              const active = status.includes(opt.value)
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggleStatus(opt.value)}
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

        {/* Tabla */}
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nombre</TableHead>
              <TableHead>DNI</TableHead>
              <TableHead className="hidden md:table-cell">Edad</TableHead>
              <TableHead className="hidden md:table-cell">Dispositivo</TableHead>
              <TableHead>Estado</TableHead>
              <TableHead className="hidden lg:table-cell">Último dato</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <TableRow key={`sk-${i}`}>
                  {Array.from({ length: 6 }).map((__, j) => (
                    <TableCell key={`sk-${i}-${j}`}>
                      <Skeleton className="h-4 w-full max-w-32" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="border-b-0 p-0">
                  <EmptyState
                    icon={Users}
                    title="No encontramos pacientes"
                    description={
                      hasActiveFilters
                        ? 'No hay resultados para los filtros aplicados.'
                        : 'Todavía no tenés pacientes asignados.'
                    }
                    action={
                      hasActiveFilters ? (
                        <Button onClick={clearFilters} variant="outline">
                          Limpiar filtros
                        </Button>
                      ) : null
                    }
                  />
                </TableCell>
              </TableRow>
            ) : (
              items.map((p) => (
                <TableRow
                  key={p.id}
                  onClick={() => navigate(`/patients/${p.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      navigate(`/patients/${p.id}`)
                    }
                  }}
                  tabIndex={0}
                  role="button"
                  aria-label={`Ver perfil de ${p.fullName}`}
                  className="cursor-pointer focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500 focus-visible:outline-none"
                >
                  <TableCell className="font-medium text-gray-900">{p.fullName}</TableCell>
                  <TableCell>{p.dni}</TableCell>
                  <TableCell className="hidden md:table-cell">{p.age}</TableCell>
                  <TableCell className="hidden md:table-cell">
                    {p.assignedDeviceId ? (
                      <Badge variant="outline" className="font-mono">
                        {p.assignedDeviceId}
                      </Badge>
                    ) : (
                      <span className="text-gray-400">—</span>
                    )}
                  </TableCell>
                  <TableCell>
                    <PatientStatusBadge status={p.studyStatus} />
                  </TableCell>
                  <TableCell className="hidden lg:table-cell">
                    {formatRelativeTime(p.lastDataReceivedAt)}
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
