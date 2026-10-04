import { Activity, Play, Power, Square, Trash2 } from 'lucide-react'
import { useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

import type { SimulateAnomalyBody, SimulatedAnomalyType } from '../api/simulatorApi'
import { estimateBatch, formatBytes } from '../defaults'
import { BRIDGE_POST_FRAMES, MAX_BACKLOG_FRAMES } from '../deviceClock'
import { compressionRatio, type VestPhase, type VestState } from '../types'
import { VestTestPanel } from './VestTestPanel'

const PHASE_LABEL: Record<VestPhase, string> = {
  idle: 'Detenido',
  generating: 'Generando señal',
  uploading: 'Subiendo',
  waiting: 'Esperando cadencia',
  done: 'Terminado',
  error: 'Error',
}

const PHASE_TONE: Record<VestPhase, string> = {
  idle: 'bg-gray-100 text-gray-700',
  generating: 'bg-primary-50 text-primary-500',
  uploading: 'bg-primary-50 text-primary-500',
  waiting: 'bg-gray-100 text-gray-700',
  done: 'bg-green-50 text-green-700',
  error: 'bg-red-50 text-red-700',
}

interface VestCardProps {
  vest: VestState
  onRun: () => void
  onStop: () => void
  onRemove: () => void
  onEdit: () => void
  onReboot: () => void
  onSetPlacement: (ok: boolean) => void
  onSimulateAnomaly: (body: SimulateAnomalyBody) => void
  onInjectAnomaly: (type: SimulatedAnomalyType) => void
}

function formatClock(epochMs: number): string {
  return new Date(epochMs).toLocaleString('es-AR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(ms: number): string {
  if (ms >= 86_400_000) return `${(ms / 86_400_000).toFixed(1)} d`
  if (ms >= 3_600_000) return `${(ms / 3_600_000).toFixed(1)} h`
  return `${Math.round(ms / 60_000)} min`
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-body3 text-gray-600">{label}</span>
      <span className="text-body2 font-medium text-gray-900 tabular-nums">{value}</span>
    </div>
  )
}

export function VestCard({
  vest,
  onRun,
  onStop,
  onRemove,
  onEdit,
  onReboot,
  onSetPlacement,
  onSimulateAnomaly,
  onInjectAnomaly,
}: VestCardProps) {
  const [showLog, setShowLog] = useState(false)
  const { config, stats, phase } = vest
  const estimate = estimateBatch(config)
  const ratio = compressionRatio(stats)
  const running = phase === 'generating' || phase === 'uploading' || phase === 'waiting'

  return (
    <Card className="flex flex-col gap-4 p-5">
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Activity className="size-4 text-primary-500" aria-hidden />
            <h3 className="text-body1 font-medium text-gray-900">{config.label}</h3>
          </div>
          <p className="text-body3 text-gray-600">
            {config.serial || 'sin equipo seleccionado'} · {config.batchMinutes} min/lote ·{' '}
            {config.batchCount} lotes
          </p>
        </div>
        <span className={cn('rounded-full px-2 py-0.5 text-body3', PHASE_TONE[phase])}>
          {PHASE_LABEL[phase]}
        </span>
      </header>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="Lotes" value={`${stats.batchesSent}/${config.batchCount}`} />
        <Metric label="Tramas generadas" value={stats.framesGenerated.toLocaleString('es-AR')} />
        <Metric label="Aceptadas" value={stats.framesAccepted.toLocaleString('es-AR')} />
        <Metric
          label="Rechazadas"
          value={`${stats.framesRejected} / dup ${stats.framesDuplicate}`}
        />
        <Metric label="Enviado" value={formatBytes(stats.bytesSent)} />
        <Metric
          label="Ratio real"
          value={
            ratio
              ? `${ratio.toFixed(1)}×`
              : `~${(estimate.uncompressedBytes / estimate.estimatedBytes).toFixed(1)}× est.`
          }
        />
        <Metric label="seq / boot" value={`${stats.lastSeq} / ${stats.bootId}`} />
        {/* Lo que el equipo grabó y el backend todavía no confirmó. Sube cuando
            hay huecos y tiene que volver a bajar: si se queda arriba, el estudio
            dejó de crecer. */}
        <Metric
          label="Flash sin confirmar"
          value={
            `${stats.framesPending.toLocaleString('es-AR')} ` +
            `(${((stats.framesPending / MAX_BACKLOG_FRAMES) * 100).toFixed(1)} %)` +
            (stats.framesLost > 0 ? ` · ${stats.framesLost} perdidas` : '')
          }
        />
        <Metric label="POSTs" value={stats.postsSent.toLocaleString('es-AR')} />
        <Metric
          label="Hora de la señal"
          value={stats.dataCursorEpochMs ? formatClock(stats.dataCursorEpochMs) : 'sin grabar'}
        />
        <Metric label="Uptime" value={formatDuration(stats.uptimeMs)} />
        <Metric
          label="Batería"
          value={stats.batteryPct === null ? '—' : `${Math.round(stats.batteryPct)} %`}
        />
      </div>

      {stats.clockAheadMs > 60_000 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-body3 text-amber-800">
          La señal va {formatDuration(stats.clockAheadMs)} adelantada a la hora real: el puente
          adelanta su reloj para cubrirla y el backend la fecha como hora no verificada. El tope es
          la tolerancia del backend (6 h).
        </p>
      )}
      {stats.backoffWindows > 0 && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-body3 text-amber-800">
          Backoff: las próximas {stats.backoffWindows} ventana(s) no transmiten; la señal se sigue
          grabando en la flash.
        </p>
      )}

      {stats.studyId && (
        <a
          href={`/studies/${stats.studyId}`}
          target="_blank"
          rel="noreferrer"
          className="text-body3 text-primary-500 underline underline-offset-2"
        >
          Ver el estudio en el portal →
        </a>
      )}

      {stats.lastError && (
        <p className="rounded-md bg-red-50 px-3 py-2 text-body3 text-red-700">
          HTTP {stats.lastStatus}: {stats.lastError}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {running ? (
          <Button size="sm" variant="outline" onClick={onStop}>
            <Square className="mr-1 size-4" aria-hidden />
            Detener
          </Button>
        ) : (
          <Button size="sm" onClick={onRun} disabled={!config.serial || !config.apiKey}>
            <Play className="mr-1 size-4" aria-hidden />
            Enviar
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={onEdit}>
          Configurar
        </Button>
        {/* Ciclo de energía. También es la salida si quedaron tramas sin
            confirmar que ya no existen (se borró el almacenamiento del
            navegador): con el bootId nuevo el backend acepta desde el próximo
            lote en vez de esperar para siempre las que faltan. */}
        <Button
          size="sm"
          variant="ghost"
          onClick={onReboot}
          disabled={running}
          title="Avanza el bootId y vuelve t0Ms a 0, como un corte de energía; la flash conserva lo pendiente"
        >
          <Power className="mr-1 size-4" aria-hidden />
          Reiniciar equipo
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowLog((value) => !value)}>
          {showLog ? 'Ocultar log' : `Log (${vest.log.length})`}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="ml-auto text-red-600"
          onClick={onRemove}
          aria-label={`Quitar ${config.label}`}
        >
          <Trash2 className="size-4" aria-hidden />
        </Button>
      </div>

      {(config.frames.corruptCrcPct > 0 ||
        config.frames.dropPct > 0 ||
        config.frames.duplicatePct > 0 ||
        config.frames.rebootAtBatch > 0 ||
        !config.frames.simulated ||
        config.network.invalidApiKey ||
        config.network.unknownSerial ||
        config.network.omitUptime ||
        config.network.noSntp ||
        config.network.lostBootTable ||
        config.network.postFrames !== BRIDGE_POST_FRAMES ||
        config.episodes.length > 0 ||
        config.network.truncateBodyPct > 0) && (
        <div className="flex flex-wrap gap-1">
          {config.frames.corruptCrcPct > 0 && (
            <Badge variant="outline">CRC roto {config.frames.corruptCrcPct}%</Badge>
          )}
          {config.frames.dropPct > 0 && (
            <Badge variant="outline">Huecos {config.frames.dropPct}%</Badge>
          )}
          {config.frames.duplicatePct > 0 && (
            <Badge variant="outline">Duplicadas {config.frames.duplicatePct}%</Badge>
          )}
          {config.frames.rebootAtBatch > 0 && (
            <Badge variant="outline">Reinicio en lote {config.frames.rebootAtBatch}</Badge>
          )}
          {!config.frames.simulated && <Badge variant="outline">Sin bit de simulado</Badge>}
          {config.network.invalidApiKey && <Badge variant="outline">API key inválida</Badge>}
          {config.network.unknownSerial && <Badge variant="outline">Serial inexistente</Badge>}
          {config.network.omitUptime && <Badge variant="outline">Sin uptime</Badge>}
          {config.network.noSntp && <Badge variant="outline">Sin SNTP</Badge>}
          {config.network.lostBootTable && <Badge variant="outline">Sin tabla de arranques</Badge>}
          {config.network.postFrames !== BRIDGE_POST_FRAMES && (
            <Badge variant="outline">{config.network.postFrames} tramas/POST</Badge>
          )}
          {config.episodes.length > 0 && (
            <Badge variant="outline">{config.episodes.length} episodio(s)</Badge>
          )}
          {config.network.truncateBodyPct > 0 && (
            <Badge variant="outline">Corte {config.network.truncateBodyPct}%</Badge>
          )}
        </div>
      )}

      <VestTestPanel
        vest={vest}
        onSetPlacement={onSetPlacement}
        onSimulateAnomaly={onSimulateAnomaly}
        onInjectAnomaly={onInjectAnomaly}
      />

      {showLog && (
        <ul className="max-h-56 overflow-y-auto rounded-md bg-gray-50 p-3 text-body3">
          {vest.log.length === 0 ? (
            <li className="text-gray-600">Sin actividad todavía.</li>
          ) : (
            vest.log.map((entry) => (
              <li
                key={`${entry.at}-${entry.message}`}
                className={cn(
                  'py-0.5 tabular-nums',
                  entry.level === 'error' && 'text-red-700',
                  entry.level === 'warn' && 'text-amber-700',
                  entry.level === 'info' && 'text-gray-700',
                )}
              >
                <span className="text-gray-500">
                  {new Date(entry.at).toLocaleTimeString('es-AR')}{' '}
                </span>
                {entry.message}
              </li>
            ))
          )}
        </ul>
      )}
    </Card>
  )
}
