import { Bell, ChevronDown, ChevronRight } from "lucide-react-native";
import { useState } from "react";
import {
  Easing,
  interpolate,
  ReduceMotion,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
  type SharedValue,
} from "react-native-reanimated";

import { alertMeta } from "@/features/patient/deviceMeta";
import type { PatientAlert } from "@/features/patient/types";
import { formatRelativeTime } from "@/lib/format";
import { brandGradient } from "@/lib/gradients";
import * as haptics from "@/lib/haptics";
import { DURATION, usePressScale } from "@/lib/motion";
import { AnimatedPressable, AnimatedView, Pressable, Text, View } from "@/tw";
import { Heading } from "@/components/ui/typography";

/** Cuánto asoma cada aviso por debajo del que tiene encima, en la pila. */
const PEEK = 14;

/** Separación entre avisos ya desplegados. */
const GAP = 4;

/** Cuánto se angosta cada aviso a medida que baja en la pila. */
const SCALE_STEP = 0.05;

/** Cuántos se dibujan apilados; del cuarto en adelante no entrarían igual. */
const MAX_STACKED = 3;

/** Alto de referencia para el primer render, antes de medir. */
const ESTIMATED_HEIGHT = 92;

interface PendingAlertsProps {
  alerts: PatientAlert[];
  onOpen: (alert: PatientAlert) => void;
}

/**
 * Los avisos sin responder de Inicio.
 *
 * Es lo único de la pantalla que el sistema le está pidiendo al paciente, y
 * tiene que ganarle visualmente a todo lo demás. Pero tres cards a página
 * completa empujaban el estado del chaleco y el botón de registrar fuera de la
 * pantalla, así que arrancan apiladas —la más reciente arriba, las otras
 * asomando por debajo— y se despliegan al tocarlas.
 *
 * La pila se dibuja con posición absoluta y un alto animado en el contenedor:
 * en flujo normal las cards no se pueden superponer. Los altos se miden con
 * `onLayout` en vez de fijarse, porque el título de un aviso puede ocupar dos
 * renglones y con Dynamic Type grande, tres.
 */
export function PendingAlerts({ alerts, onOpen }: PendingAlertsProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  /*
    Los altos se guardan por id del aviso y no por posición.

    Cuando el paciente responde el primero, ese aviso sale de la lista y todos
    los de abajo suben un lugar. Con los altos indexados por posición, el que
    quedaba primero heredaba el alto del que se fue: si medían distinto —y miden
    distinto todo el tiempo, porque un título de dos renglones es 20 pt más
    alto— la separación con el de abajo quedaba con un hueco o con las dos cards
    montadas. Y no se corregía solo: `onLayout` no vuelve a disparar por cambiar
    de posición, así que el alto equivocado se quedaba hasta el próximo refetch.
  */
  const [heights, setHeights] = useState<Record<string, number>>({});
  const progress = useSharedValue(0);

  const heightOf = (index: number) => {
    const alert = alerts[index];
    return (alert && heights[alert.id]) || ESTIMATED_HEIGHT;
  };

  const reportHeight = (id: string, value: number) => {
    setHeights((current) => {
      // Sin el corte, cada `onLayout` dispara un render que vuelve a medir.
      if (Math.abs((current[id] ?? 0) - value) < 1) return current;
      return { ...current, [id]: value };
    });
  };

  const toggle = () => {
    haptics.tap();
    const next = !isExpanded;
    setIsExpanded(next);
    progress.value = withTiming(next ? 1 : 0, {
      duration: DURATION,
      easing: Easing.out(Easing.cubic),
      // "Reducir movimiento" del sistema deja el cambio instantáneo.
      reduceMotion: ReduceMotion.System,
    });
  };

  const collapsedScaleAt = (index: number) =>
    1 - Math.min(index, MAX_STACKED) * SCALE_STEP;

  const expandedOffsetAt = (index: number) =>
    alerts
      .slice(0, index)
      .reduce((total, _alert, i) => total + heightOf(i) + GAP, 0);

  /*
    La pila se arma de abajo hacia arriba: lo que se fija es dónde termina cada
    card, no dónde empieza. Apilándolas por el techo —cada una 14 pt más abajo
    que la anterior— una card de un renglón detrás de una de dos desaparecía
    entera, porque terminaba más arriba que la que tenía encima. Y los títulos
    miden distinto todo el tiempo: "Ritmo irregular" ocupa un renglón y "Latidos
    más rápidos de lo habitual", dos.

    La escala también entra en la cuenta: encoge desde el centro, así que una
    card al 95% ya perdió la mitad de esa diferencia por abajo.
  */
  const collapsedBottoms = alerts.reduce<number[]>((bottoms, _alert, index) => {
    bottoms.push(index === 0 ? heightOf(0) : bottoms[index - 1] + PEEK);
    return bottoms;
  }, []);

  const collapsedOffsetAt = (index: number) => {
    const height = heightOf(index);
    return (
      collapsedBottoms[index] -
      height +
      (height * (1 - collapsedScaleAt(index))) / 2
    );
  };

  // Los ceros cubren el render en el que ya no queda ningún aviso: el
  // contenedor no se puede desmontar antes de terminar de encogerse.
  const lastIndex = Math.max(alerts.length - 1, 0);
  const expandedHeight =
    alerts.length === 0 ? 0 : expandedOffsetAt(lastIndex) + heightOf(lastIndex);
  const collapsedHeight =
    alerts.length === 0
      ? 0
      : collapsedBottoms[Math.min(alerts.length, MAX_STACKED) - 1];

  const containerStyle = useAnimatedStyle(() => ({
    height: interpolate(
      progress.value,
      [0, 1],
      [collapsedHeight, expandedHeight],
    ),
  }));

  const chevronStyle = useAnimatedStyle(() => ({
    transform: [
      { rotate: `${interpolate(progress.value, [0, 1], [0, 180])}deg` },
    ],
  }));

  return (
    <View className="gap-3">
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: isExpanded }}
        accessibilityLabel={`${alerts.length} ${alerts.length === 1 ? "aviso" : "avisos"} sin responder. ${
          isExpanded ? "Contraer" : "Desplegar"
        }`}
        onPress={toggle}
        className="flex-row items-center gap-3"
      >
        <View className="size-5 items-center justify-center">
          <Bell size={22} color="#000" />
        </View>
        <Heading className="flex-1">
          {alerts.length === 1
            ? "Tenés un aviso sin responder"
            : "Tenés avisos sin responder"}
        </Heading>
        <AnimatedView style={chevronStyle}>
          <ChevronDown size={22} color="#727f87" />
        </AnimatedView>
      </Pressable>

      <AnimatedView style={containerStyle}>
        {alerts.map((alert, index) => (
          <StackedAlert
            key={alert.id}
            alert={alert}
            index={index}
            total={alerts.length}
            progress={progress}
            isExpanded={isExpanded}
            collapsedY={collapsedOffsetAt(index)}
            expandedY={expandedOffsetAt(index)}
            collapsedScale={collapsedScaleAt(index)}
            collapsedOpacity={index < MAX_STACKED ? 1 : 0}
            onLayoutHeight={(value) => reportHeight(alert.id, value)}
            onPress={() => (isExpanded ? onOpen(alert) : toggle())}
          />
        ))}
      </AnimatedView>
    </View>
  );
}

