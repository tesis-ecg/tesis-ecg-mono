import { Activity, AlertTriangle, HeartPulse, Plus, Users } from 'lucide-react'
import { Link } from 'react-router-dom'

import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'

import { AttentionPatientsCard } from '@/features/dashboard/components/AttentionPatientsCard'
import { DeviceWatchdogCard } from '@/features/dashboard/components/DeviceWatchdogCard'
import { FleetGauge } from '@/features/dashboard/components/FleetGauge'
import { RunningStudiesCard } from '@/features/dashboard/components/RunningStudiesCard'
import { SeverityDonut } from '@/features/dashboard/components/SeverityDonut'
import { StatCard } from '@/features/dashboard/components/StatCard'
import { TriageList } from '@/features/dashboard/components/TriageList'
import { WeeklyActivityChart } from '@/features/dashboard/components/WeeklyActivityChart'
import { useDashboardActivity } from '@/features/dashboard/hooks/useDashboardActivity'
import { useDashboardAlerts } from '@/features/dashboard/hooks/useDashboardAlerts'
import { useDashboardKpis } from '@/features/dashboard/hooks/useDashboardKpis'
import { useAuth } from '@/features/auth/AuthContext'

/** Altas de la semana, en palabras: un "0" suelto abajo del número confunde. */
function altasLabel(count: number): string {
  if (count === 0) return 'Sin altas esta semana'
  return count === 1 ? '1 alta esta semana' : `${count} altas esta semana`
}

/** "Buen día" hasta las 13, "buenas tardes" hasta las 20, "buenas noches" después. */
function greeting(hour: number): string {
  if (hour < 13) return 'Buen día'
  if (hour < 20) return 'Buenas tardes'
  return 'Buenas noches'
}

const TODAY = new Intl.DateTimeFormat('es-AR', {
  weekday: 'long',
  day: 'numeric',
  month: 'long',
})

/**
 * La home del portal.
 *
 * Estaba armada como cinco tablas apiladas: todo pesaba lo mismo y no había
 * forma de saber por dónde empezar la mañana. El orden de ahora responde a las
 * cuatro preguntas que un médico se hace al abrir el sistema, y en ese orden:
 *
 * 1. **¿Cómo viene todo?** — la fila de tarjetas, con la forma de la semana al
 *    lado de cada número. Un "12" sin contexto no dice si es un martes normal.
 * 2. **¿Qué miro primero?** — la cola de revisión, ordenada por gravedad y en
 *    dos columnas para que ocho casos prioritarios entren sin alargar la página.
 * 3. **¿Los pacientes están contestando?** — el gráfico de la semana cruza lo
 *    detectado con lo respondido. Es la única vista del sistema que muestra si
 *    la bitácora está llegando, y sin ella un estudio aterriza sin contexto.
 * 4. **¿Qué está corriendo y qué se está por caer?** — chalecos junto a la
 *    actividad; pacientes y estudios en dos listados cortos al final.
 *
 * Todo sale de un solo request: los hooks comparten la `queryKey` del overview.
 */
