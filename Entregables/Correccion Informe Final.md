# Corrección del Informe Final según la Rúbrica de Productos

Documento corregido: `Informe Final.md` / Google Doc "Informe Final" (versión previa a la corrección, 87 páginas).
Criterio: Rúbrica para la corrección del documento final de los Productos.
Los apartados que el equipo dejó pendientes a propósito ([COMPLETAR], [CAPTURA], secciones de IA 2.5, 4.6, 5.6 y 10.6, modelo de negocio, branding, user research y lista de materiales) no se califican: se marcan como **Pendiente**.

## Resumen

| Dimensión | Peso | Dictamen previo | Dictamen tras la corrección |
|---|---|---|---|
| Cumplimiento de objetivos | 20 % | Parcial | Cumple (salvo el cronograma real, pendiente) |
| Formalidades y compromisos | 10 % | Parcial | Cumple |
| Calidad técnica | 30 % | Cumple | Cumple |
| Desarrollo funcional | 20 % | Cumple (UX y pruebas con usuarios pendientes) | Cumple (ídem) |
| Investigación y dominio técnico | 10 % | Cumple parcialmente | Cumple (user research y viabilidad técnica pendientes) |
| Desarrollo de negocio | 10 % | Pendiente | Pendiente |

## 1. Cumplimiento de objetivos (20 %)

| Criterio | Dictamen | Fundamento | Acción tomada |
|---|---|---|---|
| Objetivos | Parcial → Cumple | El objetivo general y los específicos están diferenciados y casi todos tienen una métrica de verificación. El OE5 (aplicación móvil) no era medible. | Se agregó al OE5 un criterio verificable: un reporte enviado desde la aplicación aparece sobre el trazado en la hora correcta. El OE7 (IA) queda pendiente. |
| Dominio | Cumple | Enmarcado en IoT, sistemas distribuidos, ingeniería de datos y seguridad de la información. | — |
| Alcances | Cumple | Incluye y excluye de forma explícita, con la división de responsabilidades con Biomédica. | Se eliminó el párrafo final de 1.4, que repetía la frontera firmware/plataforma de 3.1. |
| Plan de trabajo | Parcial | La Tabla 4 registra fases y fechas. La fase 2 "Desarrollo del firmware" contradecía el alcance (el firmware es de Biomédica). El cronograma real y la comparación con el planificado están pendientes. | La fase 2 se renombró a "Definición del contrato de comunicación con el firmware". **Recomendación:** insertar una captura del diagrama de Gantt (archivo `Online Gantt Chart 2026.gantt`) con el nombre de la herramienta, además de la tabla. |
| Coherencia de entregas parciales | Cumple | La Tabla 3 explica cada desvío respecto del plan de trabajo con su motivo. | — |

## 2. Formalidades y compromisos (10 %)

| Criterio | Dictamen | Fundamento | Acción tomada |
|---|---|---|---|
| Estructura | Cumple | Respeta las secciones del instructivo (Resumen, Introducción, Marco teórico, La aplicación, Tecnologías, Arquitectura, Infraestructura, Modelo de datos, Usuarios y funcionalidades, Interfaz, Ejemplos ilustrativos, Dificultades, Conclusiones, Bibliografía). | — |
| Formato | Se verifica en el Doc | El `.md` no refleja el formato. | En el Doc solo se editó el texto, sin tocar estilos. |
| Índice | Cumple | Máximo 3 niveles (x.y.z). | Se regenera el índice del Doc después de los cambios (hay subsecciones nuevas 1.5.1–1.5.7). |
| Redacción impersonal | Cumple con observaciones | No hay primera persona. Había giros narrativos o coloquiales: "El resultado más importante es la última fila", "el contrato de confirmación funcionó exactamente para lo que fue diseñado", "La lección que se desprende es…", y una 11.8 anecdótica. | Se reescribieron en tono impersonal y descriptivo. |
| Errores idiomáticos | Parcial → Cumple | "25,000 mm/s" y "10,000 mm/mV" (separador decimal incorrecto), "go back N", "cliente servidor", "compás R R", "Cortex M4F", "Cumplido parcialmente", "24 hs". El problema principal era la **redundancia**: el mismo contenido aparecía 2 o 3 veces (hardware en 3.1/4.1/3.4.3; historia de las pruebas en 5.2/10.3/11.x; seguridad en 1.5/4.5/5.5; disclaimer clínico en 1.5/1.5.1/9.4). | Se corrigieron los términos (Go-Back-N, cliente-servidor, R-R, Cortex-M4F, 25,0/10,0, Parcialmente cumplido) y se eliminaron las repeticiones: cada tema queda desarrollado en un solo lugar y los demás remiten a él. |
| Citas y referencias | Parcial → Cumple | APA consistente. Faltaban las citas del método MoSCoW y del análisis FODA. La referencia de Pagola et al. usaba una URL de PubMed en lugar del DOI. | Se agregaron Clegg y Barker (1994) y Gürel y Tat (2017). Pagola et al. pasó a citarse con su DOI (10.1016/j.ijcard.2017.10.063, verificado en Crossref). |
| Calidad de la bibliografía | Cumple | Predominan artículos con revisión por pares (Circulation, NEJM, IEEE, JMIR), RFC y normativa oficial. Las páginas de fabricantes "s.f." se usan solo para especificaciones de componentes, un uso aceptable. | — |
| Plagios | Cumple | Redacción propia y fuentes citadas. | — |

## 3. Calidad técnica (30 %)

