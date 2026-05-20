# Análisis del repo `sirius/ican` como base para la plataforma de la tesis

## Contexto

Necesitás decidir si conviene usar [sirius/ican](/Users/tserra/Documents/git/sirius/ican) como base para construir la plataforma del Holter ECG (dashboard médico + eventual app móvil para pacientes), o arrancar de cero. El repo `ican` es una plataforma médica real, en producción, orientada a **oncología** (seguimiento de pacientes con cáncer): formularios de síntomas, estudios médicos en PDF analizados por IA, métricas biométricas discretas. El stack actual en producción usa Auth0, AWS, OpenAI.

El repo está dividido en tres:
- `ican-backend` — API REST en **Java 20 / Spring Boot 3.1.2**
- `ican-web` — SPA en **React 18 + CRA + TypeScript**
- `ican-mobile` — app **Expo SDK 51 + React Native 0.74 + TypeScript** (publicada como "WeCan" en App Store y Play Store, package `com.wecan.hua`).

---

# Backend (`ican-backend`)

## Stack tecnológico

| Capa | Tech |
|---|---|
| Lenguaje / runtime | **Java 20** |
| Framework | **Spring Boot 3.1.2** (Spring Web, Spring Security, Spring Data JPA) |
| Build | Gradle |
| ORM | Hibernate / JPA |
| DB | **PostgreSQL** (prod) + H2 (tests) |
| Migraciones | **Flyway** (29 versiones) |
| Auth | **Auth0 + OAuth2 Resource Server**, JWT |
| Docs API | Springdoc OpenAPI / Swagger |
| Cloud | AWS SDK (SQS, SNS, SES) |
| Email | SendGrid + AWS SES |
| Push | AWS SNS (PlatformToken por device) |
| IA | OpenAI API (parsing de PDFs) |
| Parsing archivos | Apache Tika |
| Observabilidad | Sentry |
| Deploy | Docker + ECR + EC2 (GitHub Actions, 3 entornos: dev/staging/prod) |
| Tests | Mínimos (~9 clases) |

## Qué se puede reutilizar

**Reusable con baja fricción:**
- **Modelo de usuarios y roles** (`UserEntity`, `PatientProfileEntity`, `DoctorProfileEntity`, `AssistantProfileEntity`, `DoctorAdmin`) — la jerarquía paciente / médico / asistente está bien resuelta, con relación `patient.doctorId` y `FixedPatientEntity` para asignaciones fijas.
- **Auth con Auth0 + JWT** (`auth/config/SecurityConfig.java`) — login social, refresh, route guards, scopes por rol (`SCOPE_Patient`, `SCOPE_Admin`, `SCOPE_DoctorAdmin`).
- **Sistema de notificaciones**: AWS SNS para push, SendGrid + SES para email, scheduling con `@Scheduled` (`NotificationSchedule`, `FormSchedules`).
- **Patrón de upload binario async**: `/report/analyze/{patientId}` recibe `byte[]`, lo procesa con OpenAI/Tika, persiste resultado. Sirve como template para upload del batch ECG.
- **CI/CD listo**: GitHub Actions con build → ECR → EC2 para 3 ambientes.

**Reusable con adaptación:**
- **Framework de formularios** (`FormEntity`, `FormAnswerEntity`, tipos `DAILY/WEEKLY/BIOMETRIC`) — se puede extender para encuestas de bienestar al paciente.
- **Endpoint de métricas** (`/metric/answer`) — para vitals discretos (peso, presión).

## Qué NO sirve (o requiere reemplazo importante)

