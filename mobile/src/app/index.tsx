import { Redirect } from 'expo-router'

import { useAuth } from '@/features/auth/AuthContext'

/**
 * Puerta de entrada: manda a Inicio o al login según haya sesión.
 *
 * El `Stack.Protected` del layout ya impide entrar a lo que no corresponde;
 * esta pantalla solo elige el destino inicial.
 */
export default function Index() {
  const { patient } = useAuth()
  return <Redirect href={patient ? '/(tabs)' : '/login'} />
}
