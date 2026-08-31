import { Platform, type RefreshControlProps } from "react-native";
import { cloneElement, useState } from "react";
import { KeyboardAwareScrollView } from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { ScrollView, View } from "@/tw";
import { useTabBarSpace } from "@/components/TabBar";
import { cn } from "@/lib/cn";
import { modalHeaderFade } from "@/lib/gradients";

interface ScreenProps {
  children: React.ReactNode;
  /** Contenido scrolleable. `false` para pantallas que se ajustan solas. */
  scroll?: boolean;
  className?: string;
  contentClassName?: string;
  /**
   * Reservar el safe area de arriba. `false` en las pantallas presentadas como
   * modal: la hoja ya arranca por debajo del notch, así que sumarle el inset
   * del sistema deja un hueco muerto de casi 60 pt sobre el título.
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
}

/** Margen lateral de todas las pantallas. */
const GUTTER = "px-5";

/** Aire entre el último elemento y lo que venga abajo. */
const BOTTOM_GAP = 24;

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
}: ScreenProps) {
  const insets = useSafeAreaInsets();
  const tabBarSpace = useTabBarSpace();
  const [fixedHeaderHeight, setFixedHeaderHeight] = useState(112);
  const paddingTop = fixedHeader
    ? fixedHeaderHeight
    : topInset
      ? insets.top + 8
      : 16;
  // La barra ya incluye el safe area de abajo en su propia separación del
  // borde: sumar `insets.bottom` otra vez duplicaría el hueco.
  const paddingBottom =
    (tabBarSpace > 0 ? tabBarSpace : insets.bottom) + BOTTOM_GAP;

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
      bottomOffset={16}
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

  if (!fixedHeader) return content;

  return (
    <View className={cn("flex-1 bg-gray-50", className)}>
      {content}
      <View
        className="absolute inset-x-0 top-0 z-10 px-4 pb-7"
        // El `paddingTop` va por `style` y no por `className`: es dinámico y
        // dejarlo también en las clases haría que uno pise al otro en silencio.
        style={[
          modalHeaderFade,
          { paddingTop: topInset ? insets.top + 8 : 16 },
        ]}
        onLayout={(event) =>
          setFixedHeaderHeight(event.nativeEvent.layout.height)
        }
      >
        {fixedHeader}
      </View>
    </View>
  );
}
