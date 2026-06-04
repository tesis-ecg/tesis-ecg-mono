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
        // Cookie may already be expired; local state is already cleared.
      }
    }
  }, [])

  // On boot, always call /me — the cookie is the source of truth.
  useEffect(() => {
    let cancelled = false
    void meRequest()
      .then((user) => {
        if (cancelled) return
        const existing = sessionRef.current
        const next: Session = { user, expiresAt: existing?.expiresAt ?? '' }
        setSession(next)
        saveSession(next)
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

  useEffect(() => {
    setAuthHandler({
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
