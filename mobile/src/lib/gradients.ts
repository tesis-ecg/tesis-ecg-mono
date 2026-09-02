import type { ViewStyle } from "react-native";

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
  experimental_backgroundImage:
    "linear-gradient(160deg, #1a3edf 0%, #0b2185 55%, #081b73 100%)",
};

/** La versión clara, para superficies que acompañan sin pedir atención. */
export const brandGradientSoft: ViewStyle = {
  experimental_backgroundImage:
    "linear-gradient(160deg, #e5e7fe 0%, #cbcefd 100%)",
};

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
    "linear-gradient(180deg, rgba(11,33,133,0.40) 0%, rgba(11,33,133,0.05) 30%, rgba(11,33,133,0.30) 66%, rgba(8,27,115,0.92) 100%)",
};

/** Header fijo de los modales: superficie arriba, transparencia sobre el scroll. */
export const modalHeaderFade: ViewStyle = {
  experimental_backgroundImage:
    "linear-gradient(180deg, #f6f6f6 0%, rgba(246,246,246,0.98) 68%, rgba(246,246,246,0) 100%)",
};

/**
 * Aura del chaleco: el estado del equipo, dicho en color.
 *
 * Es un halo circular que se apoya **detrás** de la foto de Dispositivo. El
 * centro no se ve nunca —lo tapa el propio chaleco—, así que lo que dibuja el
 * estado es el derrame que asoma a los costados de la prenda. Por eso la caída
 * está corrida hacia afuera: si el degradado muriera antes del 50 % el aura
 * quedaría escondida entera debajo de la foto.
 *
 * Los cuatro tonos comparten la curva y sólo cambian el color y cuánto pesan.
 * Está escrito como función y no como cuatro strings a mano por eso mismo: la
 * caída tiene que ser idéntica en los cuatro o el cambio de estado se lee como
 * un cambio de tamaño además de uno de color.
 *
 * **Por qué cada tono lleva un alfa distinto.** Los tokens no tienen todos el
 * mismo peso visual: el ámbar de `--color-warning-500` es un arena muy claro y
 * con el mismo alfa que el azul desaparecía contra el fondo `gray-50`. Los
 * números están calibrados para que los cuatro se lean con la misma presencia,
 * no para que compartan el valor.
 *
 * Y por eso también parecen altos —el ámbar llega a 0,9— cuando lo que se ve en
 * pantalla es suave: ese pico está en el centro, o sea debajo del chaleco. Lo
 * que llega al ojo es poco más de la mitad, que es cuanto queda a la altura del
 * borde de la prenda.
 *
 * El fundido termina en `rgba(color, 0)` y no en `transparent`: la palabra
 * clave es un negro transparente, y al interpolar contra ella el halo agarra un
 * gris sucio en el borde.
 */
export type VestAuraTone = "live" | "waiting" | "attention" | "alert";

/**
 * Los tonos, en orden fijo.
 *
 * El componente monta una capa por tono y cruza opacidades para pasar de una a
 * otra, así que el orden tiene que ser estable entre renders.
 */
export const VEST_AURA_TONES: readonly VestAuraTone[] = [
  "waiting",
  "live",
  "attention",
  "alert",
];

/** La curva del halo, en un color y con un pico de opacidad. */
function aura(r: number, g: number, b: number, peak: number): ViewStyle {
  const stop = (at: number, factor: number) =>
    `rgba(${r}, ${g}, ${b}, ${Number((peak * factor).toFixed(3))}) ${at}%`;

  return {
    // `closest-side` sobre una vista cuadrada deja el 100 % del degradado justo
    // en el radio de la circunferencia inscripta: así los porcentajes de abajo
    // se leen como fracciones del radio y no dependen de la diagonal.
    experimental_backgroundImage: `radial-gradient(circle closest-side, ${[
      stop(0, 1),
      stop(32, 0.8),
      stop(55, 0.52),
      stop(75, 0.22),
      stop(100, 0),
    ].join(", ")})`,
  };
}

export const vestAura: Record<VestAuraTone, ViewStyle> = {
  /** Grabando y enviando. `--color-success-500`. */
  live: aura(81, 192, 117, 0.5),
  /** Todavía no arrancó el estudio. `--color-info-500`. */
  waiting: aura(41, 77, 236, 0.4),
  /** Hace horas que no llegan datos. `--color-warning-500`. */
  attention: aura(239, 196, 130, 0.9),
  /** El chaleco está mal colocado. `--color-error-500`. */
  alert: aura(201, 59, 43, 0.48),
};
