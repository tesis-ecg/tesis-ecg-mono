import { useLocalSearchParams, useRouter } from 'expo-router'
import { Check, CircleAlert } from 'lucide-react-native'
import { useEffect, useState } from 'react'
import { FadeIn } from 'react-native-reanimated'

import { Button } from '@/components/ui/Button'
import { ReportModalHeader } from '@/components/ReportModalHeader'
import { Card } from '@/components/ui/Card'
import { ErrorState } from '@/components/ui/ErrorState'
import { Field } from '@/components/ui/Field'
import { Screen } from '@/components/ui/Screen'
import { Select } from '@/components/ui/Select'
import { Spinner } from '@/components/ui/Spinner'
import { Body, Heading, Title } from '@/components/ui/typography'
import { useCatalogs, useCreateReport } from '@/features/patient/hooks'
import { OTHER, toggleSymptom, validateReport } from '@/features/patient/reportSchema'
import { unwrapError } from '@/lib/api'
import { formatDateTime } from '@/lib/format'
import { brandGradient } from '@/lib/gradients'
import * as haptics from '@/lib/haptics'
import { AnimatedView, Text, View } from '@/tw'

/** Cuánto queda en pantalla la confirmación antes de cerrar la hoja. */
const CONFIRMATION_MS = 1200

/**
 * Formulario de la bitácora.
 *
 * Dos entradas al mismo formulario:
 * - desde una notificación (`alertId` + `occurredAt` en los params), y entonces
 *   el momento ya está fijado por el aviso;
 * - desde el botón de Inicio o Historial, y entonces es "ahora".
 *
 * Son dos preguntas y nada más —qué sentiste y qué estabas haciendo— porque un
 * formulario largo es un formulario que el paciente abandona a mitad de camino.
 */
export default function Report() {
  const router = useRouter()
  const params = useLocalSearchParams<{ alertId?: string; occurredAt?: string }>()
  const catalogs = useCatalogs()
  const createReport = useCreateReport()

  const [symptoms, setSymptoms] = useState<string[]>([])
  const [symptomsOther, setSymptomsOther] = useState('')
  const [activity, setActivity] = useState<string | null>(null)
  const [activityOther, setActivityOther] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSent, setIsSent] = useState(false)
  const header = (
    <ReportModalHeader
      title="¿Cómo te sentiste?"
      subtitle={params.occurredAt ? `El ${formatDateTime(params.occurredAt)}` : 'Ahora mismo'}
      onClose={() => router.back()}
    />
  )

  // La hoja se cierra sola después de mostrar la confirmación. El timer se
  // limpia al desmontar por si el paciente la baja con el gesto antes de tiempo.
  useEffect(() => {
    if (!isSent) return
    const timer = setTimeout(() => router.back(), CONFIRMATION_MS)
    return () => clearTimeout(timer)
  }, [isSent, router])

  const handleToggleSymptom = (value: string) => {
    setError(null)
    setSymptoms((current) => toggleSymptom(current, value))
  }

  const handleSubmit = () => {
    const values = { symptoms, symptomsOther, activity: activity ?? '', activityOther, notes }
    const problem = validateReport(values)
    if (problem) {
      haptics.warning()
      setError(problem)
      return
    }

    setError(null)
    createReport.mutate(
      {
        alertId: params.alertId,
        occurredAt: params.occurredAt,
        symptoms,
        symptomsOther: symptomsOther.trim() || null,
        activity: values.activity,
        activityOther: activityOther.trim() || null,
        notes: notes.trim() || null,
      },
      {
        onSuccess: () => {
          // Un registro clínico que se envía sin decir nada deja al paciente sin
          // saber si llegó. La hoja se cerraba y ya está.
          haptics.success()
          setIsSent(true)
        },
        onError: (caught) => {
          haptics.error()
          setError(unwrapError(caught))
        },
      },
    )
  }

  if (isSent) return <SentConfirmation />

  if (catalogs.isLoading) {
    return (
      <Screen topInset={false} fixedHeader={header} keyboardAware>
        <Spinner label="Preparando el formulario…" />
      </Screen>
    )
  }

  // Sin catálogos no hay nada para elegir: antes se dibujaba el formulario con
  // las dos listas vacías y el paciente quedaba atrapado en "elegí al menos una
  // opción" sin ninguna opción a la vista.
  if (catalogs.isError && !catalogs.data) {
    return (
      <Screen topInset={false} fixedHeader={header} keyboardAware contentClassName="gap-6">
        <Card>
          <ErrorState error={catalogs.error} onRetry={() => void catalogs.refetch()} />
        </Card>
      </Screen>
    )
  }

  return (
    <Screen topInset={false} fixedHeader={header} keyboardAware contentClassName="gap-6">
      <View className="gap-3">
        <Heading>Qué sentiste</Heading>
        <Select
          label="Elegí lo que sentiste"
          placeholder="Tocá para elegir"
          hint="Podés elegir más de una opción."
          options={catalogs.data?.symptoms ?? []}
          selected={symptoms}
          onSelect={handleToggleSymptom}
          multiple
        />
        {symptoms.includes(OTHER) && (
          <Field
            label="Contanos qué sentiste"
            value={symptomsOther}
            onChangeText={setSymptomsOther}
            placeholder="Por ejemplo: un pinchazo en el brazo"
            multiline
            className="min-h-[88px]"
          />
        )}
      </View>

      <View className="gap-3">
        <Heading>Qué estabas haciendo</Heading>
        <Select
          label="Elegí qué estabas haciendo"
          placeholder="Tocá para elegir"
          options={catalogs.data?.activities ?? []}
          selected={activity ? [activity] : []}
          onSelect={(value) => {
            setError(null)
            setActivity(value)
          }}
        />
        {activity === OTHER && (
          <Field
            label="Contanos qué estabas haciendo"
            value={activityOther}
            onChangeText={setActivityOther}
            placeholder="Por ejemplo: cortando el pasto"
            multiline
            className="min-h-[88px]"
          />
        )}
      </View>

      <Field
        label="¿Querés agregar algo? (opcional)"
        value={notes}
        onChangeText={setNotes}
        placeholder="Lo que le quieras contar a tu médico"
        multiline
        className="min-h-[100px]"
      />

      {error ? (
        <View className="flex-row gap-2 rounded-[16px] bg-error-100 px-4 py-3">
          <CircleAlert size={20} color="#88271d" />
          <Text className="flex-1 text-[15px] text-error-700">{error}</Text>
        </View>
      ) : null}

      <Button label="Enviar a mi médico" onPress={handleSubmit} loading={createReport.isPending} />
    </Screen>
  )
}

/**
 * Confirmación de envío.
 *
 * Ocupa la hoja entera y se va sola. Es deliberadamente más grande de lo que
 * "hace falta": lo que se acaba de mandar es un dato clínico que va a leer un
 * médico, y el paciente tiene que quedarse tranquilo de que llegó.
 */
function SentConfirmation() {
  return (
    <View className="flex-1 items-center justify-center gap-4 bg-gray-50 px-8">
      <AnimatedView
        entering={FadeIn.duration(220)}
        className="size-20 items-center justify-center rounded-full"
        style={brandGradient}
      >
        <Check size={44} color="#ffffff" />
      </AnimatedView>
      <AnimatedView entering={FadeIn.duration(220).delay(90)} className="gap-2">
        <Title className="text-center">Listo</Title>
        <Body className="text-center text-gray-600">
          Tu médico va a ver esto junto a tu electrocardiograma.
        </Body>
      </AnimatedView>
    </View>
  )
}
