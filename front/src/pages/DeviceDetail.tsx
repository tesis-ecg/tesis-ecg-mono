import { ArrowLeft, HeartPulse, Pencil, Trash2, User, UserX } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'

import { EmptyState } from '@/components/EmptyState'
import { KebabMenu } from '@/components/KebabMenu'
import { Spinner } from '@/components/Spinner'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { DeleteHolterDialog } from '@/features/devices/components/DeleteHolterDialog'
import { EditHolterDialog } from '@/features/devices/components/EditHolterDialog'
import { HolterHealthCard } from '@/features/devices/components/HolterHealthCard'
import { HolterStatusBadge } from '@/features/devices/components/HolterStatusBadge'
import { ReassignHolterDialog } from '@/features/devices/components/ReassignHolterDialog'
import { UnassignHolterDialog } from '@/features/devices/components/UnassignHolterDialog'
import { useHolter } from '@/features/devices/hooks/useHolter'
import { useHolterHealth } from '@/features/devices/hooks/useHolterHealth'
import { usePatient } from '@/features/patients/hooks/usePatient'
import { isApiError, unwrapError } from '@/lib/api'
import { formatDate, formatRelativeTime } from '@/lib/time'

export function DeviceDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [editOpen, setEditOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const holter = useHolter(id)
  const health = useHolterHealth(id)
  const assignedPatient = usePatient(holter.data?.assignedPatientId ?? undefined)

  useEffect(() => {
    if (
      holter.isError &&
      holter.error &&
      !(isApiError(holter.error) && holter.error.code === 'NOT_FOUND')
    ) {
      toast.error(unwrapError(holter.error), {
        id: 'device-detail-error',
        action: { label: 'Reintentar', onClick: () => void holter.refetch() },
      })
    }
  }, [holter])

  if (holter.isError && isApiError(holter.error) && holter.error.code === 'NOT_FOUND') {
    return (
      <div className="flex flex-col gap-4">
        <BackToList navigate={navigate} />
        <Card className="p-6">
          <EmptyState
            icon={HeartPulse}
            title="Holter no encontrado"
            description="El Holter que estás buscando no existe o ya no está disponible."
            action={
              <Button onClick={() => navigate('/devices')} variant="outline">
                <ArrowLeft className="mr-2 size-4" aria-hidden />
                Volver al listado
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  if (holter.isLoading || !holter.data) {
    return (
      <div className="flex flex-col gap-4">
        <BackToList navigate={navigate} />
        <Card className="flex items-center gap-4 p-6">
          <Skeleton className="size-16 rounded-full" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-4 w-32" />
          </div>
        </Card>
        <Card className="p-6">
          <Spinner label="Cargando dispositivo…" />
        </Card>
      </div>
    )
  }

  const h = holter.data
  const isAssigned = h.assignedPatientId !== null
  const isRetired = h.status === 'retired'

  return (
    <div className="flex flex-col gap-4">
      <BackToList navigate={navigate} />

      {/* Header */}
      <Card className="flex flex-col gap-4 p-6 md:flex-row md:items-start md:justify-between">
        <div className="flex items-start gap-4">
          <div className="flex size-16 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
            <HeartPulse className="size-7" aria-hidden />
          </div>
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-h5 text-gray-900">{h.serial}</h1>
              <HolterStatusBadge status={h.status} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-body3 text-gray-600">
              <span>{h.model}</span>
              <span>·</span>
              <span>Firmware v{h.firmwareVersion ?? '—'}</span>
              <span>·</span>
              <span>Registrado el {formatDate(h.createdAt)}</span>
            </div>
            <div className="text-body3 text-gray-600">
              Última vez visto:{' '}
              {h.lastSeenAt ? (
                <span className="text-gray-900">{formatRelativeTime(h.lastSeenAt)}</span>
              ) : (
                <span className="italic text-gray-500">Nunca</span>
              )}
            </div>
          </div>
        </div>

        {!isRetired && (
          <div className="flex flex-wrap items-center gap-2">
            <ReassignHolterDialog holter={h} />
            {isAssigned && <UnassignHolterDialog holterId={h.id} serial={h.serial} />}
            <KebabMenu
              label={`Acciones para ${h.serial}`}
              actions={[
                {
                  label: 'Editar Holter',
                  icon: Pencil,
                  onSelect: () => setEditOpen(true),
                },
                {
                  label: 'Eliminar Holter',
                  icon: Trash2,
                  variant: 'destructive',
                  onSelect: () => setDeleteOpen(true),
                },
              ]}
            />
          </div>
        )}
      </Card>

      <EditHolterDialog holter={h} open={editOpen} onOpenChange={setEditOpen} />
      <DeleteHolterDialog
        holter={h}
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        navigateOnSuccess
      />

      {/* Paciente asignado */}
      <Card className="p-6">
        {isAssigned ? (
          assignedPatient.isLoading ? (
            <Spinner label="Cargando paciente…" />
          ) : assignedPatient.data ? (
            <div className="flex items-center gap-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
                <User className="size-5" aria-hidden />
              </div>
              <div className="flex flex-col">
                <span className="text-body3 text-gray-600">Asignado a</span>
                <Link
                  to={`/patients/${assignedPatient.data.id}`}
                  className="text-body1 font-medium text-primary-500 hover:underline"
                >
                  {assignedPatient.data.fullName}
                </Link>
              </div>
            </div>
          ) : (
            <p className="text-body3 text-gray-600">
              Asignado al paciente <span className="font-mono">{h.assignedPatientId}</span>
            </p>
          )
        ) : (
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-500">
                <UserX className="size-5" aria-hidden />
              </div>
              <div className="flex flex-col">
                <span className="text-body3 text-gray-600">Sin paciente asignado</span>
                <span className="text-body2 text-gray-900">
                  Asigná este Holter para empezar a monitorear.
                </span>
              </div>
            </div>
            {!isRetired && <ReassignHolterDialog holter={h} />}
          </div>
        )}
      </Card>

      {/* Health */}
      {health.isLoading ? (
        <Card className="p-6">
          <Spinner label="Cargando estado…" />
        </Card>
      ) : health.data ? (
        <HolterHealthCard health={health.data} />
      ) : (
        <Card className="p-6">
          <EmptyState
            icon={HeartPulse}
            title="Sin datos de salud todavía"
            description="Este Holter no se conectó todavía o no envió ningún ping. Aparecerá información en cuanto reporte."
          />
        </Card>
      )}
    </div>
  )
}

function BackToList({ navigate }: { navigate: ReturnType<typeof useNavigate> }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => navigate('/devices')}
      className="w-fit text-gray-600 hover:text-primary-500"
    >
      <ArrowLeft className="mr-1 size-4" aria-hidden />
      Volver al listado
    </Button>
  )
}
