import * as Notifications from 'expo-notifications'
import { useRouter } from 'expo-router'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'

import { useAuth } from '@/features/auth/AuthContext'
import { patientKeys } from '@/features/patient/hooks'
import { registerForPushNotifications } from './registerPushToken'
import { routeForNotification, type NotificationData } from './routeForNotification'

/**
 * Comportamiento en primer plano.
 *
 * Se muestra el banner aunque la app esté abierta: los avisos son pocos y
 * accionables, y esconderlo cuando el paciente está en otra pantalla de la app
 * significa que se lo pierde.
 */
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldPlaySound: true,
    shouldSetBadge: false,
    shouldShowBanner: true,
    shouldShowList: true,
  }),
})

/**
 * Une las notificaciones con la navegación y con la sesión.
 *
 * No renderiza nada: vive dentro de `AuthProvider` porque registrar el token
 * exige estar logueado, y dentro del router porque tocar un aviso tiene que
 * abrir el formulario ya anclado a ese momento.
 */
export function NotificationsBridge() {
  const { patient } = useAuth()
  const router = useRouter()
  const queryClient = useQueryClient()
  /** El token de Expo rota, así que se re-registra en cada arranque con sesión. */
  const registeredFor = useRef<string | null>(null)
  const lastResponse = Notifications.useLastNotificationResponse()
  const handledResponse = useRef<string | null>(null)

  useEffect(() => {
    if (!patient) {
      registeredFor.current = null
      return
    }
    if (registeredFor.current === patient.id) return
    registeredFor.current = patient.id
    void registerForPushNotifications()
  }, [patient])

  /**
   * Un push que llega con la app abierta tiene que actualizar lo que se está
   * mirando, no solo dibujar el banner.
   *
   * Las dos queries y no solo la de avisos: el aviso de chaleco mal colocado
   * lleva a *Dispositivo*, y el estado de la colocación vive en la query del
   * dispositivo. Invalidando solo `alerts`, la pantalla a la que el propio
   * aviso manda al paciente seguía dibujando el estado cacheado.
   */
  useEffect(() => {
    if (!patient) return
    const subscription = Notifications.addNotificationReceivedListener(() => {
      void queryClient.invalidateQueries({ queryKey: patientKeys.alerts })
      void queryClient.invalidateQueries({ queryKey: patientKeys.device })
    })
    return () => subscription.remove()
  }, [patient, queryClient])

  useEffect(() => {
    if (!patient || !lastResponse) return
    const responseId = lastResponse.notification.request.identifier
    if (handledResponse.current === responseId) return
    handledResponse.current = responseId

    if (lastResponse.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) {
      Notifications.clearLastNotificationResponse()
      return
    }
    const data = lastResponse.notification.request.content.data as NotificationData
    const route = routeForNotification(data)
    // Un aviso tocado desde el fondo no pasa por el listener de recepción, así
    // que la pantalla a la que lleva se dibujaría con lo que haya en caché.
    void queryClient.invalidateQueries({ queryKey: patientKeys.alerts })
    void queryClient.invalidateQueries({ queryKey: patientKeys.device })

    if (route.pathname === '/(tabs)/device') {
      router.navigate(route.pathname)
    } else if (route.pathname === '/notifications') {
      router.push(route.pathname)
    } else {
      router.push({ pathname: route.pathname, params: route.params })
    }
    Notifications.clearLastNotificationResponse()
  }, [lastResponse, patient, router, queryClient])

  return null
}
