import { useQuery, useQueryClient } from '@tanstack/react-query'
import * as Notifications from 'expo-notifications'
import { useCallback, useEffect } from 'react'
import { AppState } from 'react-native'

interface PermissionState {
  granted: boolean
  /** `false` cuando el sistema ya no vuelve a preguntar: solo queda Ajustes. */
  canAskAgain: boolean
  request: () => Promise<boolean>
}

const KEY = ['notification-permission'] as const

/**
 * Estado del permiso de notificaciones.
 *
 * Va por TanStack Query y no por `useState`: el permiso es estado que vive
 * afuera de React (lo cambia el sistema operativo), y el paciente puede irse a
 * Ajustes a activarlo y volver. Modelarlo como una query da la revalidación al
 * volver a foreground sin escribir estado a mano dentro de un efecto.
 */
export function useNotificationPermission(): PermissionState {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: KEY,
    queryFn: () => Notifications.getPermissionsAsync(),
    staleTime: 0,
  })

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') void queryClient.invalidateQueries({ queryKey: KEY })
    })
    return () => subscription.remove()
  }, [queryClient])

  const request = useCallback(async () => {
    const status = await Notifications.requestPermissionsAsync()
    queryClient.setQueryData(KEY, status)
    return status.granted
  }, [queryClient])

  return {
    granted: data?.granted ?? false,
    canAskAgain: data?.canAskAgain ?? true,
    request,
  }
}
