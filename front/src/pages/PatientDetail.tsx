import { Activity, ArrowLeft, FileSearch, Heart, Mail, Phone, UserX } from 'lucide-react'
import { useEffect } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { EmptyState } from '@/components/EmptyState'
import { Spinner } from '@/components/Spinner'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DeviceHealthCard } from '@/features/patients/components/DeviceHealthCard'
import { MetricCard } from '@/features/patients/components/MetricCard'
import { PatientStatusBadge } from '@/features/patients/components/PatientStatusBadge'
import { StudiesTable } from '@/features/patients/components/StudiesTable'
import { usePatient } from '@/features/patients/hooks/usePatient'
import { usePatientDevice } from '@/features/patients/hooks/usePatientDevice'
import { usePatientStudies } from '@/features/patients/hooks/usePatientStudies'
import { usePatientSummary } from '@/features/patients/hooks/usePatientSummary'
import { isApiError, unwrapError } from '@/lib/api'

type TabValue = 'resumen' | 'estudios' | 'dispositivo'

const VALID_TABS: TabValue[] = ['resumen', 'estudios', 'dispositivo']

function getInitials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}

const SEX_LABEL = { M: 'Masculino', F: 'Femenino', X: 'No especificado' } as const

export function PatientDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const tabParam = searchParams.get('tab') as TabValue | null
  const tab: TabValue = tabParam && VALID_TABS.includes(tabParam) ? tabParam : 'resumen'

  const patient = usePatient(id)
  const summary = usePatientSummary(tab === 'resumen' ? id : undefined)
  const studies = usePatientStudies(tab === 'estudios' ? id : undefined)
  const device = usePatientDevice(tab === 'dispositivo' ? id : undefined)

  useEffect(() => {
    if (
      patient.isError &&
      patient.error &&
      !(isApiError(patient.error) && patient.error.code === 'NOT_FOUND')
    ) {
      toast.error(unwrapError(patient.error), {
        id: 'patient-detail-error',
        action: { label: 'Reintentar', onClick: () => void patient.refetch() },
      })
    }
  }, [patient])

  // 404 → estado dedicado.
  if (patient.isError && isApiError(patient.error) && patient.error.code === 'NOT_FOUND') {
    return (
      <div className="flex flex-col gap-4">
        <BackToList navigate={navigate} />
        <Card className="p-6">
          <EmptyState
            icon={UserX}
            title="Paciente no encontrado"
            description="El paciente que estás buscando no existe o ya no está disponible."
            action={
              <Button onClick={() => navigate('/patients')} variant="outline">
                <ArrowLeft className="mr-2 size-4" aria-hidden />
                Volver al listado
              </Button>
            }
          />
        </Card>
      </div>
    )
  }

  if (patient.isLoading || !patient.data) {
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
          <Spinner label="Cargando perfil…" />
        </Card>
      </div>
    )
  }

  const p = patient.data
  const setTab = (next: string) => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev)
        if (next === 'resumen') params.delete('tab')
        else params.set('tab', next)
        return params
      },
      { replace: true },
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <BackToList navigate={navigate} />

      {/* Header */}
      <Card className="flex flex-col gap-4 p-6 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-4">
          <Avatar className="size-16">
            <AvatarFallback className="bg-primary-50 text-primary-500 text-h5 font-semibold">
              {getInitials(p.fullName)}
            </AvatarFallback>
          </Avatar>
          <div className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-h5 text-gray-900">{p.fullName}</h1>
              <PatientStatusBadge status={p.studyStatus} />
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-body3 text-gray-600">
              <span>DNI {p.dni}</span>
              <span>·</span>
              <span>{p.age} años</span>
              <span>·</span>
              <span>{SEX_LABEL[p.sex]}</span>
            </div>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-body3 text-gray-600">
              {p.contactEmail && (
                <span className="inline-flex items-center gap-1">
                  <Mail className="size-3.5" aria-hidden />
                  {p.contactEmail}
                </span>
              )}
              {p.contactPhone && (
                <span className="inline-flex items-center gap-1">
                  <Phone className="size-3.5" aria-hidden />
                  {p.contactPhone}
                </span>
              )}
            </div>
          </div>
        </div>

        <NewStudyButton />
      </Card>

      {/* Tabs */}
      <Tabs value={tab} onValueChange={setTab} className="flex flex-col gap-4">
        <TabsList>
          <TabsTrigger value="resumen">Resumen</TabsTrigger>
          <TabsTrigger value="estudios">Estudios</TabsTrigger>
          <TabsTrigger value="dispositivo">Dispositivo</TabsTrigger>
        </TabsList>

        <TabsContent value="resumen" className="flex flex-col gap-4">
          {summary.isLoading ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Card key={i} className="flex flex-col gap-2 p-5">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-16" />
                  <Skeleton className="h-3 w-32" />
                </Card>
              ))}
            </div>
          ) : summary.isError ? (
            <Card className="p-6">
              <EmptyState
                title="No pudimos cargar las métricas"
                description={unwrapError(summary.error)}
                action={
                  <Button variant="outline" onClick={() => void summary.refetch()}>
                    Reintentar
                  </Button>
                }
              />
            </Card>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <MetricCard
                icon={Heart}
                label={`HR promedio ${summary.data?.windowHours ?? 24}h`}
                value={summary.data?.heartRate?.averageBpm ?? '—'}
                unit={summary.data?.heartRate ? 'bpm' : undefined}
                trend={
                  summary.data?.heartRate
                    ? {
                        value: formatDelta(summary.data.heartRate.deltaBpm, 'bpm vs. ayer'),
                        direction: summary.data.heartRate.trend,
                      }
                    : undefined
                }
              />
              <MetricCard
                icon={Activity}
                label="Eventos detectados"
                value={summary.data?.eventsDetected?.count ?? '—'}
                trend={
                  summary.data?.eventsDetected
                    ? {
                        value: formatDelta(summary.data.eventsDetected.delta, 'vs. ayer'),
                        direction: summary.data.eventsDetected.trend,
                      }
                    : undefined
                }
              />
              <MetricCard
                icon={FileSearch}
                label="Adherencia"
                value={summary.data?.adherencePercent?.value ?? '—'}
                unit={summary.data?.adherencePercent ? '%' : undefined}
                trend={
                  summary.data?.adherencePercent
                    ? {
                        value: formatDelta(summary.data.adherencePercent.deltaPp, 'pp vs. ayer'),
                        direction: summary.data.adherencePercent.trend,
                      }
                    : undefined
                }
              />
            </div>
          )}
          <Card className="flex h-64 flex-col items-center justify-center p-6 text-gray-500">
            <FileSearch className="mb-2 size-8 text-gray-300" aria-hidden />
            <p className="text-body2">Gráfico de ECG / variabilidad — placeholder</p>
            <p className="text-body3 text-gray-400">
              Implementación del visualizador en otro ticket.
            </p>
          </Card>
        </TabsContent>

        <TabsContent value="estudios">
          {studies.isLoading ? (
            <Card className="p-6">
              <Spinner label="Cargando estudios…" />
            </Card>
          ) : studies.isError ? (
            <Card className="p-6">
              <EmptyState
                title="No pudimos cargar los estudios"
                description={unwrapError(studies.error)}
                action={
                  <Button variant="outline" onClick={() => void studies.refetch()}>
                    Reintentar
                  </Button>
                }
              />
            </Card>
          ) : !studies.data || studies.data.items.length === 0 ? (
            <Card className="p-6">
              <EmptyState
                icon={FileSearch}
                title="Sin estudios previos"
                description="Cuando inicies un nuevo estudio aparecerá listado acá."
              />
            </Card>
          ) : (
            <Card className="overflow-hidden p-0">
              <StudiesTable studies={studies.data.items} />
            </Card>
          )}
        </TabsContent>

        <TabsContent value="dispositivo">
          {device.isLoading ? (
            <Card className="p-6">
              <Spinner label="Cargando dispositivo…" />
            </Card>
          ) : device.isError ? (
            <Card className="p-6">
              <EmptyState
                icon={UserX}
                title="Sin dispositivo asignado"
                description={unwrapError(device.error)}
              />
            </Card>
          ) : device.data ? (
            <DeviceHealthCard device={device.data} />
          ) : null}
        </TabsContent>
      </Tabs>
    </div>
  )
}

function formatDelta(value: number, suffix: string): string {
  if (value === 0) return 'Sin cambios'
  const sign = value > 0 ? '+' : '−'
  return `${sign}${Math.abs(value)} ${suffix}`
}

function BackToList({ navigate }: { navigate: ReturnType<typeof useNavigate> }) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() => navigate('/patients')}
      className="w-fit text-gray-600 hover:text-primary-500"
    >
      <ArrowLeft className="mr-1 size-4" aria-hidden />
      Volver al listado
    </Button>
  )
}

function NewStudyButton() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button>Iniciar nuevo estudio</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Iniciar nuevo estudio</DialogTitle>
          <DialogDescription>
            Próximamente. La configuración del estudio (duración, dispositivo, parámetros) se
            implementa en otro ticket.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" type="button">
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