| Criterio | Dictamen | Fundamento | Acción tomada |
|---|---|---|---|
| Modelo de datos y arquitectura | Cumple | DER (Figura 5), arquitectura de despliegue (Figura 2), componentes (Figura 1) y ADR (Tabla 7). | — |
| Justificación tecnológica | Cumple con observación | Buena justificación de Python, FastAPI, PostgreSQL, uPlot, Vercel, Auth0 y el canal WiFi (Tabla 6, con consumos y costos). React se justificaba solo por "experiencia previa del equipo". | Se agregó el argumento técnico: ecosistema de componentes accesibles y reutilización con React Native. Se quitaron de la prosa las versiones que ya figuran en la Tabla 5. |
| Tendencias en informática | Cumple | IoT aplicado de punta a punta (dispositivo → nube) y computación serverless. La IA está en desarrollo (pendiente). | Se explicitó en 3.1 que el sistema aplica IoT y cloud serverless. |
| Producto entregado | Cumple | Sistema desplegado e integrado con el hardware real. Los objetivos pendientes se declaran con honestidad en la Tabla 17. | — |

## 4. Desarrollo funcional (20 %)

| Criterio | Dictamen | Fundamento | Acción tomada |
|---|---|---|---|
| Funcionalidades | Cumple | RF (Tabla 11) con origen y prioridad MoSCoW, RNF (Tabla 12) con su verificación y HU (Tabla 13) con criterios de aceptación. | Se agregó la cita de MoSCoW. |
| Diagramas funcionales | Cumple | Flujo del estudio, estados del estudio, secuencia de alerta, secuencia de ingesta y procesamiento. | — |
| Usabilidad (UX) | Pendiente | Depende de las capturas (Figuras 9–18). | — |
| Metodologías ágiles | Cumple | Kanban justificado, con Linear, ramas, pull requests, CI como condición de integración y ADR. | **Recomendación:** sumar una captura del tablero de Linear. |
| Pruebas funcionales | Cumple | Unitarias, integración, contrato, carga y pruebas con el hardware real, además de la revisión clínica. | — |
| Resultados de las pruebas | Cumple | La sección 10.3 presenta mediciones cuantitativas (Tablas 15 y 16) y decisiones concretas derivadas. Las pruebas de usabilidad con pacientes están pendientes. | La lista de decisiones de 10.3 remite al cap. 11 en lugar de repetir su contenido. |

## 5. Investigación y dominio técnico (10 %)

| Criterio | Dictamen | Fundamento | Acción tomada |
|---|---|---|---|
| Marco teórico | Cumple con observación | Nivela al lector en la temática (ECG, Holter, arritmias) y en lo técnico (IoT, compresión, ARQ, NTP, REST, serverless, OAuth/JWT/RBAC), sin tomar decisiones. El párrafo de conectividad de 2.2 mostraba sesgo hacia la alternativa elegida. | El párrafo se dejó neutral y la comparación quedó solo en 4.1 (Tabla 6). La sección 2.5 (IA) está pendiente. |
| Estado del arte | Cumple (con celdas por verificar) | Tabla comparativa (Tabla 1) + FODA (Tabla 2). | Las celdas [VERIFICAR] de Nuubo quedan a cargo del equipo. Se agregó la cita del método FODA. |
| Conclusión del estado del arte | Cumple | Explicita el diferencial y la relevancia social. | — |
| User research | Pendiente | Hoy solo está la revisión de dos cardiólogos. La rúbrica pide 2 o más herramientas (p. ej., entrevistas transcriptas con la selección justificada y una encuesta a público relevante). | — |
| Viabilidad legal | Cumple | ANMAT clase II, Ley 25.326, Ley 26.529 y disclaimer clínico en la interfaz y el PDF. | Se integró como 1.5.7 y se eliminó el disclaimer repetido, que queda en 1.5.3. |
| Viabilidad técnica | Pendiente | Falta la lista de materiales y el análisis de fabricación. | — |
| Lecciones aprendidas | Cumple | La Tabla 17 vincula cada objetivo con su evidencia, y las lecciones se derivan de los resultados. | Las lecciones se condensaron sin repetir la narración del cap. 11. |

## 6. Desarrollo de negocio (10 %)

Modelo de negocio, viabilidad económico-financiera, branding y logotipo: **Pendiente** (a completar por el equipo). Es el 10 % de la nota y hoy no tiene contenido. Se recomienda priorizarlo.

## 7. Valor ético y social

La sección 1.5 no coincidía con el *Informe de Valor Ético y Social* entregado: era un resumen reescrito que omitía los derechos humanos, el bien común, la sustentabilidad y el discernimiento ético. Se reemplazó por el texto de ese informe (subsecciones 1.5.1 a 1.5.6), cambiando solo las afirmaciones técnicas que ya no son ciertas por los cambios de hardware:

- LTE-M → WiFi del domicilio.
- microSD con dos días de buffer → flash de 16 MB con unas cinco horas y confirmación.
- 500–800 mAh y 2–3 días → 1800 mAh y unos diez días.
- "sin app móvil" → la aplicación es un complemento.
- "sin alertas al paciente" → notificaciones informativas.
- Referencia a `Bibliografia.md` → sección Bibliografía.

El archivo del informe ético no se modificó.

## 8. Extensión

La prosa se redujo de ~17.000 a ~15.000 palabras (−11 % total). Sin contar la 1.5, que creció al incorporar el informe ético, la reducción es del −18 %. No se eliminó ninguna tabla, figura ni requisito evaluado por la rúbrica. Los recortes corresponden a repeticiones entre capítulos y a descripciones que las tablas o figuras ya muestran.
