# Holter Wearable ECG — Proyecto de Tesis

## Codebase Navigation (read this first)

**For any task involving discovery, search, investigation, or navigation of this codebase — read `docs/CODEBASE_INDEX.md` before exploring files directly.**

The index contains: module ownership, route → service → repository chains, data model graph, cross-app request flows, external integrations, and suggested reading paths per task type. It is the fastest way to find the right file without a broad directory walk.

```
@docs/CODEBASE_INDEX.md
```

## Qué es este proyecto

Proyecto interdisciplinario de Trabajo Final de Grado (TFG) en la Universidad Austral. Dos equipos trabajan en conjunto:
- **Ingeniería en Informática**: Tomás Serra y Martín Barreiro (director: Federico Ruiz) — firmware, app móvil, cloud, dashboard, gestión de datos
- **Ingeniería Biomédica**: Gonzalo Oxoby y Juan Bautista Buthet (director: Dr. Federico Bustos) — hardware, PCB, AFE, electrodos, prenda textil

Se está desarrollando un dispositivo wearable tipo Holter ECG integrado en un chaleco/top textil con electrodos secos, orientado a monitoreo cardíaco preventivo continuo. Este repositorio se enfoca en la parte de Informática.

## Arquitectura del sistema

- **Hardware**: Seeed XIAO **nRF52840** (Cortex-M4F + BLE, **sin WiFi**) + AFE TI **ADS1292R** (2 canales de 24 bits) para adquisición de ECG e impedancia
- **Comunicación principal**: **WiFi del domicilio del paciente** — buffer local continuo + envío batch **cada 10 min** directo al backend por HTTPS, standalone, sin app móvil ni módulo celular. Como el nRF52840 no tiene WiFi, la radio la aporta un **co-procesador ESP32-C3** por UART, encendido solo durante el ciclo de envío
- **Provisioning**: SoftAP + portal cautivo servido por el co-procesador; se configura una vez, en la entrega
- **Almacenamiento local**: flash SPI S25FL128L de **16 MB**, organizada como log circular por slots de trama (**no** hay sistema de archivos ni espacio libre que consultar). La ventana sin conexión depende de cuánto comprima la señal, y eso depende de cómo quede puesto el chaleco: **5,1 h con el chaleco flojo, 7,1 con gel, 8,6 bien puesto** (medido sobre la placa, `../Holter-ECG-System/INTEGRACION.md` §9.1). **Se dimensiona contra las 5,1 h**: un paciente con el chaleco flojo durante 15 días es el caso normal, no el extremo. Las 9,94 h que figuraban acá son de PhysioNet y no valen para este equipo
- **No hay microSD ni la va a haber.** Figuraba como requerimiento abierto hacia Biomédica; el firmware confirmó que esta arquitectura no la contempla (§11.5 punto 5). El equivalente real, y que el equipo sí mide, es cuánto backlog sin confirmar tiene
- **Batería**: Li-Po 3,7 V **1800 mAh**, autonomía estimada **~10 días** (el canal WiFi se lleva solo ~4%)
- **App móvil**: no forma parte del canal de datos. BLE (que el firmware ya implementa) queda para provisioning, verificación de colocación y una eventual app de acompañamiento del paciente
- **Cloud**: FastAPI + PostgreSQL + S3
- **Dashboard médico**: React/Next.js

## Documentación

La arquitectura de comunicación está documentada en `info del proyecto/`:

| Archivo | Contenido |
|---|---|
| `info del proyecto/README.md` | Índice, diagrama general, stack tecnológico |
| `info del proyecto/01-justificacion.md` | WiFi vs SIM vs BLE+App: comparación y decisión de arquitectura |
| `info del proyecto/02-firmware-holter.md` | Flujo de datos, buffer local, máquina de estados |
| `info del proyecto/03-app-movil.md` | Por qué la app no es parte del canal de datos |
| `info del proyecto/04-cloud.md` | FastAPI, modelo de datos, trigger del médico |
| `info del proyecto/05-bateria-y-datos.md` | Consumo, batería, volúmenes, tiempos de transferencia |
| `info del proyecto/06-escenarios-y-seguridad.md` | Escenarios críticos, seguridad, regulatorio |
| `info del proyecto/07-wifi-y-provisioning.md` | Canal WiFi, SoftAP, portal cautivo, ciclo de envío |
| `info del proyecto/08-sim-celular-descartado.md` | Archivo histórico: análisis del canal celular LTE-M |
| `info del proyecto/09-comparativa-canales-de-transmision.md` | **Comparación BLE vs WiFi vs SIM con cuentas de consumo, costos y conclusión** |

