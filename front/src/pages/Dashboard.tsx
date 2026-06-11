import { Activity, AlertTriangle, Users } from 'lucide-react'

import { EmptyState } from '@/components/EmptyState'
import { Skeleton } from '@/components/ui/skeleton'

import { AttentionPatientsCard } from '@/features/dashboard/components/AttentionPatientsCard'
import { DeviceWatchdogCard } from '@/features/dashboard/components/DeviceWatchdogCard'
import { KpiCard } from '@/features/dashboard/components/KpiCard'
import { PendingAlertsCard } from '@/features/dashboard/components/PendingAlertsCard'
import { RunningStudiesCard } from '@/features/dashboard/components/RunningStudiesCard'
import { useDashboardKpis } from '@/features/dashboard/hooks/useDashboardKpis'

export function Dashboard() {
  const { data, isLoading, isError } = useDashboardKpis()

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Bienvenido</h1>
        <p className="text-body2 text-gray-600">Resumen del sistema de telemetría cardíaca.</p>
      </header>

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : isError || !data ? (
        <EmptyState
          icon={AlertTriangle}
          title="No se pudieron cargar los KPIs"
          description="Volvé a intentarlo en unos instantes."
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <KpiCard
            label="Pacientes activos"
            value={data.activePatients}
            icon={Users}
            delta={data.activePatientsDelta}
          />
          <KpiCard
            label="Alertas pendientes"
            value={data.pendingAlerts}
            icon={AlertTriangle}
            delta={data.pendingAlertsDelta}
          />
          <KpiCard
            label="Estudios en curso"
            value={data.runningStudies}
            icon={Activity}
            delta={data.runningStudiesDelta}
          />
        </div>
      )}

      <PendingAlertsCard />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <AttentionPatientsCard />
        <RunningStudiesCard />
      </div>

      <DeviceWatchdogCard />
    </div>
  )
}
