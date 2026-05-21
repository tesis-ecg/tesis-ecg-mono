# Plan de desarrollo — Dashboard Médico (Frontend)

Documento de referencia para el desarrollo del dashboard médico del proyecto Holter ECG (TFG Austral). Define las fases de trabajo, su alcance, los requerimientos asociados y los criterios de salida de cada fase.

Los requerimientos completos del sistema están en `../Requerimientos.md`. La arquitectura general en `../info del proyecto/README.md`.

## Resumen del producto

Dashboard web pensado para que **cardiólogos y electrofisiólogos** revisen días de datos de ECG e impedancia torácica en pocos minutos. También sirve a **administradores** (gestión de dispositivos y créditos) y a **investigadores** (exportación de datos para trials).

Stack: Vite + React 19 + TypeScript + Tailwind v4 + React Router v7 + Axios + TanStack Query.

Backend: FastAPI + PostgreSQL + S3 (en desarrollo paralelo).

## Estructura por fases

El desarrollo se organiza en cinco fases. Cada fase tiene un objetivo verificable y deja la app utilizable hasta ese punto.

```
Fase 0  → Cimientos técnicos
Fase 1  → Núcleo del dashboard médico
Fase 2  → Gestión de dispositivos y configuración de estudios
Fase 3  → Análisis clínico avanzado
Fase 4  → Investigación y trial
```

---

## Fase 0 — Cimientos técnicos

**Objetivo**: dejar el esqueleto listo antes de tocar features de dominio. Toda decisión arquitectónica importante se toma acá.

### Alcance

- Design system: tokens (colores semánticos, tipografía, espaciado, sombras, radios) con Tailwind v4 CSS-first config.
- App shell: layout reutilizable con sidebar, topbar y outlet.
- Estructura de rutas: públicas, protegidas y por rol.
- Autenticación: página de login custom + integración con Auth0 mediada por el backend (el front nunca llama Auth0 directo).
- Cliente HTTP: axios con interceptores, manejo unificado de errores, retries.
- Data fetching: TanStack Query con patrón de un hook por recurso.
- Biblioteca UI base: `Button`, `Input`, `Card`, `Table`, `Badge`, `Modal`, `Toast`, `Tabs`, `Skeleton`, `EmptyState`.
- Testing y mocking: Vitest + Testing Library + MSW para desacoplar el desarrollo del backend.

### Tickets

TES-6 a TES-15 (los 10 tickets iniciales).

### Criterios de salida

- Login funcional contra mock o backend con Auth0.
- Listado y perfil del paciente renderizan con datos mock.
- `npm run build`, `npm run lint` y `npm run test` pasan en CI.
- Existe la página `/__dev/components` que muestra todos los componentes UI.

---

## Fase 1 — Núcleo del dashboard médico

**Objetivo**: la consola clínica básica. Que el médico pueda entrar, ver sus pacientes, abrir uno y ver su ECG. Refleja el **Módulo 4** de los requerimientos y partes de los Módulos 1 y 6.

### Alcance

- Página de inicio del médico (KPIs: cantidad de pacientes activos, alertas pendientes, estudios en curso).
- Listado de pacientes con búsqueda, filtros y paginación (TES-13, ya cubierto en Fase 0 a nivel base — se profundiza acá).
- Perfil del paciente con tabs (resumen, estudios, dispositivo) (TES-14, idem).
- **Visualizador de ECG de alta fidelidad**: línea de tiempo navegable, zoom in/out sin perder contexto. Librería WebGL (`uPlot` recomendado por `analisis-wecan.md`).
- Detalle del estudio (`/studies/:id`): metadata + visualizador ECG embedido + panel lateral de eventos detectados por la IA.
- Panel de hallazgos detectados automáticamente (visualización solo lectura): listado priorizado de arritmias y eventos.
- Filtros temporales en el visualizador: jump a un evento detectado por la IA, marcar/desmarcar regiones, anotar.

### Requerimientos cubiertos

- Módulo 4: Visualizador de Señales de Alta Fidelidad.
- Módulo 4: Correlación Multimodal Sincronizada (versión básica, sólo ECG; impedancia llega en Fase 3).
- Módulo 1: Tablero de Salud del Hardware (visible en el tab "Dispositivo" del perfil).
- Módulo 6: Triage Automático de Arritmias (sólo visualización del listado).

### Criterios de salida

- Un médico puede loguearse, navegar a un paciente, abrir un estudio y revisar el ECG con fluidez.
- El visualizador soporta >1 hora de ECG sin freeze (benchmark target: 250 Hz × 1 h = 900k puntos).
- Los hallazgos detectados se muestran y son clickeables (jump al timestamp).

---

## Fase 2 — Gestión de dispositivos y configuración de estudios

**Objetivo**: dar herramientas al **administrador** y al **médico que prescribe el estudio**. Refleja los **Módulos 1 y 3** completos.

### Alcance

- Listado y detalle de dispositivos (chalecos) con su estado actual:
  - Identidad única (serial number, MAC del módulo SIM).
  - Estado de batería, señal celular, último ping, próximo envío programado.
  - Historial de uso.
