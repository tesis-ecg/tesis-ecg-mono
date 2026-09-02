import { Image } from "expo-image";
import { StatusBar } from "expo-status-bar";
import {
  CircleAlert,
  Eye,
  EyeOff,
  LockKeyhole,
  Mail,
} from "lucide-react-native";
import { useEffect, useRef, useState } from "react";
import { useWindowDimensions } from "react-native";
import {
  KeyboardAwareScrollView,
  useKeyboardState,
  type KeyboardAwareScrollViewRef,
} from "react-native-keyboard-controller";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { BrandLockup } from "@/components/BrandMark";
import { Button } from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import { Body, Caption, Title } from "@/components/ui/typography";
import { useAuth } from "@/features/auth/AuthContext";
import { unwrapError } from "@/lib/api";
import { heroScrim } from "@/lib/gradients";
import * as haptics from "@/lib/haptics";
import { Pressable, Text, View } from "@/tw";

const HERO = require("@/assets/images/login-hero.jpg");

/**
 * Entrada a la app.
 *
 * La foto ocupa el tercio de arriba y la hoja blanca monta sobre ella: es la
 * misma imagen que el portal médico, y sirve para que el paciente reconozca de
 * entrada que esto es lo de su médico y no una app cualquiera.
 *
 * La foto es muy clara —un pasillo blanco a contraluz—, así que lleva un velo
 * azul encima (`bg-hero-scrim`). Sin él la marca en blanco no se lee, y con un
 * velo parejo la cara de la foto se apaga; por eso el gradiente es más denso
 * arriba y abajo que en el medio.
 */
