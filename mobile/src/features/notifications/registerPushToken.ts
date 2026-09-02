import Constants from 'expo-constants'
import * as Device from 'expo-device'
import * as Notifications from 'expo-notifications'
import { Platform } from 'react-native'

import { registerPushToken as sendToken } from '@/features/patient/api'
import type { PushPlatform } from '@/features/patient/types'

/**
 * Canal de Android.
 *
 * Tiene que existir **antes** de pedir el token, y su `channelId` tiene que
 * coincidir con el que manda el backend (`app/core/push.py`): si no coincide,
 * Android entrega el aviso en el canal por defecto, sin sonido ni prioridad, y
 * el paciente se entera cuando abre el teléfono por otra cosa.
 */
export async function ensureAndroidChannel(): Promise<void> {
  if (Platform.OS !== 'android') return
  await Notifications.setNotificationChannelAsync('alerts', {
    name: 'Avisos de tu estudio',
    importance: Notifications.AndroidImportance.MAX,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: '#0b2185',
  })
}

function projectId(): string | undefined {
  const extra = Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined
  return extra?.eas?.projectId ?? Constants.easConfig?.projectId
}

/**
 * Registra el celular para recibir avisos. Devuelve el token, o `null`.
 *
 * Nunca tira: que fallen las notificaciones no puede impedir usar la app. El
 * paciente igual ve los avisos pendientes al abrir Inicio — el push acelera el
 * aviso, no es el único camino.
 *
 * No hay corte por `Device.isDevice`. Lo había, y era un bug de banco de
 * pruebas: `isDevice` es `false` **también en el emulador de Android**, que es
 * justamente el entorno donde las push sí funcionan gratis (con Google Play
 * services y credenciales FCM). El corte hacía que el token nunca se
 * registrara y que ningún aviso pudiera llegar, sin ningún error a la vista.
 * En el simulador de iOS el pedido del token falla y lo absorbe el `catch`,
 * pero el permiso ya quedó pedido — que es la condición para que
 * `xcrun simctl push` muestre algo.
 */
export async function registerForPushNotifications(): Promise<string | null> {
  try {
    await ensureAndroidChannel()

    const existing = await Notifications.getPermissionsAsync()
    const status = existing.granted ? existing : await Notifications.requestPermissionsAsync()
    if (!status.granted) return null

    const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId: projectId() })
    await sendToken(token, Platform.OS as PushPlatform)
    return token
  } catch (error) {
    // Silencioso en producción, ruidoso en desarrollo: sin esto, un
    // `projectId` mal configurado, un emulador sin Google Play y un permiso
    // denegado son todos el mismo `null` y no hay por dónde empezar a mirar.
    if (__DEV__) {
      console.warn(
        '[push] no se pudo registrar el token',
        Device.isDevice ? '' : '(emulador/simulador)',
        error,
      )
    }
    return null
  }
}
