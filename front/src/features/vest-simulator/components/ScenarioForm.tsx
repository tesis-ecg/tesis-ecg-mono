import { KeyRound, RefreshCw } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { unwrapError } from '@/lib/api'

import { rotateApiKey, type SimulatorDevice } from '../api/simulatorApi'
import { estimateBatch, formatBytes } from '../defaults'
import type { AnomalySpan, VestConfig } from '../types'

interface ScenarioFormProps {
  open: boolean
  config: VestConfig
  devices: SimulatorDevice[]
  onOpenChange: (open: boolean) => void
  onSave: (changes: Partial<VestConfig>) => void
  /**
   * Persiste la API key recién rotada **fuera** del borrador.
   *
   * Rotar tiene efecto inmediato en el backend: la key anterior muere ahí. Si
   * la nueva viviera solo en el `draft`, cerrar con Cancelar o Escape la
   * perdería y dejaría al chaleco con una credencial muerta — 401 en cada
   * envío, sin nada en pantalla que explicara por qué. Era la causa concreta
   * del 401 del simulador.
   */
  onApiKeyRotated: (apiKey: string) => void
}

function Section({
  title,
  hint,
  children,
}: {
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3 border-t border-gray-200 pt-4 first:border-t-0 first:pt-0">
      <div>
        <h4 className="text-body2 font-medium text-gray-900">{title}</h4>
        {hint && <p className="text-body3 text-gray-600">{hint}</p>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </section>
  )
}

function NumberField({
  label,
  value,
  onChange,
  min = 0,
  max,
  step = 1,
  hint,
}: {
  label: string
  value: number
  onChange: (value: number) => void
  min?: number
  max?: number
  step?: number
  hint?: string
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-body3">{label}</Label>
      <Input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      {hint && <span className="text-body3 text-gray-600">{hint}</span>}
    </div>
  )
}

function Toggle({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
  hint?: string
}) {
  return (
    <label className="flex cursor-pointer items-start gap-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 size-4 accent-primary-500"
      />
      <span className="flex flex-col">
        <span className="text-body3 text-gray-900">{label}</span>
        {hint && <span className="text-body3 text-gray-600">{hint}</span>}
      </span>
    </label>
  )
}

/** Un solo tramo por anomalía: alcanza para probar y mantiene la UI legible. */
function SpanField({
  label,
  spans,
  onChange,
}: {
  label: string
  spans: AnomalySpan[]
  onChange: (spans: AnomalySpan[]) => void
}) {
  const span = spans[0]
  return (
    <div className="flex flex-col gap-1">
      <Label className="text-body3">{label}</Label>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min={0}
          placeholder="desde (s)"
          value={span?.startSec ?? ''}
          onChange={(event) => {
            const startSec = Number(event.target.value)
            onChange(
              event.target.value === '' ? [] : [{ startSec, durationSec: span?.durationSec || 5 }],
            )
          }}
        />
        <Input
          type="number"
          min={0}
          placeholder="dura (s)"
          value={span?.durationSec ?? ''}
          onChange={(event) =>
            onChange(
              event.target.value === ''
                ? []
                : [{ startSec: span?.startSec ?? 0, durationSec: Number(event.target.value) }],
            )
          }
        />
      </div>
    </div>
  )
}

export function ScenarioForm({
  open,
  config,
  devices,
  onOpenChange,
  onSave,
  onApiKeyRotated,
}: ScenarioFormProps) {
  const [draft, setDraft] = useState<VestConfig>(config)
  const [rotating, setRotating] = useState(false)
  const [confirmingRotate, setConfirmingRotate] = useState(false)
  // Comprime unos segundos de señal para calibrar: no conviene rehacerlo en
  // cada tecla del formulario.
  const estimate = useMemo(
    () => estimateBatch(draft),
    [draft.signal, draft.batchMinutes], // eslint-disable-line react-hooks/exhaustive-deps
  )
  const selectedDevice = devices.find((item) => item.id === draft.deviceId) ?? null

  const setSignal = (changes: Partial<VestConfig['signal']>) =>
    setDraft((current) => ({ ...current, signal: { ...current.signal, ...changes } }))
  const setFrames = (changes: Partial<VestConfig['frames']>) =>
    setDraft((current) => ({ ...current, frames: { ...current.frames, ...changes } }))
  const setNetwork = (changes: Partial<VestConfig['network']>) =>
    setDraft((current) => ({ ...current, network: { ...current.network, ...changes } }))

  const handleRotate = async () => {
    if (!draft.deviceId) return
    setConfirmingRotate(false)
    setRotating(true)
    try {
      const apiKey = await rotateApiKey(draft.deviceId)
      setDraft((current) => ({ ...current, apiKey }))
      // Se guarda YA, sin esperar al botón Guardar: el backend ya invalidó la
      // key anterior, así que descartar esta al cancelar dejaría al chaleco sin
      // ninguna credencial válida.
      onApiKeyRotated(apiKey)
      toast.success('API key rotada y guardada en este chaleco.')
    } catch (error) {
      toast.error(unwrapError(error))
    } finally {
      setRotating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Configurar {config.label}</DialogTitle>
          <DialogDescription>
            Cada chaleco tiene su propio equipo, su propio cursor de seq y su propio reloj.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5 py-2">
          <Section title="Equipo" hint="El lote se resuelve por serial al paciente asignado.">
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Holter</Label>
              <Select
                value={draft.deviceId}
                onValueChange={(deviceId) => {
                  const device = devices.find((item) => item.id === deviceId)
                  setDraft((current) => ({
                    ...current,
                    deviceId,
                    serial: device?.serial ?? '',
                    label: device ? `Chaleco ${device.serial}` : current.label,
                  }))
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Elegir equipo" />
                </SelectTrigger>
                <SelectContent>
                  {devices.map((device) => (
                    <SelectItem key={device.id} value={device.id}>
                      {device.serial}
                      {device.patientName ? ` — ${device.patientName}` : ' — sin paciente'}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-body3">API key</Label>
              <div className="flex gap-2">
                <Input
                  value={draft.apiKey}
                  onChange={(event) =>
                    setDraft((current) => ({ ...current, apiKey: event.target.value }))
                  }
                  placeholder="Pegar o rotar"
                  className="font-mono text-body3"
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setConfirmingRotate(true)}
                  disabled={!draft.deviceId || rotating}
                  title="Rotar la API key del equipo"
                  aria-label="Rotar la API key del equipo"
                >
                  {rotating ? (
                    <RefreshCw className="size-4 animate-spin" aria-hidden />
                  ) : (
                    <KeyRound className="size-4" aria-hidden />
                  )}
                </Button>
              </div>
              <span className="text-body3 text-gray-600">
                {draft.apiKey
                  ? 'Guardada en este chaleco. Sobrevive a recargar la página.'
                  : 'Sin credencial: elegí un equipo y rotá su key para poder enviar.'}
              </span>
            </div>
          </Section>

          <Section
            title="Señal y volumen"
            hint={`${estimate.samples.toLocaleString('es-AR')} muestras · ~${estimate.estimatedFrames.toLocaleString('es-AR')} tramas · ~${formatBytes(estimate.estimatedBytes)} comprimidos (${formatBytes(estimate.uncompressedBytes)} sin comprimir)`}
          >
            <NumberField
              label="Minutos por lote"
              value={draft.batchMinutes}
              min={1}
              max={180}
              onChange={(batchMinutes) => setDraft((c) => ({ ...c, batchMinutes }))}
              hint="El equipo real manda 60."
            />
            <NumberField
              label="Cantidad de lotes"
              value={draft.batchCount}
              min={1}
              max={48}
              onChange={(batchCount) => setDraft((c) => ({ ...c, batchCount }))}
            />
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Cadencia</Label>
              <Select
                value={draft.cadence.kind}
                onValueChange={(kind) =>
                  setDraft((current) => ({
                    ...current,
                    cadence:
                      kind === 'accelerated'
                        ? { kind: 'accelerated', factor: 120 }
                        : { kind: kind as 'instant' | 'realtime' },
                  }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="instant">Instantánea (todo de una)</SelectItem>
                  <SelectItem value="accelerated">Acelerada</SelectItem>
                  <SelectItem value="realtime">Tiempo real (1 lote/hora)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {draft.cadence.kind === 'accelerated' && (
              <NumberField
                label="Factor de aceleración"
                value={draft.cadence.factor}
                min={1}
                max={3600}
                onChange={(factor) =>
                  setDraft((current) => ({ ...current, cadence: { kind: 'accelerated', factor } }))
                }
                hint="120× → un lote de 1 h cada 30 s."
              />
            )}
            <NumberField
              label="Derivaciones"
              value={draft.signal.nChannels}
              min={1}
              max={2}
              onChange={(nChannels) => setSignal({ nChannels })}
            />
            <NumberField
              label="FC base (lpm)"
              value={draft.signal.baseBpm}
              min={30}
              max={200}
              onChange={(baseBpm) => setSignal({ baseBpm })}
            />
            <NumberField
              label="Variabilidad FC (±lpm)"
              value={draft.signal.bpmVariability}
              min={0}
              max={40}
              onChange={(bpmVariability) => setSignal({ bpmVariability })}
            />
            <NumberField
              label="Amplitud QRS (µV)"
              value={draft.signal.qrsAmplitudeUV}
              min={100}
              max={5000}
              step={50}
              onChange={(qrsAmplitudeUV) => setSignal({ qrsAmplitudeUV })}
            />
            <NumberField
              label="Ruido (µV RMS)"
              value={draft.signal.noiseUV}
              min={0}
              max={500}
              onChange={(noiseUV) => setSignal({ noiseUV })}
            />
            <NumberField
              label="Offset de continua (µV)"
              value={draft.signal.baselineOffsetUV}
              min={-300000}
              max={300000}
              step={10000}
              onChange={(baselineOffsetUV) => setSignal({ baselineOffsetUV })}
              hint="Hasta ±300 mV es normal: el front-end es DC-acoplado."
            />
            <NumberField
              label="Semilla"
              value={draft.signal.seed}
              onChange={(seed) => setSignal({ seed })}
              hint="Misma semilla, mismos bytes."
            />
          </Section>

          <Section
            title="Anomalías de señal"
            hint="Por muestra. Son las que después aparecen como eventos en el portal."
          >
            <SpanField
              label="Lead-off (RA/LL suelto)"
              spans={draft.signal.leadOffSpans}
              onChange={(leadOffSpans) => setSignal({ leadOffSpans })}
            />
            <SpanField
              label="RLD off (tierra suelta)"
              spans={draft.signal.rldOffSpans}
              onChange={(rldOffSpans) => setSignal({ rldOffSpans })}
            />
            <SpanField
              label="Saturación del ADC"
              spans={draft.signal.saturatedSpans}
              onChange={(saturatedSpans) => setSignal({ saturatedSpans })}
            />
            <SpanField
              label="Tramo no analizable (SQI 1)"
              spans={draft.signal.unanalyzableSpans}
              onChange={(unanalyzableSpans) => setSignal({ unanalyzableSpans })}
            />
            <div className="flex flex-col gap-1 sm:col-span-2">
              <Label className="text-body3">Marcas de síntoma (segundos, separados por coma)</Label>
              <Input
                value={draft.signal.symptomMarkersSec.join(', ')}
                placeholder="30, 120"
                onChange={(event) =>
                  setSignal({
                    symptomMarkersSec: event.target.value
                      .split(',')
                      .map((piece) => Number(piece.trim()))
                      .filter((value) => Number.isFinite(value) && value >= 0),
                  })
                }
              />
            </div>
          </Section>

          <Section title="Anomalías de trama y protocolo">
            <NumberField
              label="Tramas con CRC roto (%)"
              value={draft.frames.corruptCrcPct}
              max={100}
              onChange={(corruptCrcPct) => setFrames({ corruptCrcPct })}
              hint="Solo en el primer envío. El equipo las retransmite intactas."
            />
            <NumberField
              label="Tramas duplicadas (%)"
              value={draft.frames.duplicatePct}
              max={100}
              onChange={(duplicatePct) => setFrames({ duplicatePct })}
              hint="En cada envío. El backend las colapsa por seq."
            />
            <NumberField
              label="Tramas descartadas (%)"
              value={draft.frames.dropPct}
              max={100}
              onChange={(dropPct) => setFrames({ dropPct })}
              hint="Se pierden en el primer envío y el ACK se corta ahí. Quedan en la SD: el equipo las retransmite en el ciclo siguiente y el estudio se completa igual, con retraso."
            />
            <NumberField
              label="Reinicio en el lote nº"
              value={draft.frames.rebootAtBatch}
              max={48}
              onChange={(rebootAtBatch) => setFrames({ rebootAtBatch })}
              hint="0 = nunca. Cambia el bootId, t0Ms vuelve a 0 y se vacía la SD."
            />
            <Toggle
              label="Enviar las tramas desordenadas"
              checked={draft.frames.shuffle}
              onChange={(shuffle) => setFrames({ shuffle })}
              hint="Pasa al retransmitir tras un corte. No es un hueco."
            />
            <Toggle
              label="Marcar como DATO SIMULADO"
              checked={draft.frames.simulated}
              onChange={(simulated) => setFrames({ simulated })}
              hint="Apagarlo hace que el estudio se archive como clínico."
            />
          </Section>

          <Section title="Red y credenciales">
            <NumberField
              label="Cortar el cuerpo al (%)"
              value={draft.network.truncateBodyPct}
              max={99}
              onChange={(truncateBodyPct) => setNetwork({ truncateBodyPct })}
              hint="0 = no cortar."
            />
            <NumberField
              label="Reintentos ante 5xx"
              value={draft.network.maxRetries}
              max={5}
              onChange={(maxRetries) => setNetwork({ maxRetries })}
            />
            <Toggle
              label="Usar una API key inválida"
              checked={draft.network.invalidApiKey}
              onChange={(invalidApiKey) => setNetwork({ invalidApiKey })}
            />
            <Toggle
              label="Usar un serial inexistente"
              checked={draft.network.unknownSerial}
              onChange={(unknownSerial) => setNetwork({ unknownSerial })}
            />
            <Toggle
              label="Omitir el header de uptime"
              checked={draft.network.omitUptime}
              onChange={(omitUptime) => setNetwork({ omitUptime })}
            />
          </Section>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            onClick={() => {
              onSave(draft)
              onOpenChange(false)
            }}
          >
            Guardar
          </Button>
        </DialogFooter>

        <Dialog open={confirmingRotate} onOpenChange={setConfirmingRotate}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Rotar la API key</DialogTitle>
              <DialogDescription>
                Se genera una credencial nueva para{' '}
                <strong>{selectedDevice?.serial ?? 'el equipo'}</strong> y la anterior deja de
                funcionar al instante. Si el Holter físico está puesto sobre un paciente,{' '}
                <strong>deja de poder subir señal</strong> hasta que se le cargue la nueva.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setConfirmingRotate(false)}>
                Cancelar
              </Button>
              <Button onClick={() => void handleRotate()} disabled={rotating}>
                Rotar de todas formas
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </DialogContent>
    </Dialog>
  )
}
