import { RefreshControl, type RefreshControlProps } from 'react-native'

interface RefreshProps extends RefreshControlProps {
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
 *
 * **El `...rest` no es de adorno.** En Android, `ScrollView` no dibuja este
 * control como un hijo más: envuelve al scroll entero en un
 * `SwipeRefreshLayout`, y para eso clona este elemento pasándole el scroll por
 * `children` y los estilos de layout por `style`. Un wrapper que no los
 * reenvía se come el scroll: la pantalla queda con el header fijo y la tab bar,
 * y el contenido no llega a montarse nunca. En iOS el control es un hijo del
 * scroll y nadie lo clona, así que el bug solo se ve en Android.
 */
export function Refresh({ refreshing, onRefresh, ...rest }: RefreshProps) {
  return (
    <RefreshControl
      {...rest}
      refreshing={refreshing}
      onRefresh={onRefresh}
      tintColor="#0b2185"
      colors={['#0b2185']}
      progressBackgroundColor="#ffffff"
    />
  )
}
