import { Activity } from "lucide-react-native";

import { Text, View } from "@/tw";
import { brandGradient } from "@/lib/gradients";
import { cn } from "@/lib/cn";

/**
 * La marca de la app.
 *
 * Un trazo de ECG y no un corazón: el corazón es el ícono por defecto de
 * cualquier cosa relacionada con la salud, y lo que este producto hace es
 * específicamente un electrocardiograma. El azulado del brand va en gradiente
 * para que el cuadrado no se lea como un bloque plano de color.
 *
 * Está dibujada en código y no es un PNG porque tiene que verse bien en cuatro
 * tamaños distintos (login, splash, perfil, ícono) y sobre dos fondos.
 */

interface BrandMarkProps {
  /** Lado del cuadrado, en puntos. */
  size?: number;
  /** Sobre la foto del login la marca va en blanco, no en gradiente. */
  variant?: "gradient" | "onDark";
  className?: string;
}

export function BrandMark({
  size = 56,
  variant = "gradient",
  className,
}: BrandMarkProps) {
  return (
    <View
      className={cn("items-center justify-center", className)}
      style={[
        // El radio sigue al tamaño para que la forma sea la misma en todas las
        // escalas: un radio fijo en un cuadrado de 24 pt lo vuelve un círculo.
        { width: size, height: size, borderRadius: 16 },
        // Sobre la foto no lleva cuadro: el recuadro traslúcido se leía como
        // una mancha rectangular y no como parte de la marca.
        variant === "gradient" && brandGradient,
      ]}
    >
      <Activity
        size={variant === "onDark" ? size : size * 0.58}
        color={variant === "onDark" ? "#ffffff" : "#ffffff"}
        strokeWidth={2.4}
      />
    </View>
  );
}

interface BrandLockupProps {
  /** El texto debajo del nombre. Se omite donde el espacio no da. */
  tagline?: string;
  variant?: "gradient" | "onDark";
  size?: number;
  className?: string;
}

/** Marca + nombre + bajada. Es lo que se ve en el login. */
export function BrandLockup({
  tagline,
  variant = "gradient",
  size = 56,
  className,
}: BrandLockupProps) {
  const onDark = variant === "onDark";
  return (
    <View className={cn("items-center gap-3", className)}>
      <BrandMark size={size} variant={variant} />
      <View className="items-center gap-1">
        <Text
          className={cn(
            "text-[32px] leading-[38px] font-bold tracking-tight",
            onDark ? "text-white" : "text-gray-900",
          )}
        >
          Holter
        </Text>
        {tagline ? (
          <Text
            className={cn(
              "text-[15px] leading-[21px]",
              onDark ? "text-primary-100" : "text-gray-600",
            )}
          >
            {tagline}
          </Text>
        ) : null}
      </View>
    </View>
  );
}
