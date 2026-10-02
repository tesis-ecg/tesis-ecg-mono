import { Platform, type RefreshControlProps } from "react-native";
import { cloneElement, useState } from "react";
import {
  KeyboardAwareScrollView,
  KeyboardStickyView,
} from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ScrollView, View } from "@/tw";
import { useTabBarSpace } from "@/components/TabBar";
import { cn } from "@/lib/cn";
import { modalFooterFade, modalHeaderFade } from "@/lib/gradients";

interface ScreenProps {
  children: React.ReactNode;
  /** Contenido scrolleable. `false` para pantallas que se ajustan solas. */
  scroll?: boolean;
  className?: string;
  contentClassName?: string;
  /**
   * Reservar el safe area de arriba. `false` en las pantallas presentadas como
   * modal: en iOS la hoja ya arranca por debajo del notch, así que sumarle el
   * inset del sistema deja un hueco muerto de casi 60 pt sobre el título.
   *
   * **En Android no aplica**: ahí un `presentation: 'modal'` se dibuja a
   * pantalla completa, sin card ni margen, así que el inset sigue haciendo
   * falta y `Screen` lo reserva igual. Ver `TOP_INSET` más abajo.
   */
  topInset?: boolean;
  refreshControl?: React.ReactElement<RefreshControlProps>;
  /** Hace que el input enfocado quede visible sobre el teclado en iOS y Android. */
  keyboardAware?: boolean;
  /**
   * Capa fija; el contenido scrollea por detrás de su gradiente. Respeta
   * `topInset`: en una pantalla apilada el header tiene que arrancar debajo de
   * la barra de estado, y en un modal no.
   */
  fixedHeader?: React.ReactNode;
  /**
   * Barra fija al pie: el call-to-action que no se puede perder de vista.
   *
   * En el formulario de la bitácora el botón de enviar vivía al final del
   * scroll, así que apenas se desplegaba el campo de "otro" quedaba fuera de
   * pantalla y el paciente tenía que buscarlo. Acá se queda siempre visible, y
   * sube con el teclado en vez de quedar tapado.
   *
   * El contenido reserva exactamente el alto que ocupe: se mide con `onLayout`
   * y no se asume, por lo mismo que la tab bar (ver `TabBar`).
   */
  fixedFooter?: React.ReactNode;
}

/** Margen lateral de todas las pantallas. */
const GUTTER = "px-5";

/** Aire entre el último elemento y lo que venga abajo. */
const BOTTOM_GAP = 24;

/**
 * Cuánto respirar arriba en una pantalla modal.
 *
 * En iOS la presentación modal es una card que ya arranca debajo de la barra de
 * estado: alcanza con un margen chico. En Android la misma presentación ocupa
 * la pantalla entera, así que ese margen dejaba el título pegado al borde
 * superior, por debajo de la hora y la batería. Es de las pocas diferencias que
 * se sostienen entre plataformas y no contradice la regla de paridad visual:
 * el objetivo es que las dos se vean igual **en pantalla**, y para eso el safe
 * area tiene que respetar lo que cada sistema reserva.
 */
const MODAL_TOP_GAP = 16;

/**
 * Contenedor de pantalla.
 *
 * El safe area se resuelve a mano y no con `SafeAreaView` porque el espacio de
 * abajo lo aporta la tab bar flotante: sumar los dos deja un hueco muerto en
 * los iPhone con notch.
 *
 * Cuánto reserva al pie lo dice la propia barra, que se mide con `onLayout` y
 * publica su alto por contexto. Antes era una constante de 76 pt que quedaba
 * corta apenas el paciente agrandaba la letra del sistema, y entonces la barra
 * tapaba la última card. Fuera de las pestañas el contexto vale 0 y solo se
 * reserva el safe area.
 *
 * El contenido va en una `View` interna y no en el `contentContainer` del
 * scroll. `react-native-css` mapea `contentContainerClassName` **sobre**
 * `contentContainerStyle`, así que pasar los dos hacía que los insets pisaran
 * las clases y la pantalla quedara sin margen lateral. Separándolos, cada prop
 * tiene un solo dueño.
 */
