import { RefreshControl } from 'react-native'

interface RefreshProps {
  refreshing: boolean
  onRefresh: () => void
  /**
   * Desde dónde cae el spinner. Lo inyecta `Screen` cuando la pantalla tiene
   * header fijo: sin esto el control aparece arriba de todo del scroll, que es
   * justo lo que el header tapa.
   */
  progressViewOffset?: number
}

/**
 * El spinner del "tirá para actualizar".
 *
 * Va acá y no suelto en cada pantalla porque los colores del `RefreshControl`
 * se pasan por props distintas en cada plataforma: iOS lee `tintColor` y
 * Android lee `colors` (y pinta el disco de atrás con `progressBackgroundColor`).
 * Con el control pelado, iOS mostraba el spinner gris del sistema y Android el
 * teal de Material — dos animaciones que no se parecían entre sí ni al resto de
 * la app. Pasando las tres, la rueda es la misma azul de marca en los dos lados.
 */
export function Refresh({ refreshing, onRefresh, progressViewOffset }: RefreshProps) {
  return (
    <RefreshControl
      refreshing={refreshing}
      onRefresh={onRefresh}
      progressViewOffset={progressViewOffset}
      tintColor="#0b2185"
      colors={['#0b2185']}
      progressBackgroundColor="#ffffff"
    />
  )
}
