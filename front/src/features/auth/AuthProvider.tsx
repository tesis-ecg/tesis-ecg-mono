import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { setAuthHandler } from '@/lib/api'

import { loginRequest, logoutRequest, meRequest } from './api'
import { AuthContext, type AuthContextValue } from './AuthContext'
import { queryClient } from '@/lib/queryClient'
import type { Session } from './types'

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null)
  const [loading, setLoading] = useState(true)
  const sessionRef = useRef<Session | null>(session)
  const invalidationRef = useRef<Promise<void> | null>(null)

  useEffect(() => {
    sessionRef.current = session
  }, [session])

  const invalidateLocalSession = useCallback((): Promise<void> => {
    if (invalidationRef.current) return invalidationRef.current

    invalidationRef.current = (async () => {
      await queryClient.cancelQueries()
      queryClient.clear()
      sessionRef.current = null
      setSession(null)
    })().finally(() => {
      invalidationRef.current = null
    })

    return invalidationRef.current
  }, [])

  const handleLogout = useCallback(async () => {
    const hadSession = sessionRef.current !== null
    await invalidateLocalSession()
    if (!hadSession) return

    try {
      await logoutRequest()
    } catch {
      // La cookie puede haber expirado. La sesión local ya fue invalidada.
    }
  }, [invalidateLocalSession])

  const handleUnauthorized = useCallback(() => {
    // Nunca llama /logout: un 401 de ese endpoint no puede reingresar al interceptor.
    void invalidateLocalSession()
  }, [invalidateLocalSession])

  // On boot, always call /me — the HttpOnly cookie is the only source of truth.
  useEffect(() => {
    let cancelled = false
    void meRequest()
      .then(async (user) => {
        if (cancelled) return
        await queryClient.cancelQueries()
        queryClient.clear()
        const next: Session = {
          user,
          expiresAt: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
        }
        sessionRef.current = next
        setSession(next)
      })
      .catch(() => {
        if (cancelled) return
        void invalidateLocalSession()
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [invalidateLocalSession])

  useEffect(() => {
    setAuthHandler({ onUnauthorized: handleUnauthorized })
    return () => setAuthHandler({ onUnauthorized: () => {} })
  }, [handleUnauthorized])

  const login = useCallback(async (email: string, password: string) => {
    await queryClient.cancelQueries()
    queryClient.clear()
    const next = await loginRequest(email, password)
    sessionRef.current = next
    setSession(next)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user: session?.user ?? null,
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
