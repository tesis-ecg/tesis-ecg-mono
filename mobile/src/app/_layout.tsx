import '@/global.css'

import { QueryClientProvider } from '@tanstack/react-query'
import { Stack } from 'expo-router'
import * as SplashScreen from 'expo-splash-screen'
import { StatusBar } from 'expo-status-bar'
import { useEffect } from 'react'
import { GestureHandlerRootView } from 'react-native-gesture-handler'
import { KeyboardProvider } from 'react-native-keyboard-controller'
import { configureReanimatedLogger, ReanimatedLogLevel } from 'react-native-reanimated'
import { SafeAreaProvider } from 'react-native-safe-area-context'

import { AuthProvider, useAuth } from '@/features/auth/AuthContext'
import { NotificationsBridge } from '@/features/notifications/NotificationsBridge'
import { queryClient } from '@/lib/queryClient'

/**
 * Modo estricto de Reanimated apagado.
 *
 * Con `reactCompiler: true` (ver `app.json`), el compilador memoiza el closure
 * de `useAnimatedStyle` y Reanimated lo interpreta como una lectura de
 * `shared value` durante el render: tira "Reading from `value` during component
 * render" en cada render de cada pantalla con animación. Los usos de este
 * repo son los idiomáticos —el `.value` se lee adentro del worklet, que es su
 * lugar—, así que la advertencia es un falso positivo de esa combinación.
 *
 * No es solo ruido en la consola: el toast de LogBox se para encima de la tab
 * bar y tapa la interfaz mientras se desarrolla. `strict: false` es el escape
 * documentado. Los errores de verdad de Reanimated siguen llegando: lo que se
 * apaga es la verificación estricta, no el logger.
 */
configureReanimatedLogger({ level: ReanimatedLogLevel.warn, strict: false })

void SplashScreen.preventAutoHideAsync()

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <KeyboardProvider>
        <SafeAreaProvider>
          <QueryClientProvider client={queryClient}>
            <AuthProvider>
              {/* Vive dentro del provider: registrar el push token exige sesión. */}
              <NotificationsBridge />
              <StatusBar style="dark" />
              <RootNavigator />
            </AuthProvider>
          </QueryClientProvider>
        </SafeAreaProvider>
      </KeyboardProvider>
    </GestureHandlerRootView>
  )
}

/**
 * Rutas protegidas con `Stack.Protected`.
 *
 * El guard es declarativo y no un `router.replace()` dentro de un efecto: con
 * el redirect imperativo, la pantalla protegida alcanza a montarse y dispara
 * sus queries antes de que la navegación ocurra — y esas queries salen sin
 * token y ensucian la sesión con 401.
 */
function RootNavigator() {
  const { patient, isRestoring } = useAuth()

  useEffect(() => {
    if (!isRestoring) void SplashScreen.hideAsync()
  }, [isRestoring])

  // Mientras se revive la sesión guardada, el splash sigue en pantalla: montar
  // el login para esconderlo un instante después es peor que no mostrar nada.
  if (isRestoring) return null

  return (
    <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: '#f6f6f6' } }}>
      <Stack.Protected guard={patient !== null}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="notifications" />
        <Stack.Screen
          name="report"
          options={{ presentation: 'modal', animation: 'slide_from_bottom' }}
        />
        <Stack.Screen
          name="report-response"
          options={{ presentation: 'modal', animation: 'slide_from_bottom' }}
        />
      </Stack.Protected>

      <Stack.Protected guard={patient === null}>
        <Stack.Screen name="login" />
      </Stack.Protected>
    </Stack>
  )
}
