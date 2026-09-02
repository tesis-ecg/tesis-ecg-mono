import { useQuery } from '@tanstack/react-query'
import { Plus, Play, Square } from 'lucide-react'
import { useMemo, useState } from 'react'

import { EmptyState } from '@/components/EmptyState'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { listDevices } from '@/features/vest-simulator/api/simulatorApi'
import { ScenarioForm } from '@/features/vest-simulator/components/ScenarioForm'
import { VestCard } from '@/features/vest-simulator/components/VestCard'
import { makeVestConfig } from '@/features/vest-simulator/defaults'
import { useVestFleet } from '@/features/vest-simulator/hooks/useVestFleet'
import { Activity } from 'lucide-react'

/**
 * Simulador de chalecos Holter.
 *
 * Solo admin: manda datos reales al endpoint de ingesta y rota API keys de
 * equipos. Tiene entrada en el menú —antes había que saber la URL de memoria,
 * lo que en la práctica lo volvía inencontrable incluso para quien lo
 * necesitaba; el control de acceso lo da `RoleRoute`, no la oscuridad.
 *
 * Anda también en producción, a propósito: la idea es poder validar contra el
 * entorno real, no solo contra el de desarrollo.
 */
export function VestSimulator() {
  const { data: devices = [], isLoading } = useQuery({
    queryKey: ['simulator', 'devices'],
    queryFn: listDevices,
    staleTime: 60_000,
  })

  const {
    vests,
    addVest,
    removeVest,
    updateVest,
    run,
    runAll,
    rebootVest,
    setPlacement,
    simulateAnomaly,
    stop,
    stopAll,
  } = useVestFleet()
  const [editing, setEditing] = useState<string | null>(null)

  const editingVest = useMemo(
    () => vests.find((vest) => vest.config.id === editing) ?? null,
    [vests, editing],
  )
  const anyRunning = vests.some(
    (vest) => vest.phase === 'generating' || vest.phase === 'uploading' || vest.phase === 'waiting',
  )
  const ready = vests.filter((vest) => vest.config.serial && vest.config.apiKey).length

  const handleAdd = () => {
    const config = makeVestConfig()
    addVest(config)
    setEditing(config.id)
  }

  return (
    <div className="flex flex-col gap-4">
      <header className="flex flex-col gap-1">
        <h1 className="text-h4 text-gray-900">Simulador de chalecos</h1>
        <p className="text-body2 text-gray-600">
          Genera señal ECG, la comprime con el mismo codec Rice del firmware y la sube al endpoint
          real de ingesta. Las tramas van marcadas como <strong>dato simulado</strong>, así que los
          estudios que produce quedan identificados como de banco y no como clínicos. Los chalecos
          configurados quedan guardados en este navegador.
        </p>
      </header>

      <Card className="flex flex-wrap items-center gap-3 p-4">
        <Button onClick={handleAdd}>
          <Plus className="mr-1 size-4" aria-hidden />
          Agregar chaleco
        </Button>
        <Button variant="outline" onClick={runAll} disabled={ready === 0 || anyRunning}>
          <Play className="mr-1 size-4" aria-hidden />
          Enviar todos ({ready})
        </Button>
        <Button variant="outline" onClick={stopAll} disabled={!anyRunning}>
          <Square className="mr-1 size-4" aria-hidden />
          Detener todos
        </Button>
        <span className="ml-auto text-body3 text-gray-600">
          {isLoading ? 'Cargando equipos…' : `${devices.length} equipos disponibles`}
        </span>
      </Card>

      {vests.length === 0 ? (
        <Card className="p-0">
          <EmptyState
            icon={Activity}
            title="Sin chalecos configurados"
            description="Agregá uno, elegí un Holter asignado a un paciente y rotá su API key para empezar a mandar lotes."
            action={<Button onClick={handleAdd}>Agregar chaleco</Button>}
          />
        </Card>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          {vests.map((vest) => (
            <VestCard
              key={vest.config.id}
              vest={vest}
              onRun={() => void run(vest.config.id)}
              onStop={() => stop(vest.config.id)}
              onRemove={() => removeVest(vest.config.id)}
              onEdit={() => setEditing(vest.config.id)}
              onReboot={() => rebootVest(vest.config.id)}
              onSetPlacement={(ok) => void setPlacement(vest.config.id, ok)}
              onSimulateAnomaly={(body) => void simulateAnomaly(vest.config.id, body)}
            />
          ))}
        </div>
      )}

      {editingVest && (
        <ScenarioForm
          key={editingVest.config.id}
          open
          config={editingVest.config}
          devices={devices}
          onOpenChange={(open) => !open && setEditing(null)}
          onSave={(changes) => updateVest(editingVest.config.id, changes)}
          onApiKeyRotated={(apiKey) => updateVest(editingVest.config.id, { apiKey })}
        />
      )}
    </div>
  )
}
