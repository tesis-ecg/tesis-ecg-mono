# App móvil del paciente — guía para agentes

## Expo cambió: leé la doc versionada

Antes de escribir código de Expo, leé la documentación **de esta versión** en
https://docs.expo.dev/versions/v57.0.0/. Las APIs de SDK 57 difieren de las que
circulan en tutoriales y respuestas viejas. Dos ejemplos que ya mordieron acá:

- `expo-router` ya no depende de `@react-navigation/*`. Los tipos de la barra de
  pestañas salen de `expo-router/js-tabs`, no de `@react-navigation/bottom-tabs`.
- `Tabs` se importa de `expo-router/js-tabs`; el export de `expo-router` está
  deprecado.

## Contexto

Esta app es la contraparte del **Módulo 5 (Acompañamiento del Paciente)** de
`../Requerimientos.md`. Consume el **mismo backend** que el portal médico, bajo
el prefijo `/mobile` (`back/app/modules/patient_app/`). No es un producto
aparte: si cambia un DTO del backend, hay que actualizar
`src/features/patient/types.ts`, que es un espejo escrito a mano.

Ver `README.md` para correrla, y para el detalle de push y credenciales.

## Reglas del proyecto

**Todo componente visual es propio.** Nada de `NativeTabs`, `@expo/ui` ni
componentes que cambien de aspecto según la plataforma. El requisito del
producto es que iOS y Android se vean lo más parecido posible, y por eso hasta
la barra de pestañas está dibujada a mano (`src/components/TabBar.tsx`). Antes
de escribir un componente nuevo, buscá en `src/components/ui/`.

**Nunca importes primitivos de `react-native` directamente para estilar.**
`View`, `Text`, `Pressable`, `ScrollView`, `TextInput` y `ActivityIndicator`
salen de `@/tw`, que es donde se les agrega el soporte de `className`. Los
componentes de RN que no pasan por ahí (`KeyboardAvoidingView`,
`GestureHandlerRootView`) van con `style`, no con `className`.

**Los colores salen de `src/global.css`.** Son los mismos tokens del portal
(`front/src/styles/tokens.css`). Nada de hexadecimales sueltos en los
componentes, salvo donde una API nativa exige un color literal (los `color` de
`Ionicons`, el `lightColor` del canal de Android) y los gradientes, que van
todos en `src/lib/gradients.ts` — ver la trampa de abajo.

**Los gradientes son tres y se usan en cuatro lugares.** El velo de la foto del
login, la card de aviso pendiente de Inicio, el botón primario y los círculos de
marca. El azul marca lo que hay que mirar; si estuviera en todas las
superficies no marcaría nada. Antes de sumar un gradiente nuevo, pensá si lo que
querés destacar compite con el CTA de esa pantalla.

**La app es light-only.** Está decidido: un solo esquema de color elimina las
diferencias entre cómo resuelve el modo oscuro cada plataforma. No agregues
variantes `dark:`.

**Accesibilidad para adultos mayores.** Cuerpo mínimo 17 pt, blancos de toque
de 44 pt para arriba (los botones son de 56), un solo call-to-action primario
por pantalla, y `allowFontScaling` en su default para respetar el Dynamic Type
del sistema.

## Trampas conocidas

Todas salieron corriendo la app en el simulador; ninguna la ve el type-check ni
el bundling, y las dos peores (el gradiente y el botón que no responde) tampoco
dejan rastro en la consola. **Antes de dar por buena una pantalla hay que
mirarla, y antes de dar por bueno un botón hay que tocarlo.**

**Hermes no trae `Intl.RelativeTimeFormat`.** Usarlo hacía explotar Inicio con
"undefined cannot be used as a constructor". `formatRelativeTime`
(`src/lib/format.ts`) está escrito a mano por eso. `Intl.DateTimeFormat` sí
existe, pero resuelve es-AR a 12 h: las fechas van con `hour12: false`.

**`className` y `style` no se pueden pasar juntos a una prop mapeada.**
`react-native-css` mapea `contentContainerClassName` **sobre**
`contentContainerStyle`, así que pasar los dos hace que el segundo pise al
primero en silencio — así se perdió el margen lateral de todas las pantallas.
Si una prop necesita valores dinámicos (los insets del safe area), va por
`style` y el resto se resuelve en una `View` interna. Ver `ui/Screen.tsx`.

**Las variantes de hermanos de Tailwind no existen en RN.** `last:`, `first:`,
`odd:`, `hover:` y compañía no hacen nada: no hay selectores CSS. El separador
de la última fila de una lista se pasa por prop (`InfoRow`, con `divider`).

**Las utilities de gradiente de Tailwind no funcionan.** `react-native-css@3`
compila `background-image: linear-gradient(...)` a un descriptor propio pero no
trae el resolvedor de runtime que lo convertiría a lo que espera React Native:
el objeto llega a `processBackgroundImage` sin `colorStops` y la pantalla
revienta con *"Cannot read property 'length' of undefined"*. React Native sí
parsea el string CSS por su cuenta, así que los gradientes van por `style` desde
`src/lib/gradients.ts`. No los pongas en `global.css`.

**`bg-black/40` no pinta nada.** El modificador de opacidad de Tailwind v4
compila a `color-mix(in oklab, var(--color-black) 40%, transparent)` y con el
negro `react-native-css` devuelve un color transparente: el velo del
`ui/BottomSheet` no se veía y la clase no daba ningún error. Lo insidioso es que
`bg-white/20` —el círculo del ícono en las cards de aviso de Inicio— sí anda, así
que el modificador parece funcionar hasta que se lo usa con el negro. Los colores
con alfa van con el alfa escrito en el token de `global.css` (`--color-scrim`),
no con el modificador.