- Gestión de "Créditos de Monitoreo": cargar/descontar tiempo o estudios a una cuenta médica.
- Sistema de alertas del watchdog: dispositivo que dejó de transmitir → notificación visual en el dashboard.
- Asignación dispositivo ↔ paciente.
- Prescripción digital del estudio: configurar qué módulos se activan (ECG, Impedancia, ambos), ventanas operativas (horarios de medición), umbrales de alerta por paciente.
- Vista de "salud del trial" para el admin: cuántos dispositivos activos, cuántos con problemas, cuánta gente cerca de quedarse sin crédito.

### Requerimientos cubiertos

- Módulo 1 completo (Identidad, Créditos, Tablero de Salud, Watchdog).
- Módulo 3 completo (Prescripción Digital, Ventanas Operativas, Umbrales de Alerta).

### Criterios de salida

- Admin puede registrar un nuevo dispositivo, asignarlo a un paciente y ver su estado.
- Médico puede prescribir un estudio configurando módulos, ventanas y umbrales.
- Se dispara una alerta visual cuando un dispositivo no transmite por más de N horas.

---

## Fase 3 — Análisis clínico avanzado

**Objetivo**: hacer el dashboard "clínicamente útil" — no sólo mostrar señal, sino acelerar el diagnóstico. Refleja los **Módulos 4 (avanzado) y 6** completos.

### Alcance

- Vista de correlación multimodal sincronizada: ECG y tendencia de impedancia en el mismo timeline, con marcadores cruzados (ej. arritmia ↔ pico de congestión).
- Gestión y validación de hallazgos:
  - El médico puede validar, corregir o descartar cada anomalía detectada por la IA.
  - Anotaciones libres por segmento (markdown).
  - Audit de quién validó qué.
- Vigilancia de congestión: gráfico de tendencia de impedancia nocturna, detección de baseline.
- Configuración avanzada de alertas: umbrales clínicos por paciente que dispararán notificaciones (email/push) cuando se crucen.
- Generador de reportes clínicos automatizados: PDF con hallazgos consolidados, tendencias, adherencia. Listo para historia clínica del Hospital Austral.

### Requerimientos cubiertos

- Módulo 4: Correlación Multimodal Sincronizada (completo), Gestión y Validación de Hallazgos, Generador de Reportes.
- Módulo 6 completo (Triage, Vigilancia de Congestión, Alertas Basadas en Riesgo, Soporte a la Decisión).

### Criterios de salida

- Un médico puede revisar un estudio, validar todos los hallazgos y generar un PDF en menos de 10 minutos.
- El PDF contiene: resumen ejecutivo, lista de hallazgos validados, gráficos clave, datos del paciente.

---

## Fase 4 — Investigación y trial (Clinical Research Hub)

**Objetivo**: cubrir las necesidades del **equipo de investigación** que lidera el ensayo clínico. Refleja el **Módulo 7** completo.

### Alcance

- Exportador masivo: descargar ECG e impedancia de múltiples pacientes en CSV o EDF+ para análisis externos / modelos de IA de terceros.
- Gestión de consentimiento informado digital: registro con firma del paciente, vinculado a la Ley 25.326.
- Audit trail: registro de quién accedió a qué datos y cuándo (requerido por ANMAT clase II).
- Panel de métricas de población:
  - Estudios completados, fallados, en curso.
  - Adherencia promedio por paciente.
  - Tasa de ruido por dispositivo (señaliza problemas en la prenda textil).
  - Análisis demográfico agregado.

### Requerimientos cubiertos

- Módulo 7 completo.

### Criterios de salida

- Un investigador puede descargar el dataset completo del trial en CSV/EDF+ para `n` pacientes en un click.
- El audit trail es navegable y filtrable.
- El panel de métricas de población refleja el estado real del trial en tiempo real.

---

## Fuera de alcance del frontend

Estas piezas viven en otros componentes del sistema:

- **Módulo 2 — Ingesta de Datos**: pipeline server-side (FastAPI + workers) para procesar el formato de 72 bits del ADSR. Es trabajo de backend.
- **Módulo 5 — Acompañamiento del Paciente (app móvil)**: explícitamente fuera de la arquitectura actual (`CLAUDE.md` línea "no aplica — sin app móvil"). El dispositivo es standalone vía SIM.

Si en algún momento la app móvil entra al alcance, se trata como un proyecto separado (no como una expansión del dashboard).

---

## Convenciones de trabajo

- **Idioma**: documentación en español; código y nombres técnicos en inglés.
- **Stack**: ver `package.json` y `../CLAUDE.md`.
- **Tickets en Linear**: equipo `TES`, todos con label `Frontend`. Labels exclusivos en grupo "Domain", así que `Frontend` no se combina con `Diseño`/`Infra`/`Backend`.
- **Branch naming**: Linear genera automáticamente `feature/tes-<n>` por ticket.
- **CI/Lint**: `npm run lint && npm run build && npm run test` deben pasar en todo PR a `main`.
