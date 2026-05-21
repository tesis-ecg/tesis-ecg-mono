# Requerimientos \- Sistema de Telemetría Cardíaca

### Módulo 1: Gestión de Dispositivos y Modelo de Suscripciones

Este módulo es el pilar comercial. Su objetivo es que el administrador pueda controlar quién usa la tecnología y asegurarse de que el hardware esté operativo durante el ensayo clínico.

* **Identidad Única del Equipo:** Cada chaleco debe ser reconocido automáticamente por el sistema al encenderse, vinculando el hardware físico con un registro digital para evitar errores en la asignación de pacientes.

* **Gestión de "Créditos de Monitoreo":** El sistema debe permitir al administrador cargar "tiempo de uso" o "cantidad de estudios" a una cuenta médica. Una vez agotado el crédito, el sistema notificará al profesional y restringirá el inicio de nuevos estudios hasta la renovación de la suscripción.

* **Tablero de Salud del Hardware:** El personal administrativo debe poder ver de un vistazo si los equipos están cargados, si tienen buena señal celular en el domicilio del paciente y si están transmitiendo datos correctamente.

  * En caso de que el adsr mande que está midiendo mal, se le debe notificar al paciente vía la app mobile (los primeros

* **Guardián del Ensayo (Watchdog):** Si un paciente deja de usar el dispositivo o este se apaga durante el periodo del trial, el sistema debe generar una alerta automática para que el equipo de soporte intervenga y no se pierdan días de estudio.

### Módulo 2: Ingesta de Datos y Aseguramiento de la Calidad Clínica

Este módulo es el "cerebro invisible". Su función es recibir los datos crudos y transformarlos en algo que un cardiólogo pueda firmar como un diagnóstico válido.

* **Recepción Multimodal Inteligente:** El sistema debe procesar en paralelo la actividad eléctrica (ECG) y la acumulación de líquidos (Impedancia), organizando la información para que el médico pueda ver ambas facetas del corazón del mismo paciente de forma sincronizada.

  * El ADSR exporta esto en un formato de 72 bits cada 2 milisegundos. Esto, se parte en 3 secciones: 

    * La primera: (24 bits) Estado de los electrodos

    * La segunda: (24 bits) ECG

    * La tercera: (24 bits) impedancia

  * El sistema debe ser capaz de recopilar esta estructura y, desde el microcontrolador o el servidor (a definir), procesarla. 

* **Filtro de Confianza Clínica:** Antes de mostrar la señal al médico, el sistema debe "limpiar" automáticamente el ruido provocado por el movimiento del paciente, la fricción de la prenda o la interferencia eléctrica del hogar.

* **Semáforo de Calidad de Señal:** Si el paciente se colocó mal el chaleco o un electrodo seco perdió contacto, el sistema debe identificar ese segmento de tiempo como "No Diagnóstico" y notificarlo, evitando que el médico pierda tiempo analizando ruido.

* **Reconstrucción Fiel de la Experiencia:** El sistema debe garantizar que los datos recolectados en la memoria del equipo lleguen íntegros a la nube, incluso si hay cortes en la señal celular durante el día o la noche.

* **Exportación para Integración Hospitalaria:** Los resultados deben poder descargarse en formatos que el Hospital Austral ya utilice en sus sistemas de cardiología, facilitando que el estudio se incorpore a la historia clínica del paciente.

### Módulo 3: Configuración de Protocolos y Orquestación de Estudios

Este módulo es el "centro de mando" donde el médico prescribe cómo debe comportarse el hardware según la patología del paciente.

* **Prescripción Digital del Estudio:** Antes de entregar el chaleco, el médico debe poder configurar desde el sistema qué "módulos" de medición se activan: solo ECG (Holter), solo Impedancia (Seguimiento de Insuficiencia Cardíaca) o un modo combinado.

* **Programación de Ventanas Operativas:** El profesional puede programar horarios específicos de medición (ej. "Monitoreo de Impedancia de 22:00 a 06:00") para capturar datos en reposo y extender la autonomía de la batería del dispositivo.

* **Configuración de Umbrales de Alerta:** Espacio para que el médico defina qué constituye un "evento crítico" para un paciente en particular (ej. una frecuencia cardíaca menor a 40 lpm), asegurando que el sistema solo notifique lo que es clínicamente relevante para ese caso.

  * Deberíamos poder además identificar los eventos más fáciles, tanto de arritmias como de cambios bruscos de impedancia. 

### Módulo 4: Consola Clínica de Análisis (Dashboard Médico)

Es la interfaz principal del cardiólogo y electrofisiólogo. Su objetivo es la eficiencia: permitir analizar días de datos en pocos minutos.

* **Visualizador de Señales de Alta Fidelidad:** Una línea de tiempo interactiva que permite al médico "navegar" por los registros de ECG e Impedancia con fluidez, haciendo zoom en eventos específicos sin perder el contexto del estudio completo.

  * Hacer benchmarks de sistemas de análisis de holter. Hacen resúmenes de eventos, diferencian ruido (dispositivo mal colocado), etc.

* **Correlación Multimodal Sincronizada:** El dashboard debe presentar en una misma vista la actividad eléctrica y la tendencia de líquidos (impedancia). Esto permite al médico ver, por ejemplo, si una arritmia detectada coincide con un aumento en la congestión pulmonar del paciente.

  * Buscar sistemas de análisis de impedancia torácica. Funciona igual que ver un ECG porque el valor va variando a lo largo del ciclo cardiaco. 

* **Gestión y Validación de Hallazgos:** Herramienta para que el médico valide, corrija o descarte las anomalías detectadas automáticamente por la inteligencia artificial, manteniendo siempre el control final sobre el diagnóstico.

* **Generador de Reportes Clínicos Automatizados:** Con un solo clic, el sistema debe consolidar los hallazgos más importantes (arritmias, tendencias de peso/líquidos, cumplimiento del paciente) en un documento PDF listo para ser integrado en la Historia Clínica Electrónica del Hospital Austral.

### Módulo 5: Acompañamiento del Paciente (App Mobile)

Aprovechar la app mobile para el seguimiento del paciente en vez de usarlo como puente entre el chaleco y el  servidor.

* **Asistente Inteligente de Colocación y Cuidado:** Guía visual paso a paso para la correcta posición del chaleco y, fundamentalmente, instrucciones claras para el **lavado y mantenimiento de los electrodos secos**. Esto es vital para preservar la vida útil de la prenda textil.

  * El chaleco debe lavarse de una forma específica (dentro de una red) para evitar el daño de los electrodos. 

* **Diario de Eventos y Síntomas:** Interfaz simplificada para que el paciente registre palpitaciones, dolor de pecho o falta de aire con un solo toque. El sistema vincula automáticamente estos reportes con el segmento exacto de la señal eléctrica capturada por el hardware.

* **Monitor de Confianza del Equipo:** Visualización del nivel de batería y estado de la conexión. El paciente recibe tranquilidad al saber que el sistema está "velando por él" y transmitiendo sus datos correctamente a la nube.

* **Centro de Notificaciones Preventivas:** Avisos automáticos si el sistema detecta que el dispositivo se ha movido o si la medición se ha interrumpido, permitiendo al paciente corregirlo sin esperar a que el médico lo note días después.

### Módulo 6: Motor de Inteligencia Clínica y Análisis (IA)

Este módulo actúa como un "primer filtro" que procesa los lotes de datos para resaltar lo que realmente importa al cardiólogo.

* **Triage Automático de Arritmias:** Clasificación de eventos de ritmo (como Fibrilación Auricular o extrasístoles) para que el médico reciba una lista priorizada de hallazgos en su panel de control.

* **Vigilancia de Congestión (Tendencia de Impedancia):** Análisis de la evolución de la bioimpedancia nocturna para detectar cambios en la línea de base que sugieran acumulación de líquidos en los pulmones antes de que el paciente presente síntomas graves.

* **Generador de Alertas Basado en Riesgo:** Sistema de notificaciones configurables que avisa al médico solo cuando se superan umbrales clínicos específicos predefinidos para cada paciente.

* **Soporte a la Decisión (No Diagnóstico Autónomo):** El módulo se presenta como una herramienta de asistencia que marca segmentos anómalos para revisión humana, cumpliendo con los estándares regulatorios de soporte a la decisión clínica.

### Módulo 7: Centro de Exportación de Datos y Auditoría (Clinical Research Hub)

Este módulo no está pensado para el diagnóstico diario, sino para el equipo de investigación que lidera el trial. Su objetivo es la integridad y la portabilidad del dato. Dado que el objetivo de uso preliminar del sistema es para los trials clínicos, nos tenemos que asegurar que tenemos todo ordenado para usar la información de la forma correcta y eficiente. 

* **Exportador Masivo para Investigación:** Permite descargar grandes volúmenes de datos (ECG e Impedancia) de múltiples pacientes en formatos estructurados (como CSV o EDF+) para realizar análisis estadísticos externos o alimentar modelos de IA de terceros.

* **Gestión de Consentimiento Informado Digital:** Un registro de que el paciente ha aceptado participar en el trial y que sus datos están siendo protegidos bajo la **Ley 25.326 de Protección de Datos Personales**.

* **Registro de Auditoría (Audit Trail):** El sistema debe registrar quién accedió a qué datos y cuándo. Esto es un requerimiento innegociable para cualquier validación clínica seria y para cumplir con las normativas de productos médicos Clase II.

* **Panel de Métricas de Población:** Una vista agregada para ver el progreso del trial: cuántos estudios se completaron, cuántos fallaron por ruido (señalizando problemas en la prenda textil) y cuál es la adherencia promedio de los pacientes al tratamiento.