- **Modelo de datos de oncología** (DiseaseEntity, TreatmentEntity, MedicationEntity, DiseaseSpecResponseEntity, SymptomEntity con recomendaciones por IA) — todo esto es ruido para la tesis y suma deuda de mantenimiento.
- **Storage time-series**: NO existe. `MedicalStudy` guarda archivos como BLOB en Postgres, sin índice por tiempo. Para ECG necesitás un esquema nuevo (o S3 + tabla `ecg_batch`/`ecg_sample` indexada por `patientId, timestamp`). El repo no propone solución a esto.
- **Pipeline de ECG**: parsing de waveform, detección de QRS, alertas por arritmia — no existe nada equivalente.
- **OpenAI para extraer texto de PDFs**: no aplica al caso ECG.
- **Auth0** es un servicio pago. Para tesis quizás conviene algo más liviano (Spring Security con users locales, o Keycloak self-hosted) — desacoplar Auth0 implica tocar todo el flujo de seguridad.

## Choque con el stack planeado de la tesis

Tu `CLAUDE.md` (`info del proyecto/04-cloud.md`) define el backend de la tesis como **FastAPI + PostgreSQL + S3** (Python). El backend de `ican` es **Java + Spring Boot**. Adoptar `ican` implica cambiar la decisión de stack del anteproyecto. Esto no es trivial:
- Spring Boot pesa más (más boilerplate, JVM, build más lento) que FastAPI.
- Para una tesis con dos personas y 200 hs c/u, Java agrega curva de aprendizaje y verbosidad.
- El equipo de Biomédica no necesita tocar el backend, así que la elección es 100% del equipo de Informática.

---

# Frontend (`ican-web`)

## Stack tecnológico

| Capa | Tech |
|---|---|
| Framework | **React 18.2 + Create React App (CRA)** |
| Lenguaje | TypeScript 4.0 |
| Routing | React Router v6 |
| State / data fetching | **Redux Toolkit + RTK Query + Redux Persist** |
| UI | **Material-UI v5** + styled-components |
| Charts | **ApexCharts** (`react-apexcharts`) + reaviz |
| Auth | Auth0 (`@auth0/auth0-react`) |
| Forms / utils | lodash, moment.js, react-toastify |
| Observabilidad | Sentry |
| Lint / format | ESLint + Prettier + Husky pre-commit |
| Deploy | GitHub Actions (dev/stg/prod) |
| **Mobile app** | Existe en `ican-mobile` (ver sección siguiente) |

## Qué se puede reutilizar

**Para el dashboard del médico (alta reusabilidad):**
- **Páginas que ya existen** y mapean casi 1:1 a lo que necesitás:
  - `pages/PatientList/PatientListScreen` — lista de pacientes con búsqueda
  - `pages/PatientProfile/ProfileScreen` — detalle de paciente
  - `pages/Home/HomeScreen` — KPIs / dashboard general
  - `pages/RecentReports` — reportes recientes
  - `pages/MyTeam` y `pages/DoctorsAcceptance` — gestión de equipo médico (útil para invitar médicos)
- **Componentes de gráfico**: `MetricChart`, `BiometricChart` (multi-serie temporal) — directamente aplicables a HR, HRV, etc. ApexCharts soporta multi-serie line plot, lo cual sirve para ECG **agregado** (HR/min, HRV cada N seg). Para waveform crudo (250 Hz, miles de puntos) ApexCharts se queda corto y se suele usar **WebGL/canvas** (uPlot, Plotly WebGL, o D3 + canvas).
- **Componentes base**: `TableComponent`, `Card`, `Button`, `Input`, `Dropdown`, `Wrapper` (layout con navbar), `Toast`, `Loader`, `ProtectedRoute`.
- **Capa de API tipada** (`src/redux/api/patientApi.ts`, etc.) — RTK Query con tipos compartibles si después armás la app móvil.
- **Tema/UI ya en español** (login, forms, navegación) — el repo está localizado para AR.

**Esfuerzo estimado para adaptar el dashboard**: medio. La estructura de "doctor ve listado, entra a perfil de paciente, ve gráficos temporales" ya existe — sumar una pantalla de "ECG viewer" es una página más con un chart custom.

## Qué NO sirve / hay que rehacer

