import { CloudOff } from 'lucide-react-native'

import { View } from '@/tw'
import { unwrapError } from '@/lib/api'
import { Button } from './Button'
import { Body, Heading } from './typography'

interface ErrorStateProps {
  /** El error que tiró la query. Se traduce con `unwrapError`. */
  error: unknown
  /** Reintento. Casi siempre el `refetch` de la query que falló. */
  onRetry?: () => void
}

/**
 * "No pudimos cargar esto", con forma de reintento.
 *
 * Existe porque su ausencia era un bug: sin él, cada pantalla caía en su estado
 * vacío cuando la request fallaba, y el paciente leía "todavía no tenés
 * chaleco" o "todavía no anotaste nada" cuando lo único que pasaba era que el
 * celular estaba sin señal. Decirle a alguien que no tiene el equipo que sí
 * tiene puesto es peor que no decirle nada.
 *
 * El texto sale de `unwrapError`, que ya distingue el caso sin conexión —el más
 * común en un celular— del error del servidor.
 */
export function ErrorState({ error, onRetry }: ErrorStateProps) {
  return (
    <View className="items-center gap-3 px-4 py-10">
      <View className="size-16 items-center justify-center rounded-full bg-error-100">
        <CloudOff size={30} color="#88271d" />
      </View>
      <Heading className="text-center">No pudimos cargar esto</Heading>
      <Body className="text-center text-gray-600">{unwrapError(error)}</Body>
      {onRetry ? (
        <Button label="Reintentar" variant="secondary" onPress={onRetry} fullWidth={false} className="mt-2" />
      ) : null}
    </View>
  )
}