export default function Login() {
  const { signIn } = useAuth();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  // La pantalla entra entera: el contenido mide exactamente una pantalla, así
  // que con el teclado cerrado no hay nada que scrollear y lo único que se
  // podía arrastrar era el rebote de iOS —la foto despegándose de arriba—.
  // Apagando el rebote la pantalla queda quieta; cuando el teclado abre, el
  // `contentInset` agrega el alto que falta y ahí sí scrollea de verdad.
  //
  // **El scroll no se apaga con `scrollEnabled`.** Se probó y rompe lo otro:
  // con el `ScrollView` deshabilitado, la `KeyboardAwareScrollView` tampoco
  // puede correr el contenido, y el campo de contraseña se quedaba tapado por
  // el teclado.
  const isKeyboardVisible = useKeyboardState((state) => state.isVisible);
  const scrollRef = useRef<KeyboardAwareScrollViewRef>(null);
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Proporcional y no fijo: en un iPhone SE una foto de 340 pt no deja lugar
  // para los campos, y en un Pro Max una de 260 se ve como una banda perdida.
  const heroHeight = Math.min(Math.max(height * 0.38, 250), 360);

  // Al cerrarse el teclado el `contentInset` desaparece y con él el lugar que
  // había para scrollear, pero el desplazamiento que quedó no vuelve solo: la
  // hoja se quedaba corrida y sin rebote no había forma de bajarla a mano.
  useEffect(() => {
    if (isKeyboardVisible) return;
    scrollRef.current?.scrollTo({ y: 0, animated: true });
  }, [isKeyboardVisible]);

  const handleSubmit = async () => {
    if (!identifier.trim() || !password) {
      haptics.warning();
      setError("Completá los dos campos para entrar.");
      return;
    }
    setError(null);
    setIsSubmitting(true);
    try {
      await signIn(identifier.trim(), password);
    } catch (caught) {
      haptics.error();
      setError(unwrapError(caught));
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <View className="flex-1 bg-white">
      {/* La foto arranca detrás de la barra de estado, así que va en claro. */}
      <StatusBar style="light" />

      <KeyboardAwareScrollView
        ref={scrollRef}
        style={{ flex: 1 }}
        contentContainerStyle={{ flexGrow: 1 }}
        keyboardShouldPersistTaps="handled"
        bottomOffset={16}
        // `insets` y no `layout`. En `layout` la librería agrega un espaciador
        // como último hijo del scroll, y acá la hoja blanca es `flex-1` dentro
        // de un `flexGrow: 1`: el espaciador no estiraba el contenido, le
        // robaba alto a la hoja. El total seguía midiendo una pantalla, no
        // había nada que scrollear y el campo de contraseña se quedaba abajo
        // del teclado. Con `insets` el alto no se toca y el lugar lo pone el
        // `contentInset`.
        mode="insets"
        // Lo único que se puede arrastrar con el teclado cerrado; con el
        // teclado abierto el rebote vuelve, que es como se siente un scroll
        // de verdad en iOS.
        bounces={isKeyboardVisible}
        overScrollMode={isKeyboardVisible ? "auto" : "never"}
        showsVerticalScrollIndicator={false}
      >
        <View style={{ height: heroHeight }}>
          <Image
            source={HERO}
            style={{ width: "100%", height: "100%" }}
            contentFit="cover"
            // La imagen está en el bundle: no hay descarga que atenuar, y un
            // fundido de entrada solo agregaría un parpadeo al abrir la app.
            transition={0}
            accessibilityIgnoresInvertColors
          />
        </View>

        {/* Monta sobre la foto: el solape es lo que une las dos mitades. */}
        <View
          className="-mt-7 flex-1 rounded-t-[32px] bg-white px-5 pt-8"
          style={{ paddingBottom: insets.bottom + 24 }}
        >
          <View className="items-center px-6 pb-12">
            <BrandLockup tagline="Monitoreo cardíaco continuo" size={60} />
          </View>

          <View className="gap-4">
            <Field
              label="Email o DNI"
              leadingIcon={Mail}
              value={identifier}
              onChangeText={setIdentifier}
              autoCapitalize="none"
              autoCorrect={false}
              // `email-address` fuerza el teclado con `@` y hace incómodo tipear
              // el DNI. Con el teclado normal los dos caminos son cómodos.
              keyboardType="default"
              textContentType="username"
              placeholder="tu@email.com o 30123456"
              returnKeyType="next"
            />

            <Field
              label="Contraseña"
              leadingIcon={LockKeyhole}
              value={password}
              onChangeText={setPassword}
              secureTextEntry={!showPassword}
              autoCapitalize="none"
              autoCorrect={false}
              textContentType="password"
              placeholder="La que te dio tu médico"
              returnKeyType="go"
              onSubmitEditing={() => void handleSubmit()}
              // Ver la contraseña importa más de lo habitual: la primera vez el
              // paciente la copia de un papel que le dictó el médico.
              trailingAccessory={
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={
                    showPassword ? "Ocultar contraseña" : "Mostrar contraseña"
                  }
                  onPress={() => {
                    haptics.selection();
                    setShowPassword((value) => !value);
                  }}
                  // El ícono mide 22 pt; el `hitSlop` lo lleva al blanco de
                  // toque de 44 sin agrandar el cuadro del campo.
                  hitSlop={11}
                >
                  {showPassword ? (
                    <EyeOff size={22} color="#5c6b74" />
                  ) : (
                    <Eye size={22} color="#5c6b74" />
                  )}
                </Pressable>
              }
            />

            {error ? (
              <View className="flex-row gap-2 rounded-[16px] bg-error-100 px-4 py-3">
                <CircleAlert size={20} color="#88271d" />
                <Text className="flex-1 text-[15px] text-error-700">
                  {error}
                </Text>
              </View>
            ) : null}

            <Button
              label="Entrar"
              onPress={() => void handleSubmit()}
              loading={isSubmitting}
              className="mt-2"
            />
          </View>

          {/* Empuja la ayuda al pie: sin esto la hoja quedaba con un tercio de
              blanco muerto abajo y el formulario se leía cortado a la mitad. */}
          <View className="min-h-6 flex-1" />

          <Caption className="pt-6 text-center">
            ¿No podés entrar? Pedile a tu médico que te genere una contraseña
            nueva.
          </Caption>
        </View>
      </KeyboardAwareScrollView>
    </View>
  );
}
