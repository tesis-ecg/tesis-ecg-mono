import type { ViewStyle } from 'react-native'

/**
 * Gradientes de marca.
 *
 * **Por qué no salen de `global.css` como el resto de los colores.**
 * `react-native-css@3` compila `background-image: linear-gradient(...)` a un
 * descriptor interno y no trae el resolvedor de runtime que lo convertiría a lo
 * que espera React Native: el objeto llega a `processBackgroundImage` sin
 * `colorStops` y la pantalla revienta con "Cannot read property 'length' of
 * undefined". No es un error de uso, es que la utility no está implementada del
 * lado nativo.
 *
 * React Native, en cambio, sí parsea el string CSS por su cuenta
 * (`processBackgroundImage`, RN 0.86). Así que los gradientes van por `style`,
 * que es la excepción que el propio proyecto ya contempla para las APIs
 * nativas que exigen un color literal. Están todos acá y no sueltos por las
 * pantallas para que los valores sigan viviendo en un solo lugar: si cambia el
 * primary, cambia acá.
 *
 * Los hexadecimales son los mismos tokens de `global.css`:
 * `--color-primary-400/500/600` y `--color-primary-50/100`.
 */

/** El azul de marca, para el CTA y los avisos que hay que responder. */
export const brandGradient: ViewStyle = {
  experimental_backgroundImage: 'linear-gradient(160deg, #1a3edf 0%, #0b2185 55%, #081b73 100%)',
}

/** La versión clara, para superficies que acompañan sin pedir atención. */
export const brandGradientSoft: ViewStyle = {
  experimental_backgroundImage: 'linear-gradient(160deg, #e5e7fe 0%, #cbcefd 100%)',
}

/**
 * Velo sobre la foto del login.
 *
 * La imagen del portal es muy clara —un pasillo blanco a contraluz—, así que
 * sin velo el texto blanco de la marca no se lee.
 *
 * El velo trabaja solo donde hay texto: denso abajo, detrás de la marca, y una
 * pizca arriba para que se lea la hora y la señal. En el medio queda casi
 * transparente a propósito. La primera versión tenía 0,28 en la franja central
 * y teñía toda la foto de violeta: la médica quedaba detrás de una bruma y la
 * imagen dejaba de parecer una foto.
 */
export const heroScrim: ViewStyle = {
  experimental_backgroundImage:
    'linear-gradient(180deg, rgba(11,33,133,0.40) 0%, rgba(11,33,133,0.05) 30%, rgba(11,33,133,0.30) 66%, rgba(8,27,115,0.92) 100%)',
}

/** Header fijo de los modales: superficie arriba, transparencia sobre el scroll. */
export const modalHeaderFade: ViewStyle = {
  experimental_backgroundImage:
    'linear-gradient(180deg, #f6f6f6 0%, rgba(246,246,246,0.98) 68%, rgba(246,246,246,0) 100%)',
}
