# App móvil del paciente

App de acompañamiento del paciente del Holter ECG. Corre en iOS y Android desde
el mismo código (Expo + React Native) y habla con **el mismo backend que el
portal médico**, bajo el prefijo `/mobile`.

## Qué hace

- El paciente entra con su **email o su DNI** y la contraseña que le generó su
  médico desde el portal.
- Ve el estado de su chaleco: batería, último envío, si está grabando.
- Recibe **notificaciones push** cuando el chaleco queda mal colocado o cuando
  el sistema detecta algo para revisar.
- Conserva un centro paginado con todos los avisos y el estado de sus respuestas.
- Completa un formulario corto —qué sintió y qué estaba haciendo— que el médico
  ve en el estudio, marcado sobre el electrocardiograma en el momento exacto.
- Puede registrar lo mismo por su cuenta, sin aviso previo.

## Correr en desarrollo

La app corre **siempre como development build**, nunca en Expo Go: usa
`expo-dev-client`, notificaciones push y módulos nativos que Expo Go no trae.

```bash
cp .env.example .env      # apuntá EXPO_PUBLIC_API_URL a tu backend
npm install
npm run ios               # o `npm run android`
```

`npm run ios` compila el proyecto nativo, lo instala en el simulador y levanta
Metro. Con la app ya instalada alcanza `npm start`, que arranca Metro en modo
`--dev-client`.

Hay que volver a compilar (`npm run ios`) cada vez que cambien las dependencias
nativas o los plugins de `app.json`; `ios/` y `android/` son carpetas generadas
(están en `.gitignore`) y se regeneran con `npx expo prebuild`.

**Instalá con npm, no con pnpm.** `react-native-css` sólo funciona con
`lightningcss` 1.30.1 y ese pin vive en el campo `overrides` de `package.json`,
que es de npm. Correr `pnpm install` acá trae `lightningcss` 1.33.0 y el
bundling revienta con `failed to deserialize; expected an object-like struct
named Specifier, found ()`. Si pasó: borrá `pnpm-lock.yaml` y `node_modules`, y
volvé a `npm install`.

El backend se levanta desde la raíz del repo (ver `docs/` y el `docker-compose.yml`).
`EXPO_PUBLIC_API_URL` tiene que ser una dirección que el celular o el emulador
pueda alcanzar:

| Dónde corre la app | URL |
|---|---|
| Emulador de Android | `http://10.0.2.2:8000` |
| Simulador de iOS | `http://localhost:8000` |
| Teléfono físico | `http://<IP-de-tu-máquina>:8000` |

## Notificaciones push

**Expo Go no alcanza.** Desde SDK 53 Expo Go no entrega notificaciones en
Android, así que hace falta un *development build*:

```bash
npx eas build --profile development --platform android
```

- **Android**: hay que subir a EAS las credenciales de **FCM V1** (un service
  account de un proyecto de Firebase gratis, plan Spark).
- **iOS**: hace falta cuenta de Apple Developer para las credenciales de APNs.
- Para probar sin hardware sirve un **emulador de Android con Google Play
  services**.

El backend manda los avisos a través del Expo Push Service
(`back/app/core/push.py`). En desarrollo queda apagado por defecto:
`EXPO_PUSH_ENABLED=false` usa un sender que solo escribe en el log lo que
habría mandado.

## Stack y convenciones

- **Expo SDK 57** + **expo-router** (rutas por archivos, en `src/app/`).
- **NativeWind v5 + Tailwind v4** vía `react-native-css`. Los tokens de diseño
  (`src/global.css`) son los mismos del portal (`front/src/styles/tokens.css`):
  si cambia el color de marca allá, cambia acá.
- **Componentes propios** en `src/components/ui/`. No se usan componentes
  nativos de plataforma ni `NativeTabs`: el requisito es que iOS y Android se
  vean lo más parecido posible, y la barra de pestañas está dibujada a mano
  (`src/components/TabBar.tsx`) justamente por eso.
- **Animaciones con Reanimated**, siempre por debajo de 260 ms y respetando
  "Reducir movimiento" del sistema. Los helpers están en `src/lib/motion.ts`.
- **Iconografía Lucide** mediante imports explícitos de `lucide-react-native`.
- **Teclado con `react-native-keyboard-controller`** para mantener el campo
  enfocado visible tanto en iOS como en Android.
- **Gradientes en `src/lib/gradients.ts`**, aplicados por `style`. Las utilities
  de Tailwind no sirven acá — el motivo está en `AGENTS.md`.
- **La marca se dibuja en código** (`src/components/BrandMark.tsx`). El ícono de
  la app y el logo del splash salen de `scripts/generate-icons.py`, que rinde el
  mismo trazado `Activity` de Lucide:

  ```bash
  python3 scripts/generate-icons.py   # regenera icon, splash, favicon, notification y android-*
  ```

  Después de tocar los íconos hace falta `npx expo prebuild -p ios --clean` y
  recompilar: el ícono viaja en el proyecto nativo, no en el bundle de JS.
- La app es **light-only** y respeta el Dynamic Type del sistema. El usuario
  objetivo tiene entre 40 y 70 años: cuerpo de 17 pt para arriba y botones de
  56 pt.
- Los tipos de la API (`src/features/patient/types.ts`) son un espejo a mano de
  `back/app/modules/patient_app/patient_app_schemas.py`. Si cambia un DTO del
  backend, hay que actualizarlos.

### `lightningcss` fijado en 1.30.1

El `overrides` de `package.json` no es decorativo. `react-native-css` y
Tailwind serializan el CSS con lightningcss, y entre versiones cambia el
formato binario: con dos versiones distintas, Metro falla con
`failed to deserialize; expected an object-like struct named Specifier, found ()`
apenas el CSS usa un `var()`. Si en algún momento se levanta la versión, hay
que verificar que `npx expo export --platform android` siga compilando.

## Scripts

| Comando | Qué hace |
|---|---|
| `npm run android` / `npm run ios` | Levanta la app |
| `npm run lint` | ESLint (config de Expo) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest sobre la lógica pura |
| `npm run doctor` | `expo-doctor` |