## Decisiones de diseño tomadas

- **WiFi del domicilio como único canal de datos**: arquitectura standalone, sin app obligatoria, sin plan de datos. Ver `01-justificacion.md` y las cuentas completas en `09-comparativa-canales-de-transmision.md`
- **No se cambia el MCU.** El firmware validado corre sobre nRF52840, que consume 2-3× menos que un ESP32 grabando de forma continua. Agregar un co-procesador WiFi de ~USD 2,60 conserva la autonomía (10,2 días) y el firmware; migrar a ESP32 la bajaría a ~4,8 días
- **El buffer local es el seguro final**: nunca se borra nada que el backend no haya confirmado. La ventana sin conexión es de **~5 h** en el caso normal, no de meses, así que el drenado del backlog no es un evento ocasional: es parte del sistema de grabación y tiene que ocurrir de forma sostenida durante 15 a 30 días
- **El footprint del módulo SIM queda previsto en la PCB pero sin poblar**, para que una futura variante ambulatoria no exija rediseñar la arquitectura
- **BLE no se apaga, se reubica**: el firmware ya tiene un servicio BLE completo (pairing con passkey, canales LIVE/BACKLOG/CONTROL/STATUS, backlog confirmado por ACK). No se usa como camino de datos crítico

## Contexto técnico

- MCU: Seeed Studio XIAO nRF52840 (Cortex-M4F + BLE, sin WiFi)
- AFE: TI ADS1292R sobre placa de evaluación ADS1x9xECG-FE, **500 Hz**, 24 bits, 2 canales (ECG e impedancia son **estudios separados**, no simultáneos)
- Compresión: codec Rice sin pérdida con predictor de orden 2, ratio **medido 12,80×** sobre ruido ambulatorio real → **468,6 B/s = 40,5 MB/día** en un estudio de ECG
- Radio WiFi: co-procesador ESP32-C3 por UART (LTE-M/SIM7080G queda documentado como opción futura, no implementada)
- El firmware de referencia del equipo de Biomédica vive en el repo hermano `../Holter-ECG-System` (ver su `DATAFLOW.md` y `Filtros.md` para números medidos)
- Usuarios objetivo: pacientes 40-70 años en Argentina
- Regulatorio: ANMAT (clase II), Ley 25.326

## Entregables y documentación formal

- `Plan de Trabajo` (Google Doc) — Plan de trabajo completo para Ing. en Informática (8 hs/semana × 25 semanas, 200 hs/integrante, 400 hs total del equipo)
- `Entregables/1. Tesis Tema Director.pdf` — Aprobación del tema y director
- `Anteproyecto.pdf` — Anteproyecto del equipo de Ing. Biomédica (Oxoby & Buthet)
- `Bibliografia.md` — Bibliografía en formato APA para incluir en el Plan de Trabajo

## Google Drive

Carpeta compartida del proyecto: https://drive.google.com/drive/folders/1E9GPMXy5kB-_u3xjrWAyZCKq1P0wiQgC
- Contiene los mismos archivos del repo + un Google Doc "Plan de Trabajo" en Entregables/
- Se puede acceder via `gws` (Google Workspace CLI) ya configurado con la cuenta tomi.serra@gmail.com
- Proyecto GCP: tesis-workspace

## Estructura del repositorio (monorepo)

Este repo está organizado como monorepo. El repo git vive en la raíz (`tesis/`).

- `front/` — Dashboard médico web (Vite + React + TypeScript + Tailwind)
- `back/` — Backend FastAPI (sirve al dashboard **y** a la app móvil)
- `mobile/` — App del paciente (Expo + React Native + NativeWind). Ver `mobile/AGENTS.md`
- `info del proyecto/` — Documentación técnica del sistema
- `Entregables/` — Documentación formal de la tesis

### Frontend (`front/`)