export function Dashboard() {
  const kpis = useDashboardKpis()
  const alerts = useDashboardAlerts()
  const activity = useDashboardActivity()
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const firstName = user?.fullName?.trim().split(/\s+/)[0]

  const pendingClinicalAlerts =
    activity.data?.pendingBySeverity.reduce((total, bucket) => total + bucket.count, 0) ?? 0
  const pendingDeviceAlerts = Math.max((kpis.data?.pendingAlerts ?? 0) - pendingClinicalAlerts, 0)

  const isLoading = kpis.isLoading
  const hasFailed = (kpis.isError || !kpis.data) && !kpis.isLoading

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-h4 text-gray-900">
            {greeting(new Date().getHours())}
            {firstName ? `, ${firstName}` : ''}
          </h1>
          <p className="text-body2 text-gray-600 first-letter:uppercase">
            {TODAY.format(new Date())}
            {isAdmin ? ' · vista global de todos los médicos' : ''}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link to="/patients">
              <Plus className="mr-1 size-4" aria-hidden />
              Pacientes
            </Link>
          </Button>
          <Button asChild>
            <Link to="/alerts">
              <AlertTriangle className="mr-1 size-4" aria-hidden />
              Ver alertas
            </Link>
          </Button>
        </div>
      </header>

      {hasFailed ? (
        <Card className="p-6">
          <EmptyState
            icon={AlertTriangle}
            title="No se pudo cargar el resumen"
            description="Volvé a intentarlo en unos instantes."
            action={
              <Button variant="outline" onClick={() => void kpis.refetch()}>
                Reintentar
              </Button>
            }
          />
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Alertas pendientes"
              hint="Sin revisar"
              value={kpis.data?.pendingAlerts ?? 0}
              icon={AlertTriangle}
              to="/alerts"
              isLoading={isLoading}
              trend={activity.data?.alertsTrend}
              polarity="more-is-bad"
              days={activity.data?.days}
              metric="alerts"
            />
            <StatCard
              label="Estudios en curso"
              hint="Grabando ahora"
              value={kpis.data?.runningStudies ?? 0}
              icon={Activity}
              to="/studies"
              isLoading={isLoading}
              trend={activity.data?.studiesTrend}
              polarity="neutral"
              days={activity.data?.days}
              metric="studies"
              variant="area"
            />
            {/* Sin serie diaria: "activos" es un stock, y el alta de pacientes
                —que sí es un flujo— va como línea de apoyo en vez de como un
                gráfico que no corresponde a este número. */}
            <StatCard
              label="Pacientes activos"
              hint="Con estudio abierto"
              value={kpis.data?.activePatients ?? 0}
              icon={Users}
              to="/patients"
              isLoading={isLoading}
              footnote={altasLabel(activity.data?.patientsTrend.current ?? 0)}
            />
            <StatCard
              label="Chalecos transmitiendo"
              hint={
                activity.data
                  ? `${activity.data.fleet.transmitting} de ${activity.data.fleet.assigned} asignados`
                  : 'De los asignados'
              }
              value={activity.data ? activity.data.fleet.transmitting : 0}
              icon={HeartPulse}
              to="/devices"
              isLoading={isLoading}
              visual={activity.data ? <FleetGauge fleet={activity.data.fleet} /> : undefined}
            />
          </div>

          <TriageList alerts={alerts.data} isLoading={alerts.isLoading} isError={alerts.isError} />

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
            <Card className="flex flex-col gap-1 p-5 md:col-span-2 xl:col-span-2">
              <h2 className="text-h6 text-gray-900">Actividad de la semana</h2>
              <p className="text-body3 mb-2 max-w-3xl text-gray-600">
                Lo que el sistema detectó y lo que los pacientes respondieron desde la app. Un día
                con detecciones y sin respuestas es señal para llamar.
              </p>
              {activity.isLoading ? (
                <Skeleton className="h-56 w-full" />
              ) : activity.data ? (
                <WeeklyActivityChart days={activity.data.days} />
              ) : (
                <EmptyState icon={Activity} title="No se pudo cargar" />
              )}
            </Card>
            <Card className="flex flex-col gap-4 p-5">
              <div className="flex flex-col gap-1">
                <h2 className="text-h6 text-gray-900">Alertas por gravedad</h2>
                <p className="text-body3 text-gray-600">Distribución de las alertas sin revisar.</p>
              </div>
              {activity.isLoading ? (
                <Skeleton className="h-56 w-full" />
              ) : activity.data ? (
                <SeverityDonut
                  buckets={activity.data.pendingBySeverity}
                  deviceAlerts={pendingDeviceAlerts}
                />
              ) : (
                <EmptyState icon={AlertTriangle} title="No se pudo cargar" />
              )}
            </Card>
            <DeviceWatchdogCard />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <AttentionPatientsCard />
            <RunningStudiesCard />
          </div>
        </>
      )}
    </div>
  )
}
