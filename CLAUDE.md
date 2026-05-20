# Holter Wearable ECG — Proyecto de Tesis

## Qué es este proyecto

Proyecto interdisciplinario de Trabajo Final de Grado (TFG) en la Universidad Austral. Dos equipos trabajan en conjunto:
- **Ingeniería en Informática**: Tomás Serra y Martín Barreiro (director: Federico Ruiz) — firmware, app móvil, cloud, dashboard, gestión de datos
- **Ingeniería Biomédica**: Gonzalo Oxoby y Juan Bautista Buthet (director: Dr. Federico Bustos) — hardware, PCB, AFE, electrodos, prenda textil

Se está desarrollando un dispositivo wearable tipo Holter ECG integrado en un chaleco/top textil con electrodos secos, orientado a monitoreo cardíaco preventivo continuo. Este repositorio se enfoca en la parte de Informática.

## Arquitectura del sistema

- **Hardware**: XIAO Nordic + AFE (front-end analógico) para adquisición de ECG de 3 canales
- **Comunicación principal**: módulo celular SIM (LTE-M, SIM7080G como candidato técnico) — SD como buffer continuo + envío batch cada 1h directo al backend, standalone, sin depender de app ni WiFi
- **Almacenamiento local**: microSD 128 MB (buffer primario; en operación normal retiene solo ~2.7 MB — el batch de la hora en curso; acumula hasta ~2 días sin señal celular)
- **Batería**: Li-Po 500-800 mAh, autonomía estimada ~2-3 días (SIM agrega <5% de overhead)
- **App móvil**: no aplica — el dispositivo opera 100% standalone vía SIM
- **Cloud**: FastAPI + PostgreSQL + S3
- **Dashboard médico**: React/Next.js

## Documentación

La arquitectura de comunicación está documentada en `info del proyecto/`:

| Archivo | Contenido |
|---|---|
| `info del proyecto/README.md` | Índice, diagrama general, stack tecnológico |
| `info del proyecto/01-justificacion.md` | SIM vs BLE+App: comparación y decisión de arquitectura |
| `info del proyecto/02-firmware-holter.md` | Flujo SIM principal, SD, máquina de estados |
| `info del proyecto/03-app-movil.md` | No aplica — sin app móvil en la arquitectura actual |
| `info del proyecto/04-cloud.md` | FastAPI, modelo de datos, trigger del médico |
| `info del proyecto/05-bateria-y-datos.md` | Consumo, batería, volúmenes, tiempos de transferencia |
| `info del proyecto/06-escenarios-y-seguridad.md` | Escenarios críticos, seguridad, regulatorio |
| `info del proyecto/07-sim-celular.md` | Canal SIM (LTE-M): arquitectura principal, SD buffer, envío batch periódico |

## Decisiones de diseño tomadas

- **Módulo celular SIM (LTE-M) como único canal de comunicación**: arquitectura standalone, sin BLE, sin app, sin WiFi del paciente. El módulo concreto es candidato (SIM7080G como referencia técnica más probable)
- La SD card (128 MB) es el buffer primario: ECG graba continuamente a SD; cada hora el SIM envía el batch y limpia lo enviado
- La SD es el seguro final: si la SIM no tiene señal, los datos se acumulan hasta ~2 días. Al recuperar señal, se envían los pendientes
- PSM (Power Saving Mode) del módulo SIM: el módulo duerme ~59 min/hora a 3 µA — impacto en batería menor al 5%

## Contexto técnico

- MCU: XIAO Nordic (Seeed Studio XIAO nRF52840 / variante Nordic)
- ECG: 3 canales — parámetros de muestreo (sample rate, resolución) a definir con el equipo de Biomédica en Fase 1
- Módulo celular: SIM via UART con el XIAO Nordic (LTE-M/NB-IoT, SIM7080G como candidato)
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
- `back/` — Backend FastAPI (a crear más adelante)
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
  - `npm run format` / `npm run format:check` — Prettier
- **Variables de entorno**: `VITE_API_URL` (ver `front/.env.example`). Cliente axios base en `front/src/lib/api.ts`.

## Convenciones

- Documentación en español
- Código y comentarios técnicos pueden ser en inglés