- **Stack**: Vite, React 19, TypeScript, Tailwind CSS v4 (plugin `@tailwindcss/vite`, sin `tailwind.config.js`), React Router v7, Axios
- **Calidad de código**: ESLint v9 (flat config) + Prettier (sin semicolons, comillas simples, trailing comma `all`, 100 cols)
- **Git hooks**: Husky + lint-staged en `pre-commit`. El directorio `.husky/` vive en la raíz del monorepo; `core.hooksPath` está apuntado a `.husky`. El hook hace `cd front && npx lint-staged`.
- **Scripts** (correr desde `front/`):
  - `npm run dev` — dev server (http://localhost:5173)
  - `npm run build` — type-check + build de producción
  - `npm run lint` — ESLint
  - `npm run format` / `npm run format:check` — Prettier (incluye `e2e/` y `playwright.config.ts`)
  - `npm run test:e2e` — Playwright contra el stack real; ver "Verificar cambios de UI (Playwright)"
- **Variables de entorno**: `VITE_API_URL` (ver `front/.env.example`). Cliente axios base en `front/src/lib/api.ts`.
- **Librería de componentes**: [shadcn/ui](https://ui.shadcn.com/) (style `new-york`), Radix primitives, `class-variance-authority`, `tw-animate-css`.
- **Path alias**: `@/*` → `front/src/*` (configurado en `tsconfig` y `vite.config.ts`).
- **Utility de clases**: `cn()` en `front/src/lib/utils.ts` (clsx + tailwind-merge). Usar siempre que se compongan clases condicionales — no concatenar con `.join(' ')`.

### Convención de componentes UI

**Flujo obligatorio para cualquier componente UI nuevo** (antes de escribir nada propio):

1. **Buscar en el proyecto** — ¿ya existe en `front/src/components/` (componentes de dominio) o `front/src/components/ui/` (primitivos shadcn)? Si sí, reusarlo.
2. **Buscar en ican-web como referencia** — `/Users/tserra/Documents/git/sirius/ican/ican-web/src/components/` y `/src/common/components/`. Si existe un equivalente, usarlo como **referencia visual y de API** (props, variantes, comportamiento, paleta). **Importante**: ican-web usa MUI + styled-components, así que **no se copia el código tal cual** — se reimplementa con primitivos shadcn manteniendo la misma estética y semántica.
3. **Importar de shadcn** — si no existe en (1) ni (2), agregarlo con la CLI: `cd front && npx shadcn@latest add <component>`. Queda en `src/components/ui/`. Si hace falta una variante o un comportamiento de dominio, envolverlo en un wrapper en `src/components/`.
4. **Coherencia visual** — toda variante nueva (sizes, colors, states) debe encajar con los tokens de `src/styles/tokens.css`. Si hace falta un token nuevo, agregarlo ahí, no inline.

**Tokens de shadcn** están mapeados a los tokens propios del proyecto en `src/styles/tokens.css` (sección "shadcn/ui semantic tokens"). `bg-primary`, `text-foreground`, `border-input`, etc. toman automáticamente el navy/gray del proyecto. **No reemplazar los tokens propios** por los genéricos — extender.

### Verificar cambios de UI (Playwright)

`front/e2e` maneja un Chromium real contra el stack real (Vite + FastAPI + Postgres + S3), logueado con el login de verdad (backend → Auth0) como un usuario de prueba dedicado. Es la forma de probar que un cambio de UI anda; lint y build no lo ven.

```bash
cd front
npm run test:e2e                                  # todo
npm run test:e2e -- e2e/app/dashboard.spec.ts    # un archivo
```

- **Un bug de UI, un flujo nuevo o un cambio en lo que muestra una pantalla suma o extiende un spec en `e2e/app`** (las páginas sin sesión van en `e2e/public`). Si se puede, reproducir el bug en el spec primero.
- **Sin efectos reales.** Un spec no crea usuarios en Auth0, no manda push ni escribe en S3. Los llamados que los provocan se stubbean antes de navegar: `await page.route('**/api/cosa', (route) => route.fulfill({ status: 201, json: {...} }))` (modelo: el login con credenciales inválidas en `e2e/public/login.spec.ts`).
- **Nunca cerrar sesión en un spec de `e2e/app`.** El logout sube `session_version` del usuario y invalida la sesión guardada que comparten todos los specs.
- **Capturas para el PR:** `prScreenshot(locator, '<qué muestra>')` (`e2e/support/pr-screenshot.ts`) en el momento en que el cambio se ve, con el locator del elemento que cambió (un diálogo, un panel, una tabla), nunca la página entera. **El repo es público y las imágenes también**: solo puede aparecer la data sintética del seed de demo. Tras un **Playwright e2e** verde, CI pone las capturas de los specs que el PR agregó o cambió en una sección "Screenshots" de la descripción (`.github/workflows/playwright-report.yml`). Nunca commitear imágenes para mostrar un cambio.
- **Roturas silenciosas:** `watchForFailures(page)` (`e2e/support/failures.ts`) junta las excepciones no capturadas y los 5xx de la API; afirmar que está vacío al final del spec (ver `e2e/app/navigation.spec.ts`).
- **Si falla**, leer `front/test-results/<test>/error-context.md` (snapshot de la página en el momento del error) antes de adivinar. Con `--trace on` y `npx playwright show-trace <trace.zip>` hay una línea de tiempo completa.
- **Qué necesita en local:**
  1. Postgres y MinIO arriba (`docker compose up -d db minio`, o el skill `run-holter`) y migrados. Playwright levanta solo el dev server y la API (`uv run uvicorn`); si ya hay algo escuchando en 5173/8000 lo **reusa**, y eso prueba el código de *ese* stack, no el de tu checkout. En un worktree, o con el stack del checkout principal corriendo, usar `E2E_WEB_PORT` / `E2E_API_PORT` (p. ej. `E2E_WEB_PORT=5174 E2E_API_PORT=8001 npm run test:e2e`).
     **En un worktree falta `back/.env`** (está ignorado por git, vive solo en el checkout principal) y todo comando del backend, el seed y la API de Playwright incluidos, falla con `11 validation errors for Settings`. Enlazarlo una vez: `ln -s <checkout principal>/back/.env back/.env`.
  2. El usuario de prueba, creado por una sola vez: la cuenta en Auth0 (tenant de desarrollo, conexión Username-Password-Authentication; email + contraseña) y su fila en la base. Con `front/.env.e2e.local` ya completo (punto 3):

     ```bash
     cd back && set -a && source ../front/.env.e2e.local && set +a
     uv run python -m app.scripts.seed_e2e_user --email "$E2E_EMAIL" --auth0-id "$E2E_AUTH0_SUB"
     uv run python -m app.scripts.seed_demo --doctor-email "$E2E_EMAIL"   # pacientes y estudios de demo
     ```

  3. `front/.env.e2e.local` (ignorado por git; plantilla y de dónde sale cada valor en `front/.env.e2e.example`) con `E2E_EMAIL`, `E2E_PASSWORD` y `E2E_AUTH0_SUB` (el `user_id` del usuario en Auth0). Los `AUTH0_*` **no** van ahí: la API que levanta Playwright los lee de `back/.env`, y una variable vacía lo pisa. Nunca crear, imprimir ni commitear credenciales.
- **El login tiene rate limit** (5 intentos cada 15 min por cuenta e IP). El setup reusa `e2e/.auth/user.json` mientras la sesión (1 h) siga válida para ese usuario, así que repetir `npm run test:e2e` no vuelve a loguear.
- **CI corre en cada PR a `main`** que toque `front/` o `back/` (check **Playwright e2e**, `.github/workflows/playwright-ci.yml`): Postgres nuevo y el servidor S3 de `moto` (MinIO ya no publica imágenes: el `docker pull` de `minio/minio` da *denied*), `seed_e2e_user` + `seed_demo`, y el mismo usuario de prueba desde los secretos del repo: `E2E_EMAIL`, `E2E_PASSWORD`, `E2E_AUTH0_SUB` (el `user_id` de Auth0) y `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_AUDIENCE` del backend (los de `back/.env` del tenant de **desarrollo**, nunca los de producción). Se suben una vez: `gh secret set -f front/.env.e2e.local -R tesis-ecg/tesis-ecg-mono` para los tres `E2E_*`, y para cada `AUTH0_*` `gh secret set <NOMBRE> -R tesis-ecg/tesis-ecg-mono --body "$(grep '^<NOMBRE>=' back/.env | cut -d= -f2-)"`. Los PRs desde forks se saltean (no reciben secretos). Una falla se publica como un solo comentario del PR (specs rotos y sus errores), editado en el lugar y pasado a "passed" por una corrida verde posterior. El job de test corre con token de solo lectura; el comentario y las capturas los escribe `playwright-report.yml`, que corre el código de `main`.
- **Browser integrado vs Playwright:** el panel del navegador sirve para mirar algo una vez (layout, capturas); Playwright, para todo lo que se quiera re-correr con un pass/fail.

## Convenciones

- Documentación en español
- Código y comentarios técnicos pueden ser en inglés

## Imported Claude Cowork project instructions

Este proyecto es para la tesis de mi carrera Ingenieria Informatica
