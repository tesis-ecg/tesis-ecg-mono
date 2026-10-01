# Requerimientos — Sistema de Telemetría Cardíaca

> **Documento consolidado.** Sobre la estructura original por módulos se integró el detalle técnico
> y las prioridades del documento **"Requerimientos y Prioridades"** (PDF, 6 secciones).
> Convención de prioridad **MoSCoW**: **Must** (imprescindible para el trial) · **Should** (importante) · **Could** (deseable).
> Las secciones marcadas con *(PDF §N)* provienen textualmente de ese documento.

## Mapa de prioridades

| PDF | Sección del PDF | Prioridad | Dónde vive en este documento |
| --- | --- | --- | --- |
| §1 | Arquitectura y Adquisición de Datos | **Must** | [Módulo 2 → 2.A](#2a-arquitectura-y-adquisición-de-datos--must-pdf-1) |
| §2 | API de Pacientes (App Móvil) | **Must** | [Módulo 5 → 5.A](#5a-api-de-pacientes-app-móvil--must-pdf-2) |
| §3 | Motor de Clasificación de Señal (Jerarquía de Latidos) | **Must** | [Módulo 6 → 6.A](#6a-motor-de-clasificación-de-señal-jerarquía-de-latidos--must-pdf-3) |
| §4 | Diccionario de Métricas Clínicas Obligatorias | **Should** | [Módulo 6 → 6.B](#6b-diccionario-de-métricas-clínicas-obligatorias--should-pdf-4) |
| §5 | Trazabilidad, Evidencia y Recálculo Dinámico (CQRS / Feedback Loop) | **Should** | [Módulo 4 → 4.A](#4a-trazabilidad-evidencia-y-recálculo-dinámico-cqrs--feedback-loop--should-pdf-5) |
| §6 | Herramientas de Interacción y Medición Manual en Trazado | **Could** | [Módulo 4 → 4.B](#4b-herramientas-de-interacción-y-medición-manual-en-trazado--could-pdf-6) |
| — | Módulos 1, 3 y 7 | *Sin prioridad asignada en el PDF* | Se mantienen tal cual |

---

## Módulo 1: Gestión de Dispositivos y Modelo de Suscripciones

*Sin prioridad asignada en el PDF de prioridades.*

Este módulo es el pilar comercial. Su objetivo es que el administrador pueda controlar quién usa la tecnología y asegurarse de que el hardware esté operativo durante el ensayo clínico.

* **Identidad Única del Equipo:** Cada chaleco debe ser reconocido automáticamente por el sistema al encenderse, vinculando el hardware físico con un registro digital para evitar errores en la asignación de pacientes.

* **Gestión de "Créditos de Monitoreo":** El sistema debe permitir al administrador cargar "tiempo de uso" o "cantidad de estudios" a una cuenta médica. Una vez agotado el crédito, el sistema notificará al profesional y restringirá el inicio de nuevos estudios hasta la renovación de la suscripción.

* **Tablero de Salud del Hardware:** El personal administrativo debe poder ver de un vistazo si los equipos están cargados, si tienen buena señal de WiFi en el domicilio del paciente y si están transmitiendo datos correctamente.

  * En caso de que el adsr mande que está midiendo mal, se le debe notificar al paciente vía la app mobile (los primeros
  * El protocolo de aviso concreto para "mala calidad de señal durante un intervalo continuo dT" está especificado en [5.A](#5a-api-de-pacientes-app-móvil--must-pdf-2).

* **Guardián del Ensayo (Watchdog):** Si un paciente deja de usar el dispositivo o este se apaga durante el periodo del trial, el sistema debe generar una alerta automática para que el equipo de soporte intervenga y no se pierdan días de estudio.

---

## Módulo 2: Ingesta de Datos y Aseguramiento de la Calidad Clínica

Este módulo es el "cerebro invisible". Su función es recibir los datos crudos y transformarlos en algo que un cardiólogo pueda firmar como un diagnóstico válido.

* **Recepción Multimodal Inteligente:** El sistema debe procesar en paralelo la actividad eléctrica (ECG) y la acumulación de líquidos (Impedancia), organizando la información para que el médico pueda ver ambas facetas del corazón del mismo paciente de forma sincronizada.

  * El ADSR exporta esto en un formato de 72 bits cada 2 milisegundos. Esto, se parte en 3 secciones:

    * La primera: (24 bits) Estado de los electrodos

    * La segunda: (24 bits) ECG

    * La tercera: (24 bits) impedancia

  * El sistema debe ser capaz de recopilar esta estructura y, desde el microcontrolador o el servidor (a definir), procesarla.

* **Filtro de Confianza Clínica:** Antes de mostrar la señal al médico, el sistema debe "limpiar" automáticamente el ruido provocado por el movimiento del paciente, la fricción de la prenda o la interferencia eléctrica del hogar.

* **Semáforo de Calidad de Señal:** Si el paciente se colocó mal el chaleco o un electrodo seco perdió contacto, el sistema debe identificar ese segmento de tiempo como "No Diagnóstico" y notificarlo, evitando que el médico pierda tiempo analizando ruido.

  * Formalizado como **GRUPO A — Ruido (Caso 0)** en la jerarquía de latidos ([6.A](#6a-motor-de-clasificación-de-señal-jerarquía-de-latidos--must-pdf-3)).

* **Reconstrucción Fiel de la Experiencia:** El sistema debe garantizar que los datos recolectados en la memoria del equipo lleguen íntegros a la nube, incluso si el paciente pasa horas o días fuera del alcance del WiFi de su domicilio.

* **Exportación para Integración Hospitalaria:** Los resultados deben poder descargarse en formatos que el Hospital Austral ya utilice en sus sistemas de cardiología, facilitando que el estudio se incorpore a la historia clínica del paciente.

### 2.A Arquitectura y Adquisición de Datos — **Must** *(PDF §1)*

* **Ingesta de Datos:** El ecosistema de microservicios debe exponer una **API REST** para recibir el flujo de telemetría del wearable vía WiFi.

* **Persistencia Inmutable:** El registro crudo de la señal debe almacenarse íntegramente. El ruido (**Caso 0**) es la capa base intocable: **no debe eliminarse de la base de datos bajo ninguna circunstancia**, para garantizar la integridad de auditorías y recálculos.

---

## Módulo 3: Configuración de Protocolos y Orquestación de Estudios

*Sin prioridad asignada en el PDF de prioridades.*

Este módulo es el "centro de mando" donde el médico prescribe cómo debe comportarse el hardware según la patología del paciente.

* **Prescripción Digital del Estudio:** Antes de entregar el chaleco, el médico debe poder configurar desde el sistema qué "módulos" de medición se activan: solo ECG (Holter), solo Impedancia (Seguimiento de Insuficiencia Cardíaca) o un modo combinado.

* **Programación de Ventanas Operativas:** El profesional puede programar horarios específicos de medición (ej. "Monitoreo de Impedancia de 22:00 a 06:00") para capturar datos en reposo y extender la autonomía de la batería del dispositivo.

* **Configuración de Umbrales de Alerta:** Espacio para que el médico defina qué constituye un "evento crítico" para un paciente en particular (ej. una frecuencia cardíaca menor a 40 lpm), asegurando que el sistema solo notifique lo que es clínicamente relevante para ese caso.

  * Deberíamos poder además identificar los eventos más fáciles, tanto de arritmias como de cambios bruscos de impedancia.
  * Esos "eventos más fáciles" ya están tipificados como **Casos 1 a 7** del GRUPO C en [6.A](#6a-motor-de-clasificación-de-señal-jerarquía-de-latidos--must-pdf-3); los umbrales configurables deben poder referirse a ellos y a las métricas de [6.B](#6b-diccionario-de-métricas-clínicas-obligatorias--should-pdf-4).

---

## Módulo 4: Consola Clínica de Análisis (Dashboard Médico)

Es la interfaz principal del cardiólogo y electrofisiólogo. Su objetivo es la eficiencia: permitir analizar días de datos en pocos minutos.

* **Visualizador de Señales de Alta Fidelidad:** Una línea de tiempo interactiva que permite al médico "navegar" por los registros de ECG e Impedancia con fluidez, haciendo zoom en eventos específicos sin perder el contexto del estudio completo.

  * Hacer benchmarks de sistemas de análisis de holter. Hacen resúmenes de eventos, diferencian ruido (dispositivo mal colocado), etc.

* **Correlación Multimodal Sincronizada:** El dashboard debe presentar en una misma vista la actividad eléctrica y la tendencia de líquidos (impedancia). Esto permite al médico ver, por ejemplo, si una arritmia detectada coincide con un aumento en la congestión pulmonar del paciente.

  * Buscar sistemas de análisis de impedancia torácica. Funciona igual que ver un ECG porque el valor va variando a lo largo del ciclo cardiaco.

* **Gestión y Validación de Hallazgos:** Herramienta para que el médico valide, corrija o descarte las anomalías detectadas automáticamente por la inteligencia artificial, manteniendo siempre el control final sobre el diagnóstico.

  * El flujo técnico completo (evidencia → reclasificación → recálculo) está detallado en [4.A](#4a-trazabilidad-evidencia-y-recálculo-dinámico-cqrs--feedback-loop--should-pdf-5).

* **Generador de Reportes Clínicos Automatizados:** Con un solo clic, el sistema debe consolidar los hallazgos más importantes (arritmias, tendencias de peso/líquidos, cumplimiento del paciente) en un documento PDF listo para ser integrado en la Historia Clínica Electrónica del Hospital Austral.

  * El informe debe incluir las métricas de [6.B](#6b-diccionario-de-métricas-clínicas-obligatorias--should-pdf-4) y listar automáticamente las anotaciones manuales del médico ([4.B](#4b-herramientas-de-interacción-y-medición-manual-en-trazado--could-pdf-6)).

### 4.A Trazabilidad, Evidencia y Recálculo Dinámico (CQRS / Feedback Loop) — **Should** *(PDF §5)*

Para garantizar la autoridad médica sobre los datos de [6.B](#6b-diccionario-de-métricas-clínicas-obligatorias--should-pdf-4), el backend debe implementar un flujo de trazabilidad estricto:

* **Evidencia:** Ninguna estadística crítica (FC Mínima, FC Máxima, Pausas, Máximos S/V) debe viajar al frontend como un simple valor numérico entero. Deben ser **objetos que incluyan el `timestamp` o `segment_id` de ocurrencia**.

* **Extracción de Trazado:** El frontend utilizará dicho `timestamp` para consultar una vista que retorne la ventana de señal ECG exacta (la "evidencia").

* **Comandos de Reclasificación:** Si el médico determina que la evidencia gráfica es espuria (ej. artefacto de movimiento), el sistema debe exponer una forma para **mutar el estado de ese segmento específico al GRUPO A (Ruido)** o **RECLASIFICAR**.

* **Trigger de Recálculo:** Al ejecutarse la reclasificación y confirmarse la persistencia de datos, el backend debe **desencadenar automáticamente el recálculo de todas las métricas** de [6.B](#6b-diccionario-de-métricas-clínicas-obligatorias--should-pdf-4).

* **Refresco de UI:** La respuesta del comando de recálculo debe devolver los nuevos valores estadísticos (ej. la nueva FC Máxima real) acompañados de su nuevo `timestamp`, para que el frontend grafique inmediatamente la nueva evidencia para la re-evaluación médica.

* **Edición libre de regiones:** El médico puede modificar las etiquetas (**RUIDO**, **LATIDO NORMAL**, **LATIDO ANORMAL**) a su antojo, más allá de la identificación a priori realizada por el backend. Esto también dispara el Trigger de Recálculo.

### 4.B Herramientas de Interacción y Medición Manual en Trazado — **Could** *(PDF §6)*

Para empoderar el análisis clínico visual, el visor del ECG debe proveer un set de herramientas interactivas superpuestas al canvas del gráfico, respaldadas por lógica de cálculo y persistencia:

* **Calibre de Tiempo (intervalos en eje X):** Herramienta para fijar un marcador de inicio (T1) y uno de fin (T2). El sistema calcula automáticamente la diferencia temporal (Δt en milisegundos). Es crítico para la medición manual de intervalos como el PR, la duración del QRS o el QT corregido.

* **Calibre de Amplitud (voltaje en eje Y):** Medición de la diferencia de voltaje (ΔmV) entre dos puntos verticales. Fundamental para que el médico constate manualmente los niveles de elevación o depresión del segmento ST, o mida voltajes para criterios de hipertrofia.

* **Cálculo de Área bajo la Curva (integración dinámica):** Herramienta avanzada para estimar volúmenes eléctricos (por ejemplo, analizar la morfología de la Onda P para evaluar crecimiento de la aurícula izquierda vs. derecha).

  * **UX/UI:** El médico solo necesita marcar el inicio y el fin de la onda sobre el trazado.
  * **Lógica:** El sistema debe trazar una línea base imaginaria (isoeléctrica) que una ambos puntos y aplicar un cálculo de integración matemática sobre los datos crudos para obtener el área de la curva delimitada, sin requerir que el usuario dibuje polígonos cerrados. Este cálculo puede delegarse al backend enviando `timestamp_inicio` y `timestamp_fin`.

* **Compás Digital (Caliper R-R):** Recreación de la herramienta analógica tradicional. Permite al médico "congelar" una distancia entre dos picos R sucesivos y arrastrar ese bloque de medida a lo largo del gráfico continuo. Sirve para validar visualmente la regularidad del ritmo cardíaco en distintos fragmentos del estudio.

* **Marcadores y Anotaciones Clínicas (Bookmarks):** El médico debe poder hacer clic derecho en cualquier parte de la señal para anclar una nota de texto. El backend debe exponer y persistir estas anotaciones vinculadas a su `timestamp`. **Estas observaciones manuales deben listarse automáticamente en el informe final.**

* **Ajuste de Escala Dinámico:** Controles integrados en el gráfico para modificar temporalmente la ganancia (ej. 10 mm/mV a 20 mm/mV) y la velocidad de barrido (ej. 25 mm/s a 50 mm/s), para hacer "zoom" clínico sobre complejos que requieran mayor detalle visual **sin alterar los datos subyacentes**.

---

## Módulo 5: Acompañamiento del Paciente (App Mobile)

Aprovechar la app mobile para el seguimiento del paciente en vez de usarlo como puente entre el chaleco y el servidor.

* **Asistente Inteligente de Colocación y Cuidado:** Guía visual paso a paso para la correcta posición del chaleco y, fundamentalmente, instrucciones claras para el **lavado y mantenimiento de los electrodos secos**. Esto es vital para preservar la vida útil de la prenda textil.

  * El chaleco debe lavarse de una forma específica (dentro de una red) para evitar el daño de los electrodos.

* **Diario de Eventos y Síntomas:** Interfaz simplificada para que el paciente registre palpitaciones, dolor de pecho o falta de aire con un solo toque. El sistema vincula automáticamente estos reportes con el segmento exacto de la señal eléctrica capturada por el hardware.

* **Monitor de Confianza del Equipo:** Visualización del nivel de batería y estado de la conexión. El paciente recibe tranquilidad al saber que el sistema está "velando por él" y transmitiendo sus datos correctamente a la nube.

* **Centro de Notificaciones Preventivas:** Avisos automáticos si el sistema detecta que el dispositivo se ha movido o si la medición se ha interrumpido, permitiendo al paciente corregirlo sin esperar a que el médico lo note días después.

### 5.A API de Pacientes (App Móvil) — **Must** *(PDF §2)*

| Requerimiento | Descripción técnica | Prioridad |
| --- | --- | --- |
| **Push Notifications** | Servicios de background para disparar alertas al paciente ante la detección algorítmica de anomalías en tiempo real.<br><br>Debe incluir un **protocolo de aviso** (fuera de los baches de envío) para que el chaleco avise si detecta **mala calidad de señal durante un intervalo continuo dT**. | Alta |
| **Bitácora (Post-Push)** | Formularios generados tras una alerta. El DTO debe capturar: 1) **Síntomas**, 2) **Actividad del usuario**. | Alta |
| **Registro Manual** | Formularios generados proactivamente por el paciente, en ausencia de notificaciones push algorítmicas. | Media |

---

## Módulo 6: Motor de Inteligencia Clínica y Análisis (IA)

Este módulo actúa como un "primer filtro" que procesa los lotes de datos para resaltar lo que realmente importa al cardiólogo.

* **Triage Automático de Arritmias:** Clasificación de eventos de ritmo (como Fibrilación Auricular o extrasístoles) para que el médico reciba una lista priorizada de hallazgos en su panel de control.

* **Vigilancia de Congestión (Tendencia de Impedancia):** Análisis de la evolución de la bioimpedancia nocturna para detectar cambios en la línea de base que sugieran acumulación de líquidos en los pulmones antes de que el paciente presente síntomas graves.

* **Generador de Alertas Basado en Riesgo:** Sistema de notificaciones configurables que avisa al médico solo cuando se superan umbrales clínicos específicos predefinidos para cada paciente.

* **Soporte a la Decisión (No Diagnóstico Autónomo):** El módulo se presenta como una herramienta de asistencia que marca segmentos anómalos para revisión humana, cumpliendo con los estándares regulatorios de soporte a la decisión clínica.

### 6.A Motor de Clasificación de Señal (Jerarquía de Latidos) — **Must** *(PDF §3)*

El motor de procesamiento debe etiquetar los segmentos del ECG en **3 grandes grupos**. Para los latidos válidos se requiere **análisis morfológico continuo** (ancho/alto del QRS):

* **GRUPO A — Ruido (Caso 0):** Segmentos descartados estadísticamente pero **mantenidos en el registro crudo** (ver [2.A, Persistencia Inmutable](#2a-arquitectura-y-adquisición-de-datos--must-pdf-1)).

* **GRUPO B — Latidos Normales:** Segmentos con morfología y ritmo dentro de parámetros fisiológicos.

* **GRUPO C — Latidos Arrítmicos:** Segmentos libres de ruido con anomalías, subdivididos en:

  | Caso | Hallazgo |
  | --- | --- |
  | Caso 1 | Bloqueo |
  | Caso 2 | Pausa |
  | Caso 3 | Taquiarritmias |
  | Caso 4 | TPS o TPV |
  | Caso 5 | PRs (desviaciones) |
  | Caso 6 | QT (desviaciones) |
  | Caso 7 | Extrasístoles |

### 6.B Diccionario de Métricas Clínicas Obligatorias — **Should** *(PDF §4)*

El backend debe calcular y exponer las siguientes métricas exactas, requeridas para la generación del **informe cardiológico estándar**:

| Familia | Métricas |
| --- | --- |
| **Frecuencia Cardíaca (FC)** | FC promedio, mínima y máxima (mínima y máxima **estrictamente asociadas a su `timestamp`**); latidos totales; latidos anormales; proporción anormal por mil. |
| **Pausas** | Detección y conteo de pausas de latidos **R-R > 2000 ms**. |
| **Eventos Supraventriculares (S)** | S total; S aisladas (*Single SVE*); pares (Total S Par); bigeminismo; trigeminismo; S por mil; máximo S en un minuto. |
| **Eventos Ventriculares (V)** | V total; V aisladas (*Single VE*); pares (Total V pars); bigeminismo; trigeminismo; total V corridas (*runs*); V por mil; máximo V en un minuto. |
| **Variabilidad de FC — dominio del tiempo** | SDNN (ms), SDANN (ms), rMSSD (ms), PNN50 (%), CV. |
| **Variabilidad de FC — dominio de la frecuencia** | Energía, ULF, VLF, LF, HF. |
| **Análisis ST** | Mediciones de elevación y depresión (duración en seg. y pendiente en mV/min), **sectorizadas por derivación** (ej. V1, V3, V5). |

> Estas métricas están sujetas al feedback loop de [4.A](#4a-trazabilidad-evidencia-y-recálculo-dinámico-cqrs--feedback-loop--should-pdf-5): toda reclasificación médica dispara su recálculo.

---

## Módulo 7: Centro de Exportación de Datos y Auditoría (Clinical Research Hub)

*Sin prioridad asignada en el PDF de prioridades.*

Este módulo no está pensado para el diagnóstico diario, sino para el equipo de investigación que lidera el trial. Su objetivo es la integridad y la portabilidad del dato. Dado que el objetivo de uso preliminar del sistema es para los trials clínicos, nos tenemos que asegurar que tenemos todo ordenado para usar la información de la forma correcta y eficiente.

* **Exportador Masivo para Investigación:** Permite descargar grandes volúmenes de datos (ECG e Impedancia) de múltiples pacientes en formatos estructurados (como CSV o EDF+) para realizar análisis estadísticos externos o alimentar modelos de IA de terceros.

* **Gestión de Consentimiento Informado Digital:** Un registro de que el paciente ha aceptado participar en el trial y que sus datos están siendo protegidos bajo la **Ley 25.326 de Protección de Datos Personales**.

* **Registro de Auditoría (Audit Trail):** El sistema debe registrar quién accedió a qué datos y cuándo. Esto es un requerimiento innegociable para cualquier validación clínica seria y para cumplir con las normativas de productos médicos Clase II.

  * Complementa la **Persistencia Inmutable** de [2.A](#2a-arquitectura-y-adquisición-de-datos--must-pdf-1): el registro crudo intocable es la contraparte de datos del audit trail.

* **Panel de Métricas de Población:** Una vista agregada para ver el progreso del trial: cuántos estudios se completaron, cuántos fallaron por ruido (señalizando problemas en la prenda textil) y cuál es la adherencia promedio de los pacientes al tratamiento.
