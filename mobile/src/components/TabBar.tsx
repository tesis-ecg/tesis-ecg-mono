import type { BottomTabBarProps } from "expo-router/js-tabs";
import {
  Cpu,
  FileText,
  House,
  UserRound,
  type LucideIcon,
} from "lucide-react-native";
import { createContext, useContext, useMemo, useState } from "react";
import { useWindowDimensions } from "react-native";
import {
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  withTiming,
} from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { AnimatedView, Pressable, Text, View } from "@/tw";
import * as haptics from "@/lib/haptics";
import { cn } from "@/lib/cn";

/**
 * Barra de navegación dibujada a mano.
 *
 * No usa `NativeTabs` a propósito. La tab bar nativa se ve distinta en iOS y en
 * Android (posición del label, altura, ripple, íconos del sistema) y el
 * requisito es que las dos plataformas se vean lo más parecido posible.
 * Dibujarla acá cuesta unas líneas y elimina la diferencia de raíz.
 */

const ICONS: Record<string, { icon: LucideIcon; label: string }> = {
  index: { icon: House, label: "Inicio" },
  device: { icon: Cpu, label: "Dispositivo" },
  history: { icon: FileText, label: "Historial" },
  profile: { icon: UserRound, label: "Perfil" },
};

const TAB_EASING = Easing.bezier(0.77, 0, 0.175, 1);

/** Separación mínima respecto del borde inferior, cuando no hay home indicator. */
const MIN_BOTTOM_GAP = 12;

/**
 * A partir de acá la barra muestra solo íconos.
 *
 * Con el cuerpo grande del sistema, "Dispositivo" no entra en un cuarto de
 * pantalla y se recortaba a "Mi dispo…"; "Inicio" y "Perfil" quedaban en "In…"
 * y "P…", que no quieren decir nada. Es lo mismo que hace la tab bar de iOS: a
 * cierto tamaño esconde los textos y deja los íconos, que no se recortan. El
 * `accessibilityLabel` sigue estando, así que VoiceOver los sigue nombrando.
 */
const LABELS_OFF_ABOVE = 1.3;

/**
 * Cuánto espacio ocupa la barra, contado desde el borde de abajo.
 *
 * Antes era una constante de 76 pt, y era mentira: el label escala con el
 * Dynamic Type del sistema, así que con el cuerpo grande la barra crecía y
 * tapaba la última card de cada pantalla. Ahora la barra se mide sola y publica
 * su alto real acá; `Screen` lo lee para saber cuánto aire dejar al final del
 * scroll. Fuera de las pestañas el valor es 0 y nadie reserva nada.
 */
const TabBarSpaceContext = createContext(0);

export function useTabBarSpace(): number {
  return useContext(TabBarSpaceContext);
}

const SetTabBarSpaceContext = createContext<(space: number) => void>(() => {});

export function TabBarSpaceProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [space, setSpace] = useState(0);
  const setter = useMemo(() => setSpace, []);
  return (
    <SetTabBarSpaceContext.Provider value={setter}>
      <TabBarSpaceContext.Provider value={space}>
        {children}
      </TabBarSpaceContext.Provider>
    </SetTabBarSpaceContext.Provider>
  );
}

export function TabBar({ state, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  const { fontScale } = useWindowDimensions();
  const reduceMotion = useReducedMotion();
  const showLabels = fontScale <= LABELS_OFF_ABOVE;
  const publishSpace = useContext(SetTabBarSpaceContext);
  /** Ancho útil de la fila, para saber cuánto mide cada pestaña. */
  const [rowWidth, setRowWidth] = useState(0);

  const bottom = Math.max(insets.bottom, MIN_BOTTOM_GAP);
  const tabs = state.routes.filter((route) => ICONS[route.name]);
  const tabWidth = tabs.length > 0 ? rowWidth / tabs.length : 0;

  // La pastilla viaja hasta la pestaña activa en vez de aparecer y desaparecer:
  // el movimiento es lo que muestra que las cuatro son el mismo control.
  const pill = useAnimatedStyle(() => {
    const x = tabWidth * state.index;
    return {
      width: tabWidth,
      transform: [
        {
          translateX: reduceMotion
            ? x
            : withTiming(x, { duration: 150, easing: TAB_EASING }),
        },
      ],
      opacity: tabWidth > 0 ? 1 : 0,
    };
  }, [tabWidth, state.index, reduceMotion]);

  return (
    <View
      // Flota: se despega del borde inferior en vez de quedar pegada. Con
      // `bottom-0` las esquinas redondeadas de abajo quedaban cortadas por el
      // borde de la pantalla y la barra se leía como un rectángulo mal tajado.
      className="absolute right-4 left-4 rounded-full bg-white p-1.5 shadow-[0px_8px_28px_rgba(23,45,57,0.14)]"
      style={{ bottom }}
      onLayout={(event) =>
        publishSpace(event.nativeEvent.layout.height + bottom)
      }
    >
      <View
        className="flex-row"
        onLayout={(event) => setRowWidth(event.nativeEvent.layout.width)}
      >
        <AnimatedView
          // Detrás de los botones, no encima: es fondo, no contenido.
          className="absolute top-0 bottom-0 rounded-full bg-primary-50"
          style={pill}
          pointerEvents="none"
        />

        {tabs.map((route, index) => {
          const meta = ICONS[route.name];
          const Icon = meta.icon;
          const isFocused = state.index === index;

          return (
            <Pressable
              key={route.key}
              accessibilityRole="button"
              accessibilityState={isFocused ? { selected: true } : {}}
              accessibilityLabel={meta.label}
              onPress={() => {
                const event = navigation.emit({
                  type: "tabPress",
                  target: route.key,
                  canPreventDefault: true,
                });
                if (!isFocused && !event.defaultPrevented) {
                  haptics.selection();
                  navigation.navigate(route.name);
                }
              }}
              className={cn(
                "flex-1 items-center gap-1 rounded-full",
                // Sin texto, el ícono se queda solo en la pastilla y necesita
                // algo más de aire arriba y abajo para no verse apretado.
                showLabels ? "py-2.5" : "py-3.5",
              )}
            >
              <Icon
                size={showLabels ? 25 : 28}
                color={isFocused ? "#0b2185" : "#89939b"}
                strokeWidth={isFocused ? 2.5 : 2}
              />
              {showLabels ? (
                <Text
                  numberOfLines={1}
                  className={cn(
                    "text-[12px]",
                    isFocused
                      ? "font-semibold text-primary-500"
                      : "text-gray-500",
                  )}
                >
                  {meta.label}
                </Text>
              ) : null}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}
