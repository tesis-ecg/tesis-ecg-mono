import { useQueryClient } from '@tanstack/react-query'
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import * as patientApi from '@/features/patient/api'
import type { PatientProfile } from '@/features/patient/types'
import { configureSession, setAccessToken } from '@/lib/api'
import { clearRefreshToken, readRefreshToken, saveRefreshToken } from '@/lib/tokens'

interface AuthValue {
  patient: PatientProfile | null
  /** `true` mientras se intenta revivir la sesión guardada al arrancar. */
  isRestoring: boolean
  signIn: (identifier: string, password: string) => Promise<void>
  signOut: () => Promise<void>
  refreshProfile: () => Promise<void>
}

const AuthContext = createContext<AuthValue | null>(null)

export function useAuth(): AuthValue {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth se usó fuera de <AuthProvider>')
  return value
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [patient, setPatient] = useState<PatientProfile | null>(null)
  const [isRestoring, setIsRestoring] = useState(true)
  const queryClient = useQueryClient()
  /**
   * El refresh también vive en un ref: el interceptor de axios corre fuera de
   * React y no puede leer estado que se actualiza con `setState`.
   */
  const refreshTokenRef = useRef<string | null>(null)

  const clearSession = useCallback(async () => {
    refreshTokenRef.current = null
    setAccessToken(null)
    setPatient(null)
    await clearRefreshToken()
    // Los datos en caché son de ESTE paciente. Si la app queda con ellos y
    // entra otro (el familiar que también usa el chaleco), vería el historial
    // del anterior hasta el primer refetch.
    queryClient.clear()
  }, [queryClient])

  // El interceptor necesita saber cómo renovar y qué hacer cuando ya no se
  // puede. Se configura una sola vez, antes de cualquier request.
  useEffect(() => {
    configureSession({
      refresh: async () => {
        const token = refreshTokenRef.current
        if (!token) return null
        try {
          const { accessToken } = await patientApi.refreshAccess(token)
          setAccessToken(accessToken)
          return accessToken
        } catch {
          return null
        }
      },
      onSessionExpired: () => {
        void clearSession()
      },
    })
  }, [clearSession])

  // Al arrancar: si hay refresh guardado, se cambia por un access y se trae el
  // perfil. Cualquier fallo se trata como "no hay sesión" y muestra el login —
  // dejar al paciente en una pantalla de carga eterna sería peor.
  useEffect(() => {
    let cancelled = false
    void (async () => {
      const token = await readRefreshToken()
      if (token) {
        refreshTokenRef.current = token
        try {
          const { accessToken } = await patientApi.refreshAccess(token)
          setAccessToken(accessToken)
          const profile = await patientApi.getMe()
          if (!cancelled) setPatient(profile)
        } catch {
          if (!cancelled) await clearSession()
        }
      }
      if (!cancelled) setIsRestoring(false)
    })()
    return () => {
      cancelled = true
    }
  }, [clearSession])

  const signIn = useCallback(async (identifier: string, password: string) => {
    const session = await patientApi.login(identifier, password)
    refreshTokenRef.current = session.refreshToken
    setAccessToken(session.accessToken)
    await saveRefreshToken(session.refreshToken)
    setPatient(session.patient)
  }, [])

  const signOut = useCallback(async () => {
    try {
      // Le avisa al backend, que incrementa `session_version` y da de baja los
      // push tokens. Si falla (sin señal), igual se cierra en el celular.
      await patientApi.logout()
    } catch {
      // Sin red: la sesión local se cierra igual.
    }
    await clearSession()
  }, [clearSession])

  const refreshProfile = useCallback(async () => {
    setPatient(await patientApi.getMe())
  }, [])

  const value = useMemo(
    () => ({ patient, isRestoring, signIn, signOut, refreshProfile }),
    [patient, isRestoring, signIn, signOut, refreshProfile],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