- **Toda la lógica de oncología en UI**: pantallas de síntomas con recomendaciones por IA, formularios de tratamiento, estudios médicos PDF — son features que hay que **borrar**, no adaptar.
- **Charting de waveform ECG en alta resolución**: ApexCharts está pensado para dashboards, no para mostrar 250 muestras/seg en scroll continuo. Hay que sumar `uPlot` u otra lib WebGL — esto es trabajo nuevo (~1-2 semanas).
- **i18n**: no existe. Strings hardcoded en español. Si querés multi-idioma hay que sumar `react-i18next`.
- **CRA está deprecado**: el equipo de React recomienda Vite o Next.js desde 2023. El `CLAUDE.md` plantea **Next.js**. Migrar de CRA a Next.js es un cambio importante (routing, SSR, build).

## App móvil para pacientes — punto crítico

**El frontend web (`ican-web`) no contiene una app móvil**, pero **sí existe una app móvil separada en `ican-mobile`** (analizada abajo). Lo único reusable del web hacia mobile sería:
- Tipos TypeScript del API (`src/redux/api/types.ts`) — copiables a un repo Expo (de hecho, `ican-mobile` los re-define).
- Patrón de auth con Auth0 (existe `@auth0/auth0-react-native`, y `ican-mobile` ya lo usa).
- **No reutilizable**: ningún componente UI (MUI → React Native no es portable).

**Importante**: tu `CLAUDE.md` explícitamente dice "**no aplica — sin app móvil en la arquitectura actual**" (el dispositivo es standalone vía SIM). Si vas a agregar app móvil, primero conviene reabrir esa decisión con tu director — porque agregarla aumenta el alcance de la tesis significativamente y no estaba planeada.

---

# Mobile (`ican-mobile`)

## Stack tecnológico

| Capa | Tech |
|---|---|
| Runtime / SDK | **Expo SDK 51** + **React Native 0.74.5** |
| Lenguaje | TypeScript 5.3 |
| Routing | **expo-router 3.5** (file-based, con grupos `(authenticated)` / `(not_authenticated)`) |
| State / data fetching | **Redux Toolkit + RTK Query + Redux Persist** (AsyncStorage; whitelist `auth`) |
| UI | **styled-components 6**, componentes propios (`Typography`, `Button`, `Textfield`, `Dropdown`, `Slider`, `DatePicker`) |
| Listas | `@shopify/flash-list` |
| Bottom sheets | `@gorhom/bottom-sheet` |
| Forms / validación | **react-hook-form + zod + @hookform/resolvers** |
| Auth | **react-native-auth0** (tenant `wecan-prod.us.auth0.com`, audience `http://ican.com/api`) |
| Storage seguro | **expo-secure-store** (accessToken, FCMToken) |
| Push | **@react-native-firebase/messaging** (FCM) + `expo-notifications` (local + foreground handler) |
| Permisos OS | flujos custom por plataforma en `utils/notifications.ts` |
| Observabilidad | **LogRocket** (`@logrocket/react-native`) — pago, distinto a `ican-web` que usa Sentry |
| Build / deploy | **EAS Build** (`development`, `development-simulator`, `preview`, `production`); canales OTA con `expo-updates` |
| Config por entorno | `EXPO_PUBLIC_*` (base URL dev/prod hardcodeada vía `eas.json`) |
| CI | GitHub Actions (`.github/workflows/ci.yaml`) — lint/format + jest |
| Tests | Mínimos (jest-expo + testing-library); ~1 carpeta `test/` |
| Lint / format | ESLint flat config + Prettier + Husky + lint-staged |

## Estructura de la app