export function Screen({
  children,
  scroll = true,
  className,
  contentClassName,
  topInset = true,
  refreshControl,
  keyboardAware = false,
  fixedHeader,
  fixedFooter,
}: ScreenProps) {
  const insets = useSafeAreaInsets();
  const tabBarSpace = useTabBarSpace();
  const [fixedHeaderHeight, setFixedHeaderHeight] = useState(112);
  const [fixedFooterHeight, setFixedFooterHeight] = useState(96);
  // Cuánto queda por encima del contenido, y también dónde arranca el header
  // fijo si lo hay.
  const topPadding =
    topInset || Platform.OS === "android" ? insets.top + 8 : MODAL_TOP_GAP;
  const paddingTop = fixedHeader ? fixedHeaderHeight : topPadding;
  // La barra ya incluye el safe area de abajo en su propia separación del
  // borde: sumar `insets.bottom` otra vez duplicaría el hueco. Con una barra
  // fija al pie manda ella, que también se lleva puesto el safe area.
  const paddingBottom =
    (fixedFooter
      ? fixedFooterHeight
      : tabBarSpace > 0
        ? tabBarSpace
        : insets.bottom) + BOTTOM_GAP;

  /*
    El spinner del "tirá para actualizar" se dibuja arriba de todo del scroll,
    que no es donde empieza el contenido: con el safe area quedaba metido detrás
    del notch, y con un header fijo, tapado por el header. En los dos casos la
    pantalla se corría hacia abajo mientras cargaba y no se veía nada girando.

    El `progressViewOffset` lo baja exactamente hasta donde arranca el
    contenido, que es el mismo número que el `paddingTop`. Se inyecta acá y no
    en cada pantalla porque ese número lo resuelve `Screen` y nadie más lo
    conoce.
  */
  const scrollRefreshControl = refreshControl
    ? cloneElement(refreshControl, { progressViewOffset: paddingTop })
    : refreshControl;

  if (!scroll) {
    return (
      <View
        className={cn("flex-1 bg-gray-50", className)}
        style={{ paddingTop, paddingBottom }}
      >
        <View className={cn("flex-1 gap-4", GUTTER, contentClassName)}>
          {children}
        </View>
      </View>
    );
  }

  const content = keyboardAware ? (
    <KeyboardAwareScrollView
      style={{ flex: 1, backgroundColor: "#f6f6f6" }}
      contentContainerStyle={{ paddingTop, paddingBottom }}
      keyboardShouldPersistTaps="handled"
      // Sin esto el campo enfocado se detiene justo arriba del teclado, que es
      // donde ahora está la barra: el `KeyboardAwareScrollView` no la conoce.
      // Los 24 de aire son para que el campo no quede lamiendo el botón.
      bottomOffset={fixedFooter ? fixedFooterHeight + 24 : 16}
      // Y el espacio para llegar hasta ahí. El `paddingBottom` que reserva la
      // barra queda por debajo del teclado cuando se abre, o sea fuera de
      // alcance: sin este extra el scroll se termina antes de poder subir el
      // campo por encima del botón, y el último renglón quedaba tapado.
      extraKeyboardSpace={fixedFooter ? fixedFooterHeight : 0}
      mode="insets"
      refreshControl={scrollRefreshControl}
    >
      <View className={cn("gap-4", GUTTER, contentClassName)}>{children}</View>
    </KeyboardAwareScrollView>
  ) : (
    <ScrollView
      className={cn("flex-1 bg-gray-50", className)}
      contentContainerStyle={{ paddingTop, paddingBottom }}
      keyboardShouldPersistTaps="handled"
      automaticallyAdjustKeyboardInsets={Platform.OS === "ios"}
      refreshControl={scrollRefreshControl}
    >
      <View className={cn("gap-4", GUTTER, contentClassName)}>{children}</View>
    </ScrollView>
  );

  if (!fixedHeader && !fixedFooter) return content;

  return (
    <View className={cn("flex-1 bg-gray-50", className)}>
      {content}
      {fixedHeader ? (
        <View
          className="absolute inset-x-0 top-0 z-10 px-4 pb-7"
          // El `paddingTop` va por `style` y no por `className`: es dinámico y
          // dejarlo también en las clases haría que uno pise al otro en silencio.
          style={[modalHeaderFade, { paddingTop: topPadding }]}
          onLayout={(event) =>
            setFixedHeaderHeight(event.nativeEvent.layout.height)
          }
        >
          {fixedHeader}
        </View>
      ) : null}
      {fixedFooter ? (
        // `KeyboardStickyView` y no un `absolute` a secas: con el teclado
        // abierto la barra quedaría escondida detrás justo cuando el paciente
        // está terminando de escribir, que es cuando más falta hace verla.
        <KeyboardStickyView
          style={{ position: "absolute", left: 0, right: 0, bottom: 0 }}
        >
          <View
            className={cn("pt-6", GUTTER)}
            style={[
              modalFooterFade,
              { paddingBottom: (tabBarSpace > 0 ? tabBarSpace : insets.bottom) + 12 },
            ]}
            onLayout={(event) =>
              setFixedFooterHeight(event.nativeEvent.layout.height)
            }
          >
            {fixedFooter}
          </View>
        </KeyboardStickyView>
      ) : null}
    </View>
  );
}
