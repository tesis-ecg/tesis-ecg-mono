import { KeyRound, RefreshCw, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { CopyableSecret } from '@/components/CopyableSecret'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { isApiError, unwrapError } from '@/lib/api'
import { formatDateTime } from '@/lib/time'

import { useHolterApiKey, useRotateHolterApiKey } from '../hooks/useHolterApiKey'
import type { Holter } from '../types'

interface HolterApiKeyCardProps {
  holter: Holter
  /** Equipo recién creado: la key se muestra sin que haya que pedirla. */
  defaultRevealed?: boolean
}

/**
 * La API key del equipo, para el admin que va a grabarla en el firmware.
 *
 * Arranca oculta y no se pide al servidor hasta que alguien la revela: el
 * backend audita cada lectura, y traerla al montar dejaría un evento
 * por cada visita a esta pantalla.
 */
export function HolterApiKeyCard({ holter, defaultRevealed = false }: HolterApiKeyCardProps) {
  const [requested, setRequested] = useState(defaultRevealed)
  const [confirmingRotate, setConfirmingRotate] = useState(false)
  // Remonta `CopyableSecret` después de rotar, para que la key nueva aparezca
  // ya revelada: rotar existe justamente para grabar la nueva en el firmware.
  const [rotations, setRotations] = useState(0)
  const apiKey = useHolterApiKey(holter.id, requested)
  const rotate = useRotateHolterApiKey()

  // `code` es el código normalizado del cliente ('CONFLICT'); el del backend
  // viaja en `serverCode`.
  const unavailable = isApiError(apiKey.error) && apiKey.error.serverCode === 'API_KEY_UNAVAILABLE'
  const failed = apiKey.isError && !unavailable

  const handleRotate = () => {
    rotate.mutate(holter.id, {
      onSuccess: () => {
        setConfirmingRotate(false)
        setRequested(true)
        setRotations((current) => current + 1)
        toast.success(`API key de ${holter.serial} rotada.`)
      },
      onError: (error) => toast.error(unwrapError(error)),
    })
  }

  return (
    <>
      <Card className="flex flex-col gap-4 p-6">
        <header className="flex items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
            <KeyRound className="size-4" aria-hidden />
          </div>
          <div className="flex flex-col">
            <h3 className="text-h6 text-gray-900">API key del dispositivo</h3>
            <p className="text-body3 text-gray-600">
              Es la credencial que el firmware de este chaleco usa para subir la señal de sus
              estudios. Tratala como una contraseña: quien la tenga puede enviar datos como este
              equipo.
            </p>
          </div>
        </header>

        {unavailable ? (
          <div className="flex gap-3 rounded-lg border border-warning-300 bg-warning-100 px-4 py-3">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning-700" aria-hidden />
            <p className="text-body3 text-gray-900">
              La key de este equipo se generó antes de que se pudieran volver a leer, así que no hay
              forma de recuperarla. Generá una nueva y cargala en el chaleco.
            </p>
          </div>
        ) : failed ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
            <span className="text-body3 text-gray-900">{unwrapError(apiKey.error)}</span>
            <Button variant="outline" size="sm" onClick={() => void apiKey.refetch()}>
              Reintentar
            </Button>
          </div>
        ) : (
          <CopyableSecret
            key={rotations}
            maskable
            defaultRevealed={defaultRevealed || rotations > 0}
            value={apiKey.data?.apiKey ?? null}
            loading={apiKey.isFetching}
            onRequestValue={() => setRequested(true)}
            label="Copiar API key"
          />
        )}

        <footer className="flex flex-wrap items-center justify-between gap-3">
          <span className="text-body3 text-gray-600">
            {apiKey.data
              ? `Generada el ${formatDateTime(apiKey.data.rotatedAt)}.`
              : 'Rotar la key deja fuera de servicio al chaleco que tenga cargada la actual.'}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setConfirmingRotate(true)}
            disabled={rotate.isPending}
          >
            <RefreshCw className="mr-1 size-4" aria-hidden />
            {rotate.isPending ? 'Rotando…' : 'Rotar API key'}
          </Button>
        </footer>
      </Card>

      <Dialog open={confirmingRotate} onOpenChange={setConfirmingRotate}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rotar API key</DialogTitle>
            <DialogDescription>
              Se genera una key nueva para <strong>{holter.serial}</strong> y la actual deja de
              funcionar en el acto. Si el chaleco ya está puesto sobre un paciente, deja de poder
              subir señal hasta que le grabes la nueva.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="outline">Volver</Button>
            </DialogClose>
            <Button onClick={handleRotate} disabled={rotate.isPending}>
              <RefreshCw className="mr-1 size-4" aria-hidden />
              {rotate.isPending ? 'Rotando…' : 'Rotar API key'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
