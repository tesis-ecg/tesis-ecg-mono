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
nativas o los plugins de `app.config.ts`; `ios/` y `android/` son carpetas
generadas (están en `.gitignore`) y se regeneran con `npx expo prebuild`.

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

## Correr contra el backend de producción

```
EXPO_PUBLIC_API_URL=https://tesis-ecg.vercel.app/api
```

**El `/api` no es opcional.** `tesis-ecg.vercel.app` es el proyecto Vercel del
*portal*, que reescribe `/api/*` al backend (ver `docs/adr/001-same-origin-api-proxy.md`).
Sin el sufijo, `POST /mobile/auth/login` cae en el fallback SPA y devuelve 405.

`EXPO_PUBLIC_*` se **inlinea en el bundle**: no alcanza con editar `.env`, hay
que rebundlear (`npx expo start --dev-client -c`) o rebuildear.

Y como `.env` está gitignoreado, **EAS Build no lo sube**. Para los builds de
EAS la variable vive en el bloque `env` de los perfiles `preview` y
`production` de `eas.json`.

Qué se puede ejercitar contra producción y qué no:

- **Chaleco mal colocado**: sí. El simulador manda `POST /ingest/device-status`,
  que se autentica con la credencial del equipo y no está gateado por entorno.
- **Anomalía detectada**: no. `POST /studies/{id}/simulate-anomaly` corta con
  403 cuando `ENVIRONMENT` es `production` o `preview`
  (`is_secure_environment`, en `studies_service.py`). Ese aviso solo se puede
  disparar contra un backend en `development`.

## Builds con EAS

Los perfiles viven en `eas.json`. Cada build sale firmado desde EAS y se
instala directo en el celular, sin cadena de Xcode ni Android Studio.

| Perfil | Android | iOS | Uso |
|---|---|---|---|
| `development` | APK con dev-client | App con dev-client | Debug con Metro en un device físico |
| `preview` | APK (`buildType: apk`) | Build interno | Compartir para probar; se instala por link |
| `production` | AAB para Play Store | IPA para App Store | Release. Auto-incrementa `versionCode` / `buildNumber` |

`EXPO_PUBLIC_API_URL` viaja **inlineado en el bundle**, así que cada perfil lo
declara en su bloque `env` de `eas.json` — `.env` local no llega al build de
EAS porque está gitignoreado.

### Archivos de Firebase (google-services.json y GoogleService-Info.plist)

Los archivos de configuración de Firebase quedan **fuera de git** (están en
`mobile/.gitignore`) y viajan a EAS como *file environment variables*
encriptadas. `app.config.ts` las lee con fallback al archivo local, así que el
mismo config sirve para `expo prebuild` local y para los builds de EAS:

```ts
android: {
  googleServicesFile: process.env.GOOGLE_SERVICES_JSON ?? './google-services.json',
  ...
}
```

Estado esperado en el disco (para dev local y `expo prebuild`):

```
mobile/google-services.json        # Android
mobile/GoogleService-Info.plist    # iOS — solo si se usa Firebase en iOS (ver abajo)
```

**Ver qué está subido a EAS**:

```bash
npx eas env:list --environment preview
npx eas env:list --environment production
```

**Subir o actualizar** el archivo (mismo comando; ya está hecho para Android):

```bash
# Android
npx eas env:set --scope project --name GOOGLE_SERVICES_JSON \
  --type file --value ./google-services.json --visibility secret \
  --environment preview --environment production
```

Corré ese comando cuando Firebase regenera el archivo, o cuando cambia el
`package` / `bundleIdentifier`. `env:set` crea la variable si no existe y la
sobreescribe si ya estaba. Reemplazá también la copia local
(`mobile/google-services.json`) para que los builds locales usen el mismo
archivo.

**iOS**: hoy la app **no necesita `GoogleService-Info.plist`**. Los pushes en
iOS salen por APNs a través del Expo Push Service — las credenciales (APNs key,
provisioning profile) las maneja EAS con `npx eas credentials`, no como archivo
en el repo. Si más adelante se suma Firebase Analytics / Crashlytics para iOS,
el patrón es idéntico al de Android:

```bash
# iOS — solo si se agrega Firebase iOS SDK
npx eas env:set --scope project --name GOOGLE_SERVICES_INFO_PLIST \
  --type file --value ./GoogleService-Info.plist --visibility secret \
  --environment preview --environment production
```

Y sumar la línea correspondiente a `app.config.ts`:

```ts
ios: {
  googleServicesFile: process.env.GOOGLE_SERVICES_INFO_PLIST ?? './GoogleService-Info.plist',
  ...
}
```

### Correr un build

```bash
# Preview — APK de Android instalable por link
npx eas build -p android --profile preview

# Preview — iOS (necesita cuenta paga de Apple Developer; el device tiene que
# estar registrado con `npx eas device:create` antes del primer build)
npx eas build -p ios --profile preview

# Production
npx eas build -p android --profile production
npx eas build -p ios --profile production

# Ambas plataformas en un solo comando
npx eas build --profile preview
npx eas build --profile production
```