```
app/
  index.tsx                    # decide ruta inicial según auth
  _layout.tsx                  # provider tree (Redux, Auth0, Notifications, Theme)
  (not_authenticated)/         # check / error / in_progress (landing pre-login)
  (authenticated)/             # home, profile, daily, quality, survey, symptoms, terms
  register/                    # onboarding multi-step
components/
  common/                      # Typography, Button, Textfield, Dropdown, Slider, Header, DatePicker
  home/                        # BiometricCard, MetricModule, QuestionnaireCard, RegistrationBottomSheet
  questionnaire/, quality/, symptoms/, register/   # features de oncología
hoc/                           # withModal, withToast (HOC pattern + slots por nombre)
hooks/                         # useCustomAuth0, useLogout, useResource, useTerms, useQualityResource
providers/NotificationsProvider.tsx   # FCM + expo-notifications + register PlatformToken
redux/
  services/                    # RTK Query slices: api.ts + authentication / profile / metric / form / register / notifications / survey / symptoms-questionnaire / quality-questionnaire / general-questionaire / featureflag
  slices/                      # auth, register, form, utils, *-questionnaire
  store.ts                     # persistedReducer (whitelist auth), interceptor 401 → logout
utils/                         # secureStore, notifications (perm flows), platform, BuildProviderThree
```

## Qué se puede reutilizar

**Reusable con baja fricción (si la tesis suma app móvil de paciente):**
- **Patrón de routing con expo-router + grupos**: `(authenticated)` vs `(not_authenticated)` + `_layout.tsx` decidiendo según token en SecureStore — directamente aplicable a app de paciente Holter.
- **`useCustomAuth0`** (`hooks/useCustomAuth0.tsx`): envuelve `useAuth0` con `audience` y `redirectUri` resuelto por plataforma — patrón limpio para login social en iOS/Android.
- **Capa de RTK Query** (`redux/services/api.ts`): base query con `prepareHeaders` que lee token de `SecureStore`, interceptor que despacha logout en 401, `tagTypes` para invalidación. Es el template que vos querrías para una app que consume tu FastAPI.
- **`NotificationsProvider`**: pide permisos por plataforma, obtiene FCM token, lo persiste en SecureStore, lo registra en backend con `expiry`, maneja `onMessage` / `setBackgroundMessageHandler` / `getInitialNotification`. Si el dispositivo Holter genera alertas (arritmia detectada → notificar al paciente), este flujo es 1:1.
- **`secureStore.ts`** + helpers `getValueFor / save / deleteToken` — wrapper estándar.
- **`BiometricCard` / `MetricModule`**: tarjetas para mostrar peso, presión, etc. — directamente aplicables a HR / HRV / SpO2 si la app expone vitals al paciente.
- **Forms con react-hook-form + zod** (`components/register/DynamicControl` + `formComponents`): patrón de "schema-driven form" muy limpio, mejor que lo que ofrece el web.
- **HOC `withModal` / `withToast`**: registro nombrado de modales/toasts, accesibles desde cualquier pantalla — patrón útil.
- **EAS Build con tres perfiles** + canales OTA (`expo-updates`) — pipeline mobile listo para copiar.

**Reusable con adaptación:**
- **Framework de cuestionarios** (`components/questionnaire/QuestionnaireScreen`, `RenderQuestionnaireOptions`, hook `useQuestionnaire`): si en la tesis se decide pedir al paciente encuestas de bienestar / síntomas autopercibidos, el motor (preguntas, respuestas, progreso, footer) es genérico.
- **Onboarding multi-step** (`app/register` + `components/register/steps`): patrón de wizard con react-hook-form que se podría reusar para registro del paciente.

## Qué NO sirve (o requiere reemplazo)

