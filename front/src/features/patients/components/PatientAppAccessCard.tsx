import { KeyRound, Mail, RefreshCw, Smartphone } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { unwrapError } from '@/lib/api'

import {
  useCreatePatientAppAccount,
  useRegeneratePatientAppPassword,
  useSendPatientPasswordReset,
} from '../hooks/usePatientAppAccess'
import type { Patient } from '../types'
import { ConfirmAppAccessDialog, type AppAccessAction } from './ConfirmAppAccessDialog'
import { PatientCredentialsDialog } from './PatientCredentialsDialog'

interface PatientAppAccessCardProps {
  patient: Patient
}

/**
 * Acceso del paciente a la app móvil.
 *
 * No hay ninguna acción de "ver contraseña" y no es un olvido: Auth0 guarda el
 * hash, así que la contraseña actual no existe en ningún lado desde donde
 * leerla. Cuando el paciente la pierde hay dos caminos honestos — generar una
 * nueva (y dictársela) o mandarle el mail para que la elija él.
 */
export function PatientAppAccessCard({ patient }: PatientAppAccessCardProps) {
  const [password, setPassword] = useState<string | null>(null)
  const [variant, setVariant] = useState<'created' | 'regenerated'>('regenerated')
  const [confirming, setConfirming] = useState<AppAccessAction | null>(null)
  const createAccount = useCreatePatientAppAccount()
  const regenerate = useRegeneratePatientAppPassword()
  const sendReset = useSendPatientPasswordReset()

  const handleCreate = () => {
    createAccount.mutate(patient.id, {
      onSuccess: ({ password: value }) => {
        setConfirming(null)
        setVariant('created')
        setPassword(value)
      },
      onError: (error) => toast.error(unwrapError(error)),
    })
  }

  const handleRegenerate = () => {
    regenerate.mutate(patient.id, {
      onSuccess: ({ password: value }) => {
        setConfirming(null)
        setVariant('regenerated')
        setPassword(value)
      },
      onError: (error) => toast.error(unwrapError(error)),
    })
  }

  const handleReset = () => {
    sendReset.mutate(patient.id, {
      onSuccess: () => {
        setConfirming(null)
        toast.success(`Le mandamos un mail a ${patient.contactEmail}.`)
      },
      onError: (error) => toast.error(unwrapError(error)),
    })
  }

  const CONFIRM = {
    create: { onConfirm: handleCreate, isPending: createAccount.isPending },
    regenerate: { onConfirm: handleRegenerate, isPending: regenerate.isPending },
    reset: { onConfirm: handleReset, isPending: sendReset.isPending },
  } as const

  return (
    <>
      <Card className="flex flex-col gap-5 p-6">
        <header className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-500">
              <Smartphone className="size-4" aria-hidden />
            </div>
            <div className="flex flex-col">
              <h3 className="text-h6 text-gray-900">Acceso a la app</h3>
              <p className="text-body3 text-gray-600">
                {patient.hasAppAccount
                  ? 'Entra con su email o su DNI y una contraseña.'
                  : 'Este paciente todavía no puede usar la app.'}
              </p>
            </div>
          </div>
          <Badge variant={patient.hasAppAccount ? 'success' : 'neutral'}>
            {patient.hasAppAccount ? 'Activo' : 'Sin acceso'}
          </Badge>
        </header>

        {patient.hasAppAccount ? (
          <>
            <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="flex flex-col">
                <dt className="text-body3 text-gray-600">Usuario</dt>
                <dd className="text-body2 font-medium break-all text-gray-900">
                  {patient.contactEmail ?? '—'}
                </dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-body3 text-gray-600">También puede entrar con</dt>
                <dd className="text-body2 font-medium text-gray-900">DNI {patient.dni}</dd>
              </div>
            </dl>

            {/* Los botones miden su contenido: a ancho completo se leían como
                el call-to-action de la ficha, y son dos acciones de mantenimiento
                que además no se pueden deshacer. */}
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => setConfirming('regenerate')}
                disabled={regenerate.isPending}
              >
                <RefreshCw className="mr-1 size-4" aria-hidden />
                {regenerate.isPending ? 'Generando…' : 'Regenerar contraseña'}
              </Button>
              <Button
                variant="outline"
                onClick={() => setConfirming('reset')}
                disabled={sendReset.isPending || !patient.contactEmail}
              >
                <Mail className="mr-1 size-4" aria-hidden />
                {sendReset.isPending ? 'Enviando…' : 'Enviar mail de recuperación'}
              </Button>
            </div>
            <p className="text-helper text-gray-500">
              La contraseña actual no se puede consultar: el sistema solo guarda su huella. Si el
              paciente la perdió, generá una nueva o mandale el mail.
            </p>
          </>
        ) : (
          <div className="flex">
            <Button onClick={() => setConfirming('create')} disabled={createAccount.isPending}>
              <KeyRound className="mr-1 size-4" aria-hidden />
              {createAccount.isPending ? 'Creando…' : 'Crear acceso'}
            </Button>
          </div>
        )}
      </Card>

      {confirming && (
        <ConfirmAppAccessDialog
          action={confirming}
          patientName={patient.fullName}
          contactEmail={patient.contactEmail}
          isPending={CONFIRM[confirming].isPending}
          open
          onOpenChange={(next) => !next && setConfirming(null)}
          onConfirm={CONFIRM[confirming].onConfirm}
        />
      )}

      {password && (
        <PatientCredentialsDialog
          open
          onOpenChange={(next) => !next && setPassword(null)}
          patientName={patient.fullName}
          email={patient.contactEmail ?? ''}
          password={password}
          variant={variant}
        />
      )}
    </>
  )
}