Cada build imprime una URL con el progreso. Al terminar, Android muestra un
botón **Install** con QR — se abre desde el celular y se instala el APK.
El `version` (nombre visible) de `app.config.ts` se sube a mano por cada
release; `versionCode` y `buildNumber` los auto-incrementa el perfil
`production`.

## Notificaciones push

**Expo Go no alcanza.** Desde SDK 53 Expo Go no entrega notificaciones en
Android, así que hace falta un *development build*.

El backend manda los avisos a través del Expo Push Service
(`back/app/core/push.py`). En desarrollo queda apagado por defecto:
`EXPO_PUSH_ENABLED=false` usa un sender que solo escribe en el log lo que
habría mandado —incluido el `data` completo—, así que todo el flujo del backend
se puede depurar sin red y sin credenciales.

### Probarlas de punta a punta: emulador de Android

Es el único camino gratis con entrega real. iOS necesita cuenta paga de Apple
Developer para la key de APNs; ver el apéndice de abajo para la alternativa.

1. **Backend**: `EXPO_PUSH_ENABLED=true` en `back/.env` y reiniciar el
   contenedor. El paciente de prueba tiene que tener cuenta de app
   (`patient.user_id` no nulo).
2. **Proyecto de EAS** (gratis): `npx eas init && npx eas build:configure`.
   `eas init` escribe `extra.eas.projectId` en `app.config.ts`; sin eso
   `getExpoPushTokenAsync` falla con "No projectId found".
3. **Firebase** (plan Spark, gratis): proyecto nuevo → app Android con el
   package `com.holter.ecg` → bajar `google-services.json` a
   `mobile/`. `app.config.ts` ya lo lee desde ahí para builds locales; para
   los builds de EAS hay que subirlo además como env var — ver
   [Builds con EAS](#builds-con-eas).
4. **Credenciales FCM V1**: en Firebase, Configuración del proyecto → Cuentas de
   servicio → generar clave privada. Subirla con `npx eas credentials` →
   Android → Push Notifications (FCM V1).
5. **Recompilar**: `npx expo prebuild -p android --clean && npm run android`.
   El AVD tiene que ser una **imagen con Google Play** (ícono de Play Store en
   el AVD Manager): sin Play services no hay FCM.

Comprobación de que el registro funcionó, antes de buscar el problema en otro
lado:

```bash
docker compose exec db psql -U holter -d holter -c "select platform, left(token,30) from push_token where deleted_at is null;"
```

Sin fila ahí, ningún aviso va a llegar. Los logs del backend distinguen los
casos: `push_no_tokens` (falta el registro), `push_skipped`
(`EXPO_PUSH_ENABLED=false`) y `push_dispatched`, que es el envío real.

**`push_dispatched` no quiere decir que llegó.** Expo contesta `200 OK` a la
request entera y después acepta o rechaza **mensaje por mensaje**, así que hay
que mirar los conteos y no solo el nombre del evento:

```
push_dispatched sent=0 failed=1 errors=['InvalidCredentials']
```

Con `sent=0` y `errors=['InvalidCredentials']`, el problema no está en este
repo: el proyecto de EAS no tiene cargada la clave de servicio de FCM V1 —el
paso 4 de arriba— y Expo no tiene con qué entregarle a Android. Se verifica con
`npx eas credentials -p android`, en *Push Notifications (FCM V1)*. El otro
error frecuente es `DeviceNotRegistered`: el token quedó viejo (se reinstaló la
app), el backend lo da de baja solo y alcanza con volver a abrir la app.

### Disparar los avisos sin hardware

Los dos tipos de aviso se disparan desde el **simulador de chalecos** del portal
(`/__sim/vest`, solo admin), en el panel "Avisos al paciente" de cada chaleco:

- **Chaleco mal colocado**: el botón reporta por `POST /ingest/device-status` con
  la credencial del equipo. Vuelve a marcarlo bien colocado y los carteles rojos
  de *Inicio* y de *Dispositivo* se apagan — el estado sale de `vestPlacement`,
  que el backend guarda en la fila del Holter.
- **Anomalía detectada**: crea un hallazgo anclado dentro de la señal ya subida
  (hace falta haber mandado al menos un lote). El push abre el formulario de la
  bitácora en el momento del hallazgo, y la respuesta aparece en el estudio del
  portal, en la tabla y como marca sobre el ECG.

### Apéndice: simulador de iOS

Un simulador de iOS **no recibe pushes del Expo Push Service** sin cuenta paga
de Apple Developer. Lo que sí se puede sin nada de eso es inyectar el aviso
localmente, que alcanza para probar el tap, el ruteo y el formulario:

```bash
echo '{"aps":{"alert":{"title":"Registrá cómo te sentís","body":"Detectamos algo en tu registro."},"sound":"default"},"body":{"type":"report_request","alertId":"<UUID>","occurredAt":"2026-09-01T14:30:00Z"}}' | xcrun simctl push booted com.holter.ecg -
```

El `data` de un push remoto sale de la clave **`body`** de nivel superior del
payload APNs, no de `aps` — así lo lee `expo-notifications`. El `alertId` real
lo devuelve el endpoint que disparó el aviso, y también sale del log
`push_skipped` del backend. Para el otro aviso, `"type":"vest_misplaced"`.
Requiere que la app haya pedido el permiso de notificaciones (Perfil → activar).

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
