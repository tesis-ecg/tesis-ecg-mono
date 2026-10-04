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
import type { ElectrodeKind, MainsEnvironment } from '../codec/signal'
import type { VestConfig } from '../types'
import { EpisodesEditor } from './EpisodesEditor'

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

const ELECTRODE_LABEL: Record<ElectrodeKind, string> = {
  dry: 'Seco (textil)',
  gel: 'Con gel',
}

const ENVIRONMENT_LABEL: Record<MainsEnvironment, string> = {
  clean: 'Limpio (lejos de la red)',
  home: 'Casa (~2 mV pp de 50 Hz)',
  router: 'Al lado del router (~18 mV pp)',
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
            title="Lotes y cadencia"
            hint={`${estimate.samples.toLocaleString('es-AR')} muestras · ~${estimate.estimatedFrames.toLocaleString('es-AR')} tramas · ~${formatBytes(estimate.estimatedBytes)} comprimidos (${formatBytes(estimate.uncompressedBytes)} sin comprimir)`}
          >
            <NumberField
              label="Minutos por lote"
              value={draft.batchMinutes}
              min={1}
              max={180}
              onChange={(batchMinutes) => setDraft((c) => ({ ...c, batchMinutes }))}
              hint="El equipo abre una ventana de envío cada 10 min."
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
                        ? { kind: 'accelerated', factor: 60 }
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
                  <SelectItem value="realtime">
                    Tiempo real (1 lote cada {draft.batchMinutes} min)
                  </SelectItem>
                </SelectContent>
              </Select>
              <span className="text-body3 text-gray-600">
                La primera corrida termina en la hora actual; las siguientes continúan desde el
                último dato grabado.
              </span>
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
                hint="60× → un lote de 10 min cada 10 s."
              />
            )}
          </Section>

          <Section
            title="Paciente y chaleco"
            hint="Ruido y red calibrados con las capturas reales de la placa (canal 2)."
          >
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Electrodos</Label>
              <Select
                value={draft.signal.electrode}
                onValueChange={(electrode) => setSignal({ electrode: electrode as ElectrodeKind })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(ELECTRODE_LABEL).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1">
              <Label className="text-body3">Interferencia de red</Label>
              <Select
                value={draft.signal.environment}
                onValueChange={(environment) =>
                  setSignal({ environment: environment as MainsEnvironment })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {Object.entries(ENVIRONMENT_LABEL).map(([value, label]) => (
                    <SelectItem key={value} value={value}>
                      {label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-body3 text-gray-600">
                Es lo que más mueve la compresión: más red, más tramas por hora.
              </span>
            </div>
            <NumberField
              label="FC de reposo (lpm)"
              value={draft.signal.baseBpm}
              min={30}
              max={200}
              onChange={(baseBpm) => setSignal({ baseBpm })}
            />
            <NumberField
              label="Amplitud de R (µV)"
              value={draft.signal.rAmplitudeUV}
              min={300}
              max={4000}
              step={50}
              onChange={(rAmplitudeUV) => setSignal({ rAmplitudeUV })}
            />
            <NumberField
              label="Respiraciones por minuto"
              value={draft.signal.respirationPerMin}
              min={6}
              max={40}
              onChange={(respirationPerMin) => setSignal({ respirationPerMin })}
              hint="Modulan el RR (arritmia sinusal) y la línea de base."
            />
            <NumberField
              label="Offset de continua (µV)"
              value={draft.signal.dcOffsetUV}
              min={-300000}
              max={300000}
              step={1000}
              onChange={(dcOffsetUV) => setSignal({ dcOffsetUV })}
              hint="Con el chaleco puesto la entrada se para en ~40-50 mV: es DC-acoplada."
            />
            <Toggle
              label="Ritmo circadiano"
              checked={draft.signal.circadian}
              onChange={(circadian) => setSignal({ circadian })}
              hint="La FC baja de madrugada y sube a la tarde, según la hora de la muestra."
            />
            <NumberField
              label="Semilla"
              value={draft.signal.seed}
              onChange={(seed) => setSignal({ seed })}
              hint="Misma semilla, misma señal."
            />
          </Section>

          <Section
            title="Episodios"
            hint="Arritmias y artefactos agendados por lote de la corrida. Quedan en el trazado y los mide el backend."
          >
            <EpisodesEditor
              episodes={draft.episodes}
              batchCount={draft.batchCount}
              batchMinutes={draft.batchMinutes}
              onChange={(episodes) => setDraft((current) => ({ ...current, episodes }))}
            />
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
              hint="Se pierden en el primer envío y el ACK se corta ahí. Quedan en la flash: el equipo las retransmite en el POST siguiente y el estudio se completa igual."
            />
            <NumberField
              label="Reinicio en el lote nº"
              value={draft.frames.rebootAtBatch}
              max={48}
              onChange={(rebootAtBatch) => setFrames({ rebootAtBatch })}
              hint="0 = nunca. Cambia el bootId y t0Ms vuelve a 0; lo pendiente en la flash sale después con la hora del arranque anterior."
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

          <Section
            title="Puente WiFi y red"
            hint="Como el ESP32-C3: POSTs chicos, reintento ante 5xx durante la gracia, y backoff de ventanas 10 → 20 → 40 min."
          >
            <NumberField
              label="Tramas por POST"
              value={draft.network.postFrames}
              min={1}
              max={12000}
              onChange={(postFrames) => setNetwork({ postFrames })}
              hint="El puente real manda 48. Subilo para acelerar contra una API lenta."
            />
            <NumberField
              label="Gracia ante 5xx (s)"
              value={draft.network.graceSeconds}
              max={600}
              onChange={(graceSeconds) => setNetwork({ graceSeconds })}
              hint="El mismo POST se reintenta mientras dure. 0 = no reintentar."
            />
            <NumberField
              label="RSSI (dBm)"
              value={draft.network.rssiDbm}
              min={-127}
              max={0}
              onChange={(rssiDbm) => setNetwork({ rssiDbm })}
            />
            <NumberField
              label="Cortar el cuerpo al (%)"
              value={draft.network.truncateBodyPct}
              max={99}
              onChange={(truncateBodyPct) => setNetwork({ truncateBodyPct })}
              hint="0 = no cortar."
            />
            <Toggle
              label="Puente sin SNTP"
              checked={draft.network.noSntp}
              onChange={(noSntp) => setNetwork({ noSntp })}
              hint="La hora sale del Date de GET /health: fuente none, ±1 s."
            />
            <Toggle
              label="Puente sin tabla de arranques"
              checked={draft.network.lostBootTable}
              onChange={(lostBootTable) => setNetwork({ lostBootTable })}
              hint="El backlog de un arranque anterior sale con la hora del actual: el backend lo fecha como no verificado."
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