interface StackedAlertProps {
  alert: PatientAlert;
  index: number;
  total: number;
  progress: SharedValue<number>;
  isExpanded: boolean;
  collapsedY: number;
  expandedY: number;
  collapsedScale: number;
  collapsedOpacity: number;
  onLayoutHeight: (height: number) => void;
  onPress: () => void;
}

function StackedAlert({
  alert,
  index,
  total,
  progress,
  isExpanded,
  collapsedY,
  expandedY,
  collapsedScale,
  collapsedOpacity,
  onLayoutHeight,
  onPress,
}: StackedAlertProps) {
  const meta = alertMeta(alert.kind);
  const Icon = meta.icon;
  // Colapsada, solo la de arriba escucha el toque: las de atrás asoman apenas
  // unos pixeles y tocarlas por error abriría el formulario del aviso
  // equivocado. Ahí el toque despliega la pila, no abre nada.
  const isReachable = isExpanded || index === 0;
  const press = usePressScale();

  const style = useAnimatedStyle(() => ({
    transform: [
      {
        translateY: interpolate(
          progress.value,
          [0, 1],
          [collapsedY, expandedY],
        ),
      },
      { scale: interpolate(progress.value, [0, 1], [collapsedScale, 1]) },
    ],
    opacity: interpolate(progress.value, [0, 1], [collapsedOpacity, 1]),
  }));

  return (
    <AnimatedView
      // `zIndex` y no orden inverso de render: la más reciente va arriba de la
      // pila, pero el lector de pantalla la tiene que leer primero igual.
      className="absolute inset-x-0 top-0"
      style={[{ zIndex: total - index }, style]}
      pointerEvents={isReachable ? "auto" : "none"}
      accessibilityElementsHidden={!isReachable}
      importantForAccessibility={isReachable ? "auto" : "no-hide-descendants"}
      onLayout={(event) => onLayoutHeight(event.nativeEvent.layout.height)}
    >
      <AnimatedPressable
        accessibilityRole="button"
        accessibilityLabel={
          isExpanded
            ? `${meta.label}. ${formatRelativeTime(alert.detectedAt)}`
            : `${meta.label}. ${formatRelativeTime(alert.detectedAt)}. Desplegar los avisos`
        }
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        style={press.style}
      >
        {/*
          El `Pressable` escucha y esta `View` se pinta: un pressable animado
          con fondo propio deja de recibir toques (ver AGENTS.md).
        */}
        {/*
          El borde del color del fondo de pantalla es lo que separa una card de
          la siguiente cuando están apiladas: sin él, tres gradientes iguales
          superpuestos se leían como una sola mancha azul con los bordes
          redondeados repetidos.
        */}
        <View
          className="flex-row items-center gap-4 rounded-[20px] border border-gray-50 p-5"
          style={brandGradient}
        >
          <View className="size-12 items-center justify-center rounded-full bg-white/20">
            <Icon size={24} color="#ffffff" />
          </View>
          <View className="flex-1 gap-1">
            <Text className="text-[17px] font-semibold text-white">
              {meta.label}
            </Text>
            <Text className="text-[15px] text-primary-100">
              {formatRelativeTime(alert.detectedAt)}
            </Text>
          </View>
          <ChevronRight size={22} color="#cbcefd" />
        </View>
      </AnimatedPressable>
    </AnimatedView>
  );
}
