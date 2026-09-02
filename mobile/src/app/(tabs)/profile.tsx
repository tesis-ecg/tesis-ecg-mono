import { useState } from 'react'
import { Linking } from 'react-native'
import { Bell, BriefcaseMedical, LogOut, Mail, Phone } from 'lucide-react-native'

import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { ConfirmDialog } from '@/components/ui/ConfirmDialog'
import { InfoRow } from '@/components/ui/InfoRow'
import { Refresh } from '@/components/ui/Refresh'
import { Screen } from '@/components/ui/Screen'
import { Toggle } from '@/components/ui/Toggle'
import { Body, Caption, Title } from '@/components/ui/typography'
import { useAuth } from '@/features/auth/AuthContext'
import { registerForPushNotifications } from '@/features/notifications/registerPushToken'
import { useNotificationPermission } from '@/features/notifications/useNotificationPermission'
import { calculateAge } from '@/lib/format'
import { brandGradient } from '@/lib/gradients'
import * as haptics from '@/lib/haptics'
import { enterAt } from '@/lib/motion'
import { AnimatedView, Text, View } from '@/tw'

const SEX_LABEL = { M: 'Masculino', F: 'Femenino', X: 'No especificado' } as const

export default function Profile() {
  const { patient, signOut, refreshProfile } = useAuth()
  const permission = useNotificationPermission()
  const [isSigningOut, setIsSigningOut] = useState(false)
  const [isConfirmingSignOut, setIsConfirmingSignOut] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)

  /**
   * El perfil lo edita el médico desde el portal, no el paciente. Sin este
   * refresh, un teléfono corregido en el consultorio no llegaba nunca a la app:
   * `refreshProfile` existía en el contexto de sesión pero no lo llamaba nadie,
   * así que el dato viejo sobrevivía hasta el próximo login.
   */
  const handleRefresh = async () => {
    setIsRefreshing(true)
    try {
      await refreshProfile()
      haptics.tap()
    } catch {
      // Sin señal el perfil se queda como estaba; no hay nada que avisar.
    }
    setIsRefreshing(false)
  }

  /**
   * El interruptor de notificaciones.
   *
   * Los tres caminos son del sistema operativo y no de la app, que no puede
   * prender ni apagar el permiso por su cuenta:
   *
   * - Sin decidir todavía: sale el modal del sistema. Si el paciente lo
   *   rechaza, el interruptor se queda apagado solo —refleja el permiso, no el
   *   toque— y no hay nada que revertir.
   * - Ya rechazado (`canAskAgain: false`): el sistema no vuelve a preguntar
   *   nunca más, así que el único camino es Ajustes y hay que llevarlo ahí.
   * - Ya activadas: apagarlas también se hace en Ajustes. Un interruptor
   *   encendido que no se puede apagar sería una palanca rota.
   */
  const handleNotificationsToggle = () => {
    if (!permission.granted && permission.canAskAgain) {
      // El registro del token va acá y no solo en el arranque de la sesión:
      // `NotificationsBridge` lo intenta una vez por sesión, y si el paciente
      // rechazó el permiso esa vez, activarlo después dejaba el celular sin
      // token registrado hasta el siguiente arranque de la app — con las
      // notificaciones "encendidas" en pantalla y ningún aviso llegando.
      void permission.request().then((granted) => {
        if (granted) void registerForPushNotifications()
      })
      return
    }
    void Linking.openSettings()
  }

  const handleSignOut = () => {
    setIsSigningOut(true)
    void signOut().finally(() => {
      setIsSigningOut(false)
      setIsConfirmingSignOut(false)
    })
  }

  const age = calculateAge(patient?.birthDate ?? null)

  return (
    <Screen
      fixedHeader={<Title>Mi perfil</Title>}
      refreshControl={<Refresh refreshing={isRefreshing} onRefresh={() => void handleRefresh()} />}
    >

      <AnimatedView entering={enterAt(0)}>
        <Card className="items-center gap-3 py-8">
          <View
            className="size-20 items-center justify-center rounded-full"
            style={brandGradient}
          >
            <Text className="text-[30px] font-bold text-white">
              {(patient?.fullName ?? '?').trim().charAt(0).toUpperCase()}
            </Text>
          </View>
          <Title className="text-center">{patient?.fullName}</Title>
          <Caption>
            DNI {patient?.dni}
            {age !== null ? ` · ${age} años` : ''}
            {patient?.sex ? ` · ${SEX_LABEL[patient.sex]}` : ''}
          </Caption>
        </Card>
      </AnimatedView>

      {patient?.doctor && (
        <AnimatedView entering={enterAt(1)}>
          <Card className="gap-3">
            <Caption>Tu médico</Caption>
            <View className="flex-row items-center gap-3">
              <View className="size-11 items-center justify-center rounded-full bg-primary-50">
                <BriefcaseMedical size={20} color="#0b2185" />
              </View>
              <View className="flex-1">
                <Body className="font-semibold">{patient.doctor.fullName}</Body>
                {patient.doctor.email ? <Caption>{patient.doctor.email}</Caption> : null}
              </View>
            </View>
          </Card>
        </AnimatedView>
      )}

      <AnimatedView entering={enterAt(2)}>
        <Card className="gap-3">
          <View className="flex-row items-center gap-3">
            <View className="size-11 items-center justify-center rounded-full bg-primary-50">
              <Bell size={20} color="#0b2185" />
            </View>
            <Body className="flex-1 font-semibold">Notificaciones</Body>
            <Toggle
              value={permission.granted}
              onPress={handleNotificationsToggle}
              accessibilityLabel="Notificaciones"
            />
          </View>
          <Body className="text-gray-700">
            {permission.granted
              ? 'Te vamos a avisar si el chaleco queda mal colocado o si el sistema detecta algo para revisar.'
              : 'Sin notificaciones no vas a enterarte si el chaleco queda mal colocado. Se puede perder el estudio de ese día.'}
          </Body>
          {!permission.granted && !permission.canAskAgain ? (
            <Caption>
              Ya rechazaste el permiso, así que el teléfono no lo vuelve a preguntar: el
              interruptor te lleva a los ajustes de la app.
            </Caption>
          ) : null}
        </Card>
      </AnimatedView>

      <AnimatedView entering={enterAt(3)}>
        <Card className="gap-1">
          <Caption className="pb-2">Datos de contacto</Caption>
          <InfoRow icon={Mail} label="Email" value={patient?.email ?? '—'} />
          <InfoRow
            icon={Phone}
            label="Teléfono"
            value={patient?.phone ?? '—'}
            divider={false}
          />
          <Body className="pt-3 text-[15px] text-gray-600">
            Si alguno de estos datos cambió, avisale a tu médico para que lo actualice.
          </Body>
        </Card>
      </AnimatedView>

      <Button
        label="Cerrar sesión"
        variant="danger"
        onPress={() => setIsConfirmingSignOut(true)}
        loading={isSigningOut}
      />

      {/*
        La confirmación es propia y no `Alert.alert`: el alert del sistema saca
        el texto a 13 pt con botones de 30, la mitad del piso de esta app, y se
        dibuja distinto en cada plataforma. Ver `ui/ConfirmDialog`.
      */}
      <ConfirmDialog
        visible={isConfirmingSignOut}
        icon={LogOut}
        title="¿Cerrar sesión?"
        message="Vas a tener que volver a entrar con tu DNI o tu email para ver tus avisos y registrar cómo te sentís."
        confirmLabel="Cerrar sesión"
        cancelLabel="Quedarme"
        loading={isSigningOut}
        onConfirm={handleSignOut}
        onClose={() => setIsConfirmingSignOut(false)}
      />
    </Screen>
  )
}
