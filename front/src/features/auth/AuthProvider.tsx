import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { setAuthHandler } from '@/lib/api'

import { loginRequest, logoutRequest, meRequest } from './api'
import { AuthContext, type AuthContextValue } from './AuthContext'
import { clearSession, loadSession, saveSession } from './storage'
import type { Session } from './types'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => loadSession())
  const [loading, setLoading] = useState(true)
  const sessionRef = useRef<Session | null>(session)

  useEffect(() => {
    sessionRef.current = session
  }, [session])

  const handleLogout = useCallback(async () => {
    const had = sessionRef.current
    setSession(null)
    clearSession()
    if (had) {
      try {
        await logoutRequest()
      } catch {
        // El BE puede haber expirado el token; ya limpiamos el estado local.
      }
    }
  }, [])

  // Validar sesión persistida al bootear.
  useEffect(() => {
    let cancelled = false
    const existing = sessionRef.current
    if (!existing) {
      setLoading(false)
      return
    }
    void meRequest()
      .then((user) => {
        if (cancelled) return
        const refreshed = { ...existing, user }
        setSession(refreshed)
        saveSession(refreshed)
      })
      .catch(() => {
        if (cancelled) return
        clearSession()
        setSession(null)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Wire del cliente HTTP. Una sola vez: los getters leen sessionRef, así
  // el interceptor siempre ve el token actual sin re-registrar.
  useEffect(() => {
    setAuthHandler({
      getToken: () => sessionRef.current?.token ?? null,
      onUnauthorized: () => {
        void handleLogout()
      },
    })
  }, [handleLogout])

  const login = useCallback(async (email: string, password: string) => {
    const next = await loginRequest(email, password)
    setSession(next)
    saveSession(next)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
      token: session?.token ?? null,
      expiresAt: session?.expiresAt ?? null,
      loading,
      isAuthenticated: !!session,
      login,
      logout: handleLogout,
    }),
    [session, loading, login, handleLogout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