- **Toda la lógica de oncología**: `quality`, `symptoms`, `survey`, `daily` — pantallas, slices, services, components. Es ruido para una app de paciente Holter.
- **Backend acoplado a Java + Auth0**: `redux/services/api.ts` apunta a `https://api.wecan-healthcare.com` y `useCustomAuth0` apunta a `wecan-prod.us.auth0.com` con audience `http://ican.com/api`. Si la tesis va con FastAPI + auth propio, hay que reemplazar el cliente Auth0 entero (token storage queda igual, pero el flujo de login cambia).
- **LogRocket**: pago. Reemplazar por Sentry (alineado con `ican-web`) o nada en una tesis.
- **Firebase Cloud Messaging**: requiere `google-services.json` + `GoogleService-Info.plist` + cuenta Firebase + backend que mande push vía FCM. Para tesis con dispositivo standalone vía SIM, **probablemente no aplica**: no hay servidor que tenga razones de pushear al paciente, salvo que se decida un canal "el médico vio un evento crítico → notificar al paciente". Si no, sacar.
- **Tests prácticamente ausentes**: no se reutiliza nada de testing.
- **`BuildProviderThree.tsx`**: nombre/patrón confuso de composición de providers — preferir un wrapper limpio.

## Choque con el stack planeado de la tesis

- Tu `CLAUDE.md` (línea explícita en arquitectura) dice **"no aplica — sin app móvil en la arquitectura actual"**. El dispositivo Holter es 100% standalone vía SIM, no requiere app del paciente ni para datos ni para configuración.
- Si más adelante se decide sumar app móvil (p.ej. para que el paciente vea su propio estado, reciba alertas, o gestione el consentimiento), **Expo + React Native es el stack natural** y `ican-mobile` es una referencia decente. Pero hoy es alcance fuera de plan.
- Auth0 sigue siendo dependencia paga — mismo argumento que para backend/web: para tesis conviene auth liviano (FastAPI-Users / JWT propio).

---

# Veredicto: ¿fork o desde cero?

## Resumen comparativo

| Criterio | Fork de `ican` | Desde cero |
|---|---|---|
| Stack alineado al `CLAUDE.md` (FastAPI + Next.js) | ❌ Java + CRA | ✅ |
| Modelo dominio aplicable | ⚠ Parcial (auth/users/notif sí; oncología no) | ✅ |
| Time-series ECG | ❌ Hay que diseñar de cero igual | ✅ |
| Auth + roles paciente/médico | ✅ Reutilizable | Hay que armar (1-2 semanas) |
| Dashboard médico (UI base) | ✅ Plantillas listas | Hay que armar (3-4 semanas) |
| Charts time-series simples | ✅ ApexCharts ya integrado | Trivial sumar |
| Charts ECG waveform | ❌ Hay que sumar lib WebGL igual | ❌ Igual |
| App móvil (si se decide sumarla) | ⚠ Existe `ican-mobile` (Expo + RN + Auth0): patrones reusables, dominio oncología a borrar | Hay que armar (~4-6 semanas) |
| Costo de aprender el código ajeno | Alto (Spring Boot + Auth0 + AWS + repo grande) | — |
| Costo de mantener features que no usás | Alto (oncología en todo el modelo) | — |
| Defensa académica del trabajo propio | Más débil ("portamos un repo existente") | Más fuerte ("diseñamos la plataforma") |
| Velocidad inicial | Rápido (primeras 2 semanas) | Más lento al arranque |
| Velocidad sostenida | Se frena (deuda heredada) | Constante |

## Recomendación: **construir desde cero, usando `ican` como referencia de patrones**

**Por qué desde cero gana en este caso:**