**Un `Pressable` animado que además se pinta deja de recibir toques.** El botón
primario tenía el gradiente y el `useAnimatedStyle` puestos sobre el propio
`AnimatedPressable`: se dibujaba perfecto y `onPress` no se disparaba nunca —el
formulario de la bitácora quedaba imposible de enviar y no había ningún error en
consola—. La regla que salió de ahí: **el `Pressable` escucha el toque y una
`View` interna se pinta y se anima**. Ver `ui/Button.tsx` y el velo de
`ui/BottomSheet.tsx`. Un `Pressable` que solo anima un `scale` sí funciona con
todo en el mismo nodo (el disparador de `ui/Select.tsx`); lo que rompe es
sumarle el fondo.

**Apagar el scroll también apaga la evasión del teclado.** Para que el login
no se pudiera arrastrar se le puso `scrollEnabled={false}` mientras el teclado
estaba cerrado, y el campo de contraseña pasó a quedar tapado por el teclado: la
`KeyboardAwareScrollView` corre el contenido por el mismo `ScrollView`, así que
deshabilitarlo la deja sin poder hacer su trabajo, y habilitarlo en
`keyboardWillShow` llega tarde. Si una pantalla no se tiene que poder arrastrar,
va por `bounces` / `overScrollMode` y con el contenido midiendo exactamente una
pantalla — no por `scrollEnabled`.

**`mode="layout"` no sirve con hijos `flex-1`.** La
`KeyboardAwareScrollView` en ese modo agrega un espaciador como último hijo del
scroll. En el login, la hoja blanca es `flex-1` dentro de un `flexGrow: 1`: el
espaciador no estiraba el contenido, le robaba alto a la hoja, el total seguía
midiendo una pantalla y no había nada que scrollear. `mode="insets"` (el default
de la librería, y lo que usa `ui/Screen.tsx`) no toca el layout.

**El `TextInput` no llena su cuadro.** Mide lo que mide su línea de texto —unos
22 pt— y vive centrado en una caja de 56, así que tocar arriba, abajo o sobre el
ícono no enfocaba nada y había que apuntar a la franja del medio. `ui/Field.tsx`
resuelve el cuadro con un `Pressable` que reenvía el toque al input.

**El alto de la tab bar se mide, no se asume.** Era una constante de 76 pt y con
el Dynamic Type grande la barra crecía y tapaba la última card de cada pantalla.
Ahora `TabBar` se mide con `onLayout` y publica su alto por contexto;
`Screen` lo lee solo. Si agregás una pantalla dentro de `(tabs)`, no le pases
ningún inset de abajo: ya viene resuelto.

**Las pantallas presentadas como modal no llevan el inset de arriba.** La hoja
ya arranca debajo del notch; sumarle `insets.top` deja 60 pt de hueco muerto.
Se apaga con `<Screen topInset={false}>`.

**La app se corre como development build; instalar con pnpm la rompe.**
`react-native-css` sólo compila con `lightningcss` 1.30.1, y el pin está en el
campo `overrides` de `package.json`, que pnpm ignora. Un `pnpm install` acá
mete `lightningcss` 1.33.0 y el bundler falla con `failed to deserialize;
expected an object-like struct named Specifier, found ()` — un error que no
menciona ni a pnpm ni a la versión. Este paquete se instala con **npm**. Hay un
espejo del pin en `pnpm.overrides` como red, pero el lock que vale es
`package-lock.json`.

## Estructura

```
src/
  app/            rutas (expo-router)
    (tabs)/       Inicio · Mi dispositivo · Historial · Perfil
    login.tsx     entrada por email o DNI
    report.tsx    formulario de la bitácora (modal): las opciones no se
                  dibujan en la pantalla, cada sección abre un `ui/Select`
  components/
    ui/           primitivos propios (incluidos `Select` y su `BottomSheet`)
    TabBar.tsx    barra de pestañas + el contexto con su alto real
    BrandMark.tsx la marca (trazo de ECG), en código y no como PNG
  features/
    auth/         sesión: Bearer en memoria + refresh en SecureStore
    patient/      API `/mobile`, hooks de TanStack Query, catálogos
    notifications/ permisos, registro del token de Expo y ruteo del push
  lib/            cliente HTTP, formateo, almacenamiento seguro,
                  gradientes, haptics y helpers de animación
  tw/             wrappers de react-native-css (incluidos los animados)
scripts/
  generate-icons.py   ícono de la app y logo del splash, desde la misma
                      fuente de Ionicons que usa `BrandMark`
```

**Estados de error.** Toda query que se dibuje tiene que distinguir "vacío" de
"falló". El patrón es `isError && !data` → `ui/ErrorState`, y si hay datos en
caché se siguen mostrando aunque el refetch falle. No es un detalle de estilo:
sin esto, las pantallas caían en su estado vacío y le decían al paciente
"todavía no tenés chaleco" cuando lo único que pasaba era que el celular estaba
sin señal.

## Tests

`npm test` corre Vitest sobre **lógica pura**: validación del formulario, a
dónde lleva cada notificación, formateo de fechas. No hay tests de render — el
preset de Jest de Expo no convive con Vitest, y lo que se rompe en silencio en
esta app no es el markup sino esas tres cosas.
