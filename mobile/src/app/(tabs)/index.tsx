import { useRouter } from 'expo-router'
import { Bell, ChevronRight } from 'lucide-react-native'
import { useState } from 'react'

import { Badge } from '@/components/ui/Badge'
import { PendingAlerts } from '@/components/PendingAlerts'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { ErrorState } from '@/components/ui/ErrorState'
import { Meter } from '@/components/ui/Meter'
import { Refresh } from '@/components/ui/Refresh'
import { Screen } from '@/components/ui/Screen'
import { Body, Caption, Display, Heading } from '@/components/ui/typography'
import { useAuth } from '@/features/auth/AuthContext'
import { DEVICE_STATE } from '@/features/patient/deviceMeta'
import { useDevice, usePendingAlerts } from '@/features/patient/hooks'
import { formatRelativeTime } from '@/lib/format'
import * as haptics from '@/lib/haptics'
import { enterAt, usePressScale } from '@/lib/motion'
import { AnimatedPressable, AnimatedView, Text, View } from '@/tw'

function firstName(fullName: string): string {
  return fullName.trim().split(/\s+/)[0] ?? fullName
}

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Buen día'
  if (hour < 20) return 'Buenas tardes'
  return 'Buenas noches'
}

export default function Home() {
  const router = useRouter()
  const { patient } = useAuth()
  const device = useDevice()
  const alerts = usePendingAlerts()
  const [isRefreshing, setIsRefreshing] = useState(false)
  const devicePress = usePressScale()
  const bellPress = usePressScale()

  const handleRefresh = async () => {
    setIsRefreshing(true)
    await Promise.all([device.refetch(), alerts.refetch()])
    haptics.tap()
    setIsRefreshing(false)
  }

  const meta = DEVICE_STATE[device.data?.state ?? 'none']
  const pending = alerts.data?.items ?? []
  // El total lo cuenta el backend sobre todos los avisos, no sobre los tres que
  // trae esta consulta: el badge tiene que decir cuántos hay, no cuántos entran.
  const pendingTotal = alerts.data?.pendingTotal ?? 0
  const DeviceIcon = meta.icon

  /*
    El saludo y la campana quedan fijos, como en Notificaciones: el contador de
    avisos sin responder es lo que trae al paciente a la app, y con el header
    scrolleando desaparecía apenas bajaba a mirar el chaleco.
  */
  const header = (
    <View className="flex-row items-start justify-between gap-4">
      <View className="flex-1 gap-1">
        <Caption>{greeting()}</Caption>
        <Display>{patient ? firstName(patient.fullName) : ''}</Display>
      </View>
      {/*
        Se hunde al tocarlo como la card del chaleco y como los botones: es la
        única cosa tocable de la pantalla que no lo hacía, y esa diferencia se
        nota — el toque parecía no haber entrado hasta que aparecía la pantalla
        de Notificaciones.
      */}
      <AnimatedPressable
        accessibilityRole="button"
        accessibilityLabel={
          pendingTotal > 0
            ? `Ver notificaciones. ${pendingTotal} sin responder`
            : 'Ver notificaciones'
        }
        onPress={() => {
          haptics.tap()
          router.push('/notifications')
        }}
        onPressIn={bellPress.onPressIn}
        onPressOut={bellPress.onPressOut}
        style={bellPress.style}
        className="relative size-12 items-center justify-center rounded-full bg-white"
      >
        <Bell size={24} color="#172126" />
        {pendingTotal > 0 ? (
          <View className="absolute -top-1 -right-1 min-w-5 items-center justify-center rounded-full bg-error-500 px-1.5 py-0.5">
            <Text className="text-[11px] font-bold text-white">
              {pendingTotal > 9 ? '9+' : pendingTotal}
            </Text>
          </View>
        ) : null}
      </AnimatedPressable>
    </View>
  )

  return (
    <Screen
      fixedHeader={header}
      refreshControl={<Refresh refreshing={isRefreshing} onRefresh={() => void handleRefresh()} />}
    >

      {pending.length > 0 && (
        <AnimatedView entering={enterAt(0)}>
          <PendingAlerts
            alerts={pending}
            onOpen={(alert) =>
              router.push({
                pathname: '/report',
                params: { alertId: alert.id, occurredAt: alert.detectedAt },
              })
            }
          />
        </AnimatedView>
      )}

      {/*
        El error va antes que el estado del equipo: sin esto, una request que
        falla dejaba `state` en `none` y la pantalla le decía al paciente que no
        tiene chaleco cuando lo único que pasaba era que el celular no tenía
        señal. Solo aplica cuando no hay datos en caché — si los hay, mostrar el
        estado de hace un rato es más útil que un cartel de error.
      */}
      {device.isError && !device.data ? (
        <Card>
          <ErrorState error={device.error} onRetry={() => void device.refetch()} />
        </Card>
      ) : (
        <AnimatedView entering={enterAt(pending.length > 0 ? 1 : 0)}>
          {/*
            El resumen del chaleco es la puerta a la pestaña Dispositivo, donde
            está el detalle completo. Sin el chevrón la card se leía como un
            cartel informativo y nadie la tocaba, aunque respondiera al toque.
          */}
          <AnimatedPressable
            accessibilityRole="button"
            accessibilityLabel={`Mi chaleco: ${meta.label}. Ver el detalle del dispositivo`}
            onPress={() => {
              haptics.tap()
              // `navigate` y no `push`: la pestaña ya existe en el navegador y
              // apilarla otra vez deja al paciente teniendo que volver dos veces.
              router.navigate('/device')
            }}
            onPressIn={devicePress.onPressIn}
            onPressOut={devicePress.onPressOut}
            style={devicePress.style}
          >
            <Card className="gap-4">
              <View className="flex-row items-center justify-between gap-2">
                <Heading className="flex-1">Mi chaleco</Heading>
                <Badge
                  label={meta.label}
                  tone={meta.tone}
                  live={device.data?.state === 'recording'}
                  className="shrink-0"
                />
                <ChevronRight size={20} color="#727f87" />
              </View>
              <View className="flex-row items-start gap-3">
                <DeviceIcon size={24} color="#727f87" />
                <Body className="flex-1 text-gray-700">{meta.description}</Body>
              </View>

              {device.data?.hasDevice && (
                <View className="flex-row gap-3 border-t border-gray-100 pt-4">
                  <View className="flex-1 gap-2">
                    <Caption>Batería</Caption>
                    <Body className="font-semibold">
                      {device.data.batteryPercent === null ? '—' : `${device.data.batteryPercent}%`}
                    </Body>
                    <Meter value={device.data.batteryPercent} />
                  </View>
                  <View className="flex-1 gap-2">
                    <Caption>Último envío</Caption>
                    <Body className="font-semibold">
                      {formatRelativeTime(device.data.lastDataReceivedAt)}
                    </Body>
                  </View>
                </View>
              )}
            </Card>
          </AnimatedPressable>
        </AnimatedView>
      )}

      <AnimatedView entering={enterAt(pending.length > 0 ? 2 : 1)}>
        <Card className="gap-3">
          <Heading>¿Sentís algo?</Heading>
          <Body className="text-gray-700">
            Anotalo apenas te pase. Tu médico lo va a ver junto al latido exacto de ese momento.
          </Body>
          <Button
            label="Registrar cómo me siento"
            onPress={() => router.push('/report')}
            className="mt-1"
          />
        </Card>
      </AnimatedView>
    </Screen>
  )
}