1. **Stack mismatch**. El repo es Java + CRA + Auth0 + AWS pesado. Tu `CLAUDE.md` decidió FastAPI + Next.js + S3. Cambiar de stack es más caro que reusar las ideas.
2. **Dominio mismatch**. Es una plataforma de oncología con estudios médicos PDF + IA. Vos tenés señal ECG continua time-series. Casi todo el modelo de datos sobra y estorba.
3. **Lo más valioso del repo (auth + roles + notif) son ~2 semanas de trabajo en FastAPI**, no justifican adoptar todo el stack ajeno.
4. **El time-series de ECG, que es el core de tu tesis, hay que diseñarlo de cero igual** — el repo no aporta nada ahí.
5. **Para una tesis, la defensa académica importa**: es mucho más sólido decir "diseñamos un backend FastAPI con esquema time-series para ingestión batch" que "forkeamos una plataforma de oncología y le sacamos cosas".
6. **La app móvil sí existe (`ican-mobile`) pero su valor es marginal**: el stack (Expo + RN) es razonable y hay patrones reusables (expo-router con grupos auth, RTK Query + SecureStore, NotificationsProvider con FCM, forms con react-hook-form + zod), pero el dominio está atado a oncología y el backend está atado a Auth0 + Java. Además, el `CLAUDE.md` define el dispositivo como standalone vía SIM, así que la app móvil hoy está fuera de alcance — antes de evaluar fork conviene definir si va o no.

**Pero usá `ican` como referencia (no fork) para:**
- Cómo modelar la relación `User ↔ DoctorProfile / PatientProfile` con roles.
- El esquema de invitaciones de médicos (`DoctorsAcceptance`, `MyTeam`).
- Endpoints típicos del dashboard (`/patients`, `/patients/{id}/metrics`, etc.).
- Layout y navegación del dashboard en React (la estructura de `Wrapper` + sidebar + páginas).
- Configuración de gráficos con ApexCharts (`utils/chartsConfigs.ts`) — copiable conceptualmente a Next.js.
- Los flujos de notificación (push + email + scheduling).
- **De `ican-mobile`** (sólo si se decide sumar app de paciente): patrón expo-router con grupos `(authenticated)/(not_authenticated)`, `useCustomAuth0` con redirectUri por plataforma, RTK Query con interceptor 401, `NotificationsProvider` con FCM + registro de PlatformToken contra backend, perfiles de EAS Build + canales OTA.

## Stack sugerido para la tesis (alineado al `CLAUDE.md`)

| Capa | Recomendado | Justificación |
|---|---|---|
| Backend | **FastAPI + SQLAlchemy + Alembic** | Ya está decidido en `04-cloud.md`; Python alivia carga vs Java |
| DB | **PostgreSQL** + **TimescaleDB** | Postgres ya decidido; TimescaleDB es extensión que da hypertables time-series sin cambiar SQL — clave para ECG |
| Storage batches ECG crudos | **S3** | Ya decidido; S3 + metadata en Postgres, no BLOBs en DB |
| Auth | **FastAPI-Users** o **Authlib + JWT** | Más liviano que Auth0; sin dependencia paga |
| Frontend dashboard | **Next.js 15 + TypeScript + Tailwind + shadcn/ui** | Next.js ya decidido; shadcn/ui evita el peso de MUI |
| Charts dashboard | **Recharts** (KPIs) + **uPlot** (waveform ECG) | uPlot es la lib estándar para waveform de alta densidad |
| Data fetching FE | **TanStack Query** (React Query) | Reemplazo moderno y liviano de RTK Query |
| App móvil (si va) | **Expo + React Native** | Único camino razonable hoy para iOS+Android con un solo equipo |
| Observabilidad | **Sentry** | Idem `ican` |
| CI/CD | **GitHub Actions + Fly.io / Railway** | Más simple que ECR+EC2 para el alcance de la tesis |

---

# Próximos pasos sugeridos

1. **Reabrir la decisión sobre app móvil** con tu director — confirmar si va o no, porque cambia el alcance de la tesis ~30-40%.
2. **Bajar `ican` a un documento de referencia** (capturar 5-10 patrones que querés copiar conceptualmente) y archivar la idea de fork.
3. **Empezar el backend FastAPI** con: schema users/roles, endpoint de upload batch ECG (POST `/ecg/batches` con multipart o presigned URL S3), schema time-series con TimescaleDB.
4. **Stub del dashboard Next.js** con login, lista de pacientes, perfil de paciente con un chart placeholder — para tener feedback visual temprano.
