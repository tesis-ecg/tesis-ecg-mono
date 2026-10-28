# Revisión para acortar el Informe Final

Comentarios cargados en el Google Doc "Informe final" el 26/10/2026. Objetivo: pasar de unas 100 páginas a unas 70 sin perder información importante, y actualizar la arquitectura con los cambios posteriores a la redacción.

Cada entrada tiene el **ancla** (texto del Doc donde va el comentario) y el **comentario**. Se mantienen la cadencia de envío de 1 hora y el buffer de 5,1 h.

---

### C01
ANCLA: Este trabajo desarrolló el sistema de software y gestión de datos
COMENTARIO:
[Revisión general] Objetivo: bajar de ~100 a ~70 páginas. Ahorro estimado si se aplican todos los comentarios: unas 20 a 24 páginas de texto, tablas y figuras. Lo que más pesa: 1.5 (unas 2,5 p.), 8.3 y 8.4 (2,5 p.), las capturas y el texto de 9 (3 a 4 p.), las repeticiones de 11 respecto de 3.2, 5.2, 9.3 y 10.3 (2,5 p.), 5.6 (1 p.) y el marco teórico (1,5 p.). Si no alcanza, lo siguiente a recortar serían más capturas de la sección 9. Además, varios comentarios actualizan la arquitectura con lo que cambió después de la redacción: región del backend (iad1), aviso de equipo sin contacto, vista filtrada y motor de análisis en el procesamiento, 17 tablas, Playwright en la integración continua y el nuevo conteo de pruebas.

### C02
ANCLA: El sistema se validó con 356 casos de prueba
COMENTARIO:
[Resumir y actualizar] Une el párrafo con la propuesta de IA y actualiza el conteo de pruebas (1.154 casos en el backend al 26/10):

"El sistema se validó con 1.154 casos de prueba en el backend, pruebas en el dashboard y la aplicación, pruebas de punta a punta en el navegador y pruebas con el equipo real, en las que no se perdió ninguna trama ante veinte fallas del servidor. Esas pruebas mostraron también que la latencia de la plataforma serverless limita la capacidad de ingesta. El módulo de análisis automático, orientado por la revisión de cardiólogos electrofisiólogos, descarta los tramos con ruido, aplica reglas de ritmo y clasifica cada latido comparándolo con las plantillas del propio paciente. Sobre registros públicos de PhysioNet marca entre el 92 y el 100 % de los latidos anormales en los registros de alta carga arrítmica, sin hallazgos en los de control; su validación clínica con señal del chaleco queda pendiente."

### C03
ANCLA: En el otro extremo, los relojes inteligentes
COMENTARIO:
[Resumir] Repite lo que 2.6.1 dice del Apple Watch. Propuesta:

"En el otro extremo, los relojes inteligentes y las bandas deportivas son cómodos y se usan durante semanas, pero no ofrecen un registro continuo con la morfología y la validación clínica que requiere un diagnóstico electrofisiológico (sección 2.6.1)."

### C04
ANCLA: las herramientas de medición manual sobre el trazado, la exportación
COMENTARIO:
[Actualizar] Las herramientas de medición manual ya están implementadas (sección 9.3, RF22). Quitar "las herramientas de medición manual sobre el trazado," de la lista de lo que queda fuera del alcance.

### C05
ANCLA: El objeto del proyecto (el desarrollo de un dispositivo
COMENTARIO:
[Resumir 1.5.1] Si la rúbrica exige los subtítulos de 1.5, conservarlos con el texto resumido. 1.5.1 en un párrafo:

"El objeto del proyecto, un dispositivo de monitoreo cardíaco continuo, y su fin, la detección temprana de arritmias para mejorar el diagnóstico y la calidad de vida de los pacientes, son éticamente legítimos. El equipo se usa bajo prescripción médica y no presenta usos duales ni aplicaciones que puedan derivarse hacia fines moralmente cuestionables."

### C06
ANCLA: Pacientes. El sistema reduce la fricción del estudio Holter
COMENTARIO:
[Resumir 1.5.2] Los cuatro párrafos en uno:

"El sistema beneficia a los pacientes, porque la prenda sin adhesivos permite estudios más largos y aumenta la probabilidad de registrar arritmias esporádicas; a los médicos, que reciben la señal durante el estudio sin esperar la devolución del equipo; y al sistema de salud, porque un equipo de bajo costo que no depende de un teléfono favorece el acceso de poblaciones con menor alfabetización digital. En conjunto, se inscribe en una medicina preventiva y de monitoreo remoto, en línea con el desarrollo humano integral que promueve el Ideario de la Universidad Austral."

### C07
ANCLA: El proyecto contempla mitigaciones específicas frente a los riesgos
COMENTARIO:
[Resumir 1.5.3] Privacidad repite 5.5 y cumplimiento regulatorio repite 1.5.7. Propuesta:

"Los riesgos previsibles se mitigan de la siguiente manera. Los datos de salud se protegen con cifrado en tránsito y en reposo, autenticación y auditoría de los accesos (sección 5.5). El sistema es un coadyuvante diagnóstico: no emite diagnósticos automáticos, y la interfaz y el informe aclaran que la interpretación corresponde al profesional tratante. La memoria del chaleco solo descarta lo que el servidor confirmó, por lo que una falla de la red no provoca pérdidas silenciosas de información clínica. Por último, el coprocesador WiFi apagado entre envíos y la batería de 1800 mAh dan una autonomía de unos diez días, lo que reduce las recargas y el desgaste de la batería."

### C08
ANCLA: El proyecto es compatible con los derechos humanos fundamentales
COMENTARIO:
[Resumir 1.5.4] Un párrafo en lugar de cinco:

"El proyecto es compatible con los derechos humanos fundamentales, en particular con el derecho a la salud (art. 25 de la Declaración Universal). El uso del chaleco requiere la prescripción médica y el consentimiento del paciente sobre el tratamiento de sus datos; su operación no exige un teléfono ni conocimientos técnicos, y la aplicación está pensada para adultos mayores; el público objetivo, de 40 a 70 años, es el de mayor prevalencia cardiovascular; y la prenda textil evita las irritaciones y la estigmatización del cableado de los equipos tradicionales."

### C09
ANCLA: Tras la autoevaluación realizada, no se identifican aspectos
COMENTARIO:
[Resumir 1.5.6] Los cuatro párrafos repiten 1.5.1 y 1.5.3. Propuesta:

"Tras la autoevaluación, no se identifican aspectos éticamente controvertidos que requieran un Informe de Discernimiento Ético. Los riesgos del proyecto (privacidad, seguridad del paciente y cumplimiento regulatorio) son los generales de cualquier dispositivo médico conectado y cuentan con mitigaciones explícitas, y el proyecto no involucra experimentación fuera de un marco clínico controlado, usos duales ni decisiones clínicas automatizadas sin intervención profesional. Por ello se lo considera compatible con el Ideario de la Universidad Austral."

### C10
ANCLA: Los dispositivos wearables son equipos que el usuario lleva puestos
COMENTARIO:
[Resumir] Este párrafo y el siguiente en uno; el detalle de los electrodos es de Biomédica y los canales se comparan en la Tabla 6:

"Los dispositivos wearables son equipos que el usuario lleva puestos durante sus actividades diarias. En el ECG, los electrodos textiles secos son reutilizables y más cómodos que los de plata y cloruro de plata con gel, a cambio de una mayor impedancia de contacto y de una mayor sensibilidad a los artefactos de movimiento (Nigusse et al., 2021). Para comunicarse suelen usar Bluetooth de baja energía, que depende de un teléfono cercano; WiFi, con acceso directo a internet y mayor consumo instantáneo; o redes celulares para IoT como LTE-M, que requieren un módulo más costoso y un plan de datos."

### C11
ANCLA: El estilo arquitectónico REST organiza un sistema distribuido
COMENTARIO:
[Resumir 2.4] Los cuatro párrafos en dos:

"La API del sistema sigue el estilo REST, que organiza un sistema distribuido en recursos identificados por direcciones y operados con los métodos estándar de HTTP, sin estado entre interacciones (Fielding, 2000). Se ejecuta en un modelo serverless, en el que el proveedor administra la infraestructura y ejecuta el código ante cada solicitud, con dos limitaciones conocidas: el arranque en frío, la demora cuando no hay una instancia activa, y el límite de duración de cada invocación (Jonas et al., 2019). Los datos estructurados se guardan en una base relacional, con integridad y transacciones, y los archivos grandes en un almacenamiento de objetos que los entrega mediante URLs prefirmadas: direcciones temporales, firmadas por el servidor, que autorizan una descarga sin exponer las credenciales.

La identidad se apoya en OAuth 2.0, el marco estándar para delegar la autorización (Hardt, 2012); en los JSON Web Tokens, un formato firmado que transporta la identidad y los permisos (Jones et al., 2015); y en el control de acceso basado en roles, que asigna los permisos a roles y no a cada usuario (Sandhu et al., 1996)."

### C12
ANCLA: El primer componente es el chaleco, desarrollado por el equipo
COMENTARIO:
[Resumir] El detalle de los componentes vuelve a aparecer en 4.1 y en la Tabla 5. Propuesta:

"El primer componente es el chaleco, desarrollado por el equipo de Ingeniería Biomédica: una prenda con tres electrodos textiles secos que registran una derivación de ECG a 500 Hz y 24 bits. El firmware comprime la señal sin pérdida en tramas de 256 bytes y la guarda en una memoria flash de 16 MB organizada como registro circular. Como el microcontrolador no tiene WiFi, un coprocesador ESP32-C3 se enciende una vez por hora y envía las tramas acumuladas por la red del domicilio, configurada una sola vez al entregar el chaleco. Una batería de 1800 mAh le da una autonomía estimada de unos diez días."

### C13
ANCLA: Tabla 3. Cambios respecto del plan de trabajo
COMENTARIO:
[Acortar la tabla] Eliminar la fila "Frecuencia de envío" (no hubo cambio; el envío cada 10 minutos de las pruebas puede ir en una oración de 10.3) y pasar "Bluetooth" a la columna de motivo de la fila "Canal de comunicación" ("Bluetooth dejó de tener uso y se eliminó del firmware"). El párrafo que sigue a la tabla se conserva acá; la sección 11.1 lo repite y se propone acortarla.

### C14
ANCLA: Entre el 20 de mayo y el 20 de septiembre de 2026 se integraron 42
COMENTARIO:
[Actualizar] Al 26/10 son 74 solicitudes integradas desde el 20 de mayo: "Entre el 20 de mayo y el 26 de octubre de 2026 se integraron 74 solicitudes." (Revisar el número antes de entregar.)

### C15
ANCLA: AWS S3 (MinIO en desarrollo)
COMENTARIO:
[Actualizar Tabla 5] MinIO dejó de publicar imágenes y se reemplazó: "AWS S3 (RustFS en desarrollo y moto en la integración continua)". En la fila Backend agregar "SciPy, NeuroKit2, scikit-learn" (los usa el motor de 4.6) y en Calidad agregar "Playwright".

### C16
ANCLA: El hardware del chaleco, descripto en la sección 3.1, fue seleccionado
COMENTARIO:
[Resumir] Se puede reducir a una oración y dejar la comparación de canales:

"El hardware, descripto en la sección 3.1, fue seleccionado por el equipo de Biomédica: el XIAO nRF52840 se basa en un procesador Cortex-M4F de bajo consumo (Nordic Semiconductor, s.f.) y el ADS1292R es un frontal analógico de 24 bits para biopotenciales (Texas Instruments, s.f.), con un detector de QRS validado sobre la MIT-BIH Arrhythmia Database con una sensibilidad del 99,28 % y un valor predictivo positivo del 99,73 %."

### C17
ANCLA: El backend se desarrolló en Python 3.12 por su ecosistema
COMENTARIO:
[Resumir 4.2] Los tres párrafos en uno:

"El backend se desarrolló en Python 3.12, que permite mantener en un solo lenguaje la API, la decodificación de la señal con NumPy y el módulo de análisis. Se usó FastAPI (Ramírez, s.f.) por su soporte asíncrono, que atiende muchas solicitudes mientras espera a la base de datos o al almacenamiento, por la validación de datos con Pydantic y por la generación de la especificación OpenAPI, de la que se derivan los tipos TypeScript del dashboard para detectar en la integración continua cualquier diferencia de contrato. Se eligió PostgreSQL por sus transacciones, los bloqueos a nivel de fila que serializan los envíos de un mismo dispositivo, las restricciones de integridad y el tipo JSONB; el acceso se hace con SQLAlchemy 2 asíncrono y el esquema evoluciona con migraciones de Alembic. La señal, por su volumen, se guarda en un almacenamiento de objetos compatible con S3."

### C18
ANCLA: La identidad de los médicos y administradores se gestiona con Auth0
COMENTARIO:
[Resumir 4.5] Los dos párrafos en uno; la excepción y sus medidas compensatorias ya están en la Tabla 7 (ADR 003):

"Los médicos y administradores se autentican con Auth0, un proveedor de identidad externo que evita guardar contraseñas en el sistema y aporta protecciones contra ataques de fuerza bruta y contraseñas filtradas. El inicio de sesión pasa por el backend, que valida las credenciales con Auth0, verifica que la cuenta haya sido dada de alta por un administrador y emite un JWT propio en una cookie inaccesible desde JavaScript, una variante del flujo de OAuth 2.0 documentada como excepción (ADR 003). La aplicación móvil usa un token de acceso de 60 minutos y uno de renovación de 60 días, y cada chaleco se autentica con su número de serie y una clave de API propia."

### C19
ANCLA: Figura 2. Arquitectura de despliegue de la plataforma.
COMENTARIO:
[Actualizar la figura] Cambios: el backend ya no corre en São Paulo sino en iad1 (Washington D. C.), junto a Neon y S3; se agregan el ACK de vuelta al chaleco, el barrido de equipos sin contacto (GitHub Actions cada 15 min) y el motor de análisis dentro del backend. Código mermaid nuevo:

flowchart TB
    subgraph Paciente["Domicilio del paciente"]
        CH["Chaleco<br/>nRF52840 + ESP32-C3"]
        APP["App móvil<br/>Expo / React Native"]
    end
    subgraph Medico["Consultorio"]
        NAV["Navegador del médico"]
    end
    subgraph Vercel["Vercel"]
        FE["Dashboard web<br/>React + Vite"]
        BE["Backend FastAPI (región iad1)<br/>ingesta, procesamiento<br/>y motor de análisis"]
    end
    subgraph Datos["Servicios de datos (AWS, este de EE. UU.)"]
        PG[("PostgreSQL<br/>Neon")]
        S3[("Almacenamiento de objetos<br/>AWS S3")]
    end
    A0["Auth0<br/>proveedor de identidad"]
    PUSH["Servicio de notificaciones<br/>de Expo"]
    GHA["GitHub Actions<br/>barrido cada 15 min"]

    CH -- "POST /ingest/ecg-frames<br/>clave de API por dispositivo" --> BE
    BE -- "202 con lastAcceptedSeq" --> CH
    APP -- "/mobile/*<br/>token Bearer" --> BE
    NAV --> FE
    FE -- "/api/* (proxy mismo origen)<br/>cookie HttpOnly" --> BE
    NAV -- "URL prefirmada (10 min)" --> S3
    BE --> PG
    BE --> S3
    BE -- "validación de credenciales" --> A0
    BE -- "notificaciones" --> PUSH
    PUSH --> APP
    GHA -- "/internal/device-contact-sweep" --> BE

### C20
ANCLA: Una vez por hora, el ESP32-C3 envía las tramas pendientes
COMENTARIO:
[Actualizar] El puente no manda un único POST de hasta 8 MB: manda POST sucesivos de hasta 48 tramas (unos 12 kB). Los 8 MB son el límite que acepta el servidor. Propuesta para el comienzo del párrafo:

"Una vez por hora, el ESP32-C3 envía las tramas pendientes en solicitudes HTTPS sucesivas de hasta 48 tramas cada una, junto con..." (el resto igual).

### C21
ANCLA: La confirmación sigue el esquema Go-Back-N
COMENTARIO:
[Actualizar] La confirmación ahora incluye también el CRC-32 de esa trama (lastAcceptedCrc32), que le permite al equipo comprobar que el servidor confirmó la misma trama que tiene guardada. Propuesta: "...el servidor responde con el número de secuencia y el CRC-32 de la última trama que guardó en orden, y ese es el único dato que autoriza al equipo a liberar memoria."

### C22
ANCLA: Figura 3. Secuencia de envío y confirmación de un lote de tramas.
COMENTARIO:
[Actualizar la figura] Se agrega el CRC-32 al ACK y el procesamiento lote a lote en su propia transacción. Código mermaid nuevo:

sequenceDiagram
    participant D as Chaleco (ESP32-C3)
    participant I as Servicio de ingesta
    participant DB as PostgreSQL
    participant S as S3
    D->>I: POST /ingest/ecg-frames (hasta 48 tramas, serie, clave, hora NTP)
    I->>I: Autentica el equipo y valida cada trama (identificador, versión, CRC-32)
    I->>DB: Bloquea la fila del dispositivo y busca el estudio abierto
    I->>I: Calcula la ventana aceptada desde el cursor del estudio
    I->>S: Archiva las tramas aceptadas (clave determinística)
    I->>DB: Registra el lote, actualiza cursor y telemetría (commit)
    I-->>D: 202 con lastAcceptedSeq y lastAcceptedCrc32
    D->>D: Libera de la flash las tramas confirmadas
    loop Cada lote pendiente, en su propia transacción
        I->>S: Decodifica y escribe señal, vista filtrada y niveles
        I->>DB: Línea de tiempo, eventos, alertas (commit)
    end

### C23
ANCLA: Un envío sin sincronización horaria
COMENTARIO:
[Actualizar] El comportamiento cambió (commits 343f21e y e12451d): sigue rechazándose un envío sin las cabeceras de hora o con una hora absurda, pero si el puente no logra sincronizar por NTP usa la hora que devuelve la ruta de estado del servidor, y ese tramo se acepta marcado como "hora estimada". Propuesta:

"Un envío sin las cabeceras de hora, o con una hora que difiere en más de seis horas de la del servidor, se rechaza con un error específico. Si el coprocesador no logra sincronizar por NTP, informa la hora que devuelve la ruta de estado del servidor; los tramos fechados así se aceptan, pero el visor y el informe los marcan como hora estimada."

### C24
ANCLA: La recepción de un lote se divide en una etapa sincrónica
COMENTARIO:
[Actualizar] Agregar al final del párrafo lo que cambió en el procesamiento (commits 64be767 y f4ea412):

"Cada lote pendiente se procesa y confirma en su propia transacción, de modo que un atraso largo avanza lote a lote y un lote defectuoso no revierte los anteriores, y las lecturas y escrituras en el almacenamiento de objetos se ejecutan fuera del bucle de eventos para no demorar las solicitudes de otros equipos."

### C25
ANCLA: Figura 4. Procesamiento en segundo plano de un lote recibido.
COMENTARIO:
[Actualizar la figura] Faltan la vista filtrada y el motor de análisis, que hoy son parte del procesamiento y también generan alertas. Código mermaid nuevo:

flowchart LR
    A["Lote aceptado<br/>tramas crudas en S3"] --> B["Decodificación<br/>Rice a float32 en mV"]
    B --> T["Ubicación en la<br/>línea de tiempo de pared"]
    B --> C["Señal original<br/>y niveles de la pirámide"]
    B --> V["Vista filtrada<br/>notch 50 Hz y pasabanda,<br/>con sus niveles"]
    B --> F["Eventos por reglas<br/>a partir de los indicadores"]
    B --> M["Motor de análisis<br/>bloques de 5 min: calidad,<br/>ritmo, morfología y latidos"]
    M --> H["Hallazgos, calidad<br/>y métricas Holter"]
    F --> G["Alertas al médico"]
    H --> G
    G --> P["Notificación push<br/>al paciente (alta y crítica)"]

### C26
ANCLA: Un watchdog supervisa cada chaleco
COMENTARIO:
[Actualizar] El aviso por equipo sin contacto ya no depende de que un médico abra el tablero (commit 343f21e). Propuesta para insertar después de la primera oración:

"Además, una tarea programada en GitHub Actions consulta cada 15 minutos una ruta interna del backend que genera una alerta para el médico y una notificación para el paciente cuando un estudio abierto lleva dos horas sin contacto, y la cierra con el siguiente envío. GitHub no garantiza la puntualidad de estas tareas: en una prueba, la alerta llegó cuatro horas más tarde de lo previsto, por lo que se prevé reemplazar el disparador por uno externo."

### C27
ANCLA: Auditoría: se registran 27 tipos de eventos
COMENTARIO:
[Actualizar] Son 28 tipos: se agregó la reclasificación de un hallazgo automático (ECG_EVENT_RECLASSIFIED). "Auditoría: se registran 28 tipos de eventos, como los inicios de sesión, el acceso a la señal, la consulta de una clave, la reclasificación de un hallazgo y la finalización y descarga de informes; ..." Cambiar también el "27 tipos" de la Tabla 17.

### C28
ANCLA: La clasificación se resolvió con reglas sobre valores medidos
COMENTARIO:
[Resumir] Este párrafo y el siguiente ("El diseño deja abierta...") en uno; el trabajo futuro ya está en 12.3:

"La clasificación se resolvió con reglas sobre valores medidos y no con un clasificador supervisado, por tres razones: no se contaba con latidos etiquetados adquiridos con el chaleco, y las bases públicas, registradas con electrodos de gel, sirven para calibrar pero no para validar un equipo de electrodos secos; como soporte a la decisión, cada hallazgo de una regla se explica con valores que el médico puede comprobar sobre el trazado, algo que la etiqueta de un modelo entrenado no permite sin una validación diagnóstica propia; y el ruido de los electrodos secos es el principal limitante, por lo que un clasificador previo a resolverlo mediría artefactos. El diseño deja abierta la incorporación de un modelo: cada corrección del médico es una etiqueta revisada sobre señal del chaleco, y la versión del motor guardada en cada evento permite que un modelo nuevo conviva con los estudios ya analizados (sección 12.3)."

### C29
ANCLA: El médico ve los hallazgos en el panel del visor y puede confirmarlos
COMENTARIO:
[Resumir] La revisión del médico se describe también en 9.3. Dejar acá solo lo de arquitectura:

"Las correcciones del médico se guardan junto al resultado del motor, con su autor, la fecha y una nota, y las métricas se recalculan con ellas (sección 9.3). Del mismo procesamiento salen las métricas del informe Holter: frecuencia cardíaca, pausas, eventos supraventriculares y ventriculares, variabilidad de la frecuencia cardíaca en los dominios del tiempo y de la frecuencia (Task Force de la ESC y la NASPE, 1996) y nivel del segmento ST por latido."

### C30
ANCLA: MinIO como almacenamiento compatible con S3
COMENTARIO:
[Actualizar Tabla 8] Desarrollo: "RustFS" en lugar de "MinIO" (la imagen de MinIO dejó de publicarse). Producción: "Vercel (backend en la región iad1, Washington D. C.), PostgreSQL en Neon, almacenamiento en AWS S3, Auth0".

### C31
ANCLA: El backend se despliega en Vercel como función serverless en la región de São Paulo
COMENTARIO:
[Actualizar] La región cambió (commit c1c2d4f). Propuesta para la primera oración:

"El backend se despliega en Vercel como función serverless en la región de Washington D. C. (iad1). Al principio se usó la de São Paulo, la más cercana a la Argentina, pero la base de datos y el almacenamiento están en el este de los Estados Unidos y cada consulta cruzaba el continente: procesar un lote de unos 25 segundos de señal tardaba entre 10 y 36 segundos. Ubicar las funciones junto a la base de datos acorta esos viajes, que son la mayor parte del trabajo de cada lote."

### C32
ANCLA: en desarrollo se usa MinIO, que implementa la misma interfaz
COMENTARIO:
[Actualizar] "...en desarrollo se usa RustFS y en la integración continua moto, que implementan la misma interfaz."

### C33
ANCLA: Cada solicitud de integración y cada cambio en la rama principal ejecutan
COMENTARIO:
[Actualizar 6.3] Desde el 5/10 la integración continua incluye pruebas de punta a punta con Playwright y las del backend corren en paralelo y solo si cambia el backend. Propuesta:

"Cada solicitud de integración y cada cambio en la rama principal ejecutan en GitHub Actions los trabajos de la Tabla 9. Las pruebas del backend se ejecutan en paralelo y solo cuando cambian sus archivos. Cuando el cambio toca el dashboard o el backend, además, Playwright maneja un navegador real contra el stack completo (dashboard, backend, PostgreSQL y un servidor S3 simulado), con un inicio de sesión real en Auth0, y publica el resultado y las capturas en la solicitud de integración. Antes de cada commit, un gancho de Git formatea y revisa los archivos modificados."

Agregar a la Tabla 9 la fila: "Punta a punta | Playwright sobre el stack completo: inicio de sesión, tablero, navegación, visor de ECG, resumen del paciente, informe clínico, reclasificación de hallazgos y vigencia de la sesión".

### C34
ANCLA: Figura 5. Diagrama entidad relación de la plataforma
COMENTARIO:
[Actualizar la figura y el texto] Hoy son 17 tablas (se agregaron signal_quality_interval y ecg_interval_measurement). En 7.1 cambiar "quince tablas" por "diecisiete", y agregar en 7.2: "Análisis automático. Los intervalos de calidad guardan, por tramo, si la señal es analizable, marginal o no analizable y el motivo; las mediciones de intervalos guardan el QT y el QRS por tramo como dato de investigación; y cada evento registra la versión del motor que lo produjo y, si el médico lo revisó, su decisión, el autor, la fecha y la nota." Para que la figura ocupe menos, el código deja solo los atributos que el texto menciona:

erDiagram
    USER ||--o| DOCTOR : "es"
    USER ||--o| PATIENT : "accede como"
    USER ||--o{ PUSH_TOKEN : "registra"
    USER ||--o{ AUDIT_EVENT : "genera"
    DOCTOR ||--o{ PATIENT : "atiende"
    DOCTOR ||--o{ DEVICE : "tiene asignados"
    PATIENT ||--o{ DEVICE : "usa"
    PATIENT ||--o{ STUDY : "realiza"
    PATIENT ||--o{ ALERT : "recibe"
    PATIENT ||--o{ PATIENT_REPORT : "registra"
    DEVICE ||--o{ STUDY : "registra"
    DEVICE ||--o{ ECG_BATCH : "envía"
    STUDY ||--o{ ECG_BATCH : "agrupa"
    STUDY ||--o{ STUDY_TIMELINE_SEGMENT : "se ubica en"
    STUDY ||--o{ SIGNAL_QUALITY_INTERVAL : "califica"
    STUDY ||--o{ ECG_INTERVAL_MEASUREMENT : "mide"
    STUDY ||--o| STUDY_CLINICAL_REPORT_DRAFT : "tiene"
    STUDY ||--o{ STUDY_CLINICAL_REPORT : "versiona"
    ECG_BATCH ||--o{ ECG_EVENT : "origina"
    ECG_EVENT ||--o{ ALERT : "dispara"
    ALERT ||--o| PATIENT_REPORT : "es respondida por"

    STUDY {
        uuid id PK
        string status
        int last_ingested_seq
        jsonb ecg_pyramid_levels
        string ecg_sha256
        int ml_analyzed_samples
    }
    ECG_BATCH {
        uuid id PK
        int first_seq
        int last_seq
        int preceding_seq_gap_frames
        string processing_status
    }
    ECG_EVENT {
        uuid id PK
        string event_type
        string severity
        string model_version
        string validation_status
        jsonb correction
    }
    STUDY_TIMELINE_SEGMENT {
        uuid id PK
        int boot_id
        bigint start_epoch_ms
        float anchor_slope_ppm
    }
    SIGNAL_QUALITY_INTERVAL {
        uuid id PK
        bigint start_sample_index
        string level
        string reason
    }
    STUDY_CLINICAL_REPORT {
        uuid id PK
        int version
        jsonb snapshot
        string pdf_sha256
    }

### C35
ANCLA: Las entidades se agrupan en cinco áreas
COMENTARIO:
[Resumir 7.2] Varias reglas ya aparecen en 5.2, 5.4 y 9.5 (cursor, huecos, borrador con revisiones). Se puede dejar cada área en una oración y sumar la de análisis automático del comentario de la Figura 5. Ahorro aproximado: media página.

### C36
ANCLA: Herramientas de medición manual sobre el trazado: calibres
COMENTARIO:
[Actualizar RF22] Estado: "Implementado: calibres de tiempo y amplitud, área bajo la curva y compás R-R (sección 9.3); los marcadores con notas persistentes quedan como trabajo futuro".

### C37
ANCLA: Tabla 13. Historias de usuario
COMENTARIO:
[Acortar la tabla] Las 15 historias repiten casi una a una los RF de la Tabla 11 (unas 3 páginas). Propuesta: dejar 8 historias (HU04, HU05, HU06, HU07, HU09, HU10, HU13 y HU14), que son las que tienen criterios de aceptación propios del dominio, y agregar una oración antes de la tabla: "Las historias de gestión de usuarios, pacientes y chalecos (RF01 a RF04) y la del simulador (RF18) se omiten porque sus criterios coinciden con el requerimiento." Si se conserva la Tabla 13 completa, agregar una historia para la revisión del análisis automático: "Como médico, quiero confirmar, descartar o reclasificar un hallazgo automático, para que el informe refleje mi criterio. | La corrección se guarda junto a la detección original con autor, fecha y nota, y las métricas del informe se recalculan."

### C38
ANCLA: Figura 6. Flujo principal de un estudio.
COMENTARIO:
[Actualizar la figura] Falta el análisis automático y la revisión de hallazgos por el médico. Código mermaid nuevo:

flowchart TD
    A["Administrador da de alta al médico<br/>y registra el chaleco"] --> B["Administrador asigna<br/>el chaleco al médico"]
    B --> C["Médico da de alta al paciente<br/>(se crea su cuenta de la app)"]
    C --> D["Médico asigna el chaleco al paciente<br/>y se configura el WiFi del domicilio"]
    D --> E["El chaleco graba y envía<br/>un lote cada hora"]
    E --> F{"¿Primer lote?"}
    F -- "Sí" --> G["Se abre el estudio"]
    F -- "No" --> H["Se incorpora al estudio abierto"]
    G --> H
    H --> I["Procesamiento: señal, línea de tiempo,<br/>análisis automático, eventos y alertas"]
    I --> J["El médico revisa la señal y el análisis,<br/>confirma o reclasifica hallazgos<br/>y ve los reportes del paciente"]
    J --> K{"¿Fin del período?"}
    K -- "No" --> E
    K -- "Sí" --> L["El médico redacta el borrador<br/>y finaliza el informe clínico"]
    L --> M["El médico finaliza el estudio<br/>o desasigna el chaleco"]

### C39
ANCLA: Figura 7. Estados de un estudio.
COMENTARIO:
[Eliminar la figura] Son tres estados que ya se explican en 5.4 y en RF07. Reemplazar la figura y su texto introductorio por una oración: "Un estudio queda en curso desde el primer lote y pasa a completado cuando el médico lo finaliza, cuando se desasigna el chaleco o cuando el equipo reinicia su numeración, o a cancelado cuando el médico lo cancela." Ahorra media página.

### C40
ANCLA: Figura 10. Detalle de un paciente.
COMENTARIO:
[Eliminar la captura] Es la pantalla con menos información nueva de la sección. El párrafo que la presenta alcanza para describirla (quitar "(Figura 10)" y renumerar las siguientes). Ahorra entre media página y una página. Lo mismo vale para la Figura 12 (bandeja de alertas), que se describe en una oración.

### C41
ANCLA: Para apoyar la revisión del trazado, el visor incorpora cuatro
COMENTARIO:
[Resumir] Este párrafo y el siguiente ("Las mediciones se calculan...") en uno:

"Para medir sobre el trazado, el visor incorpora un calibre de tiempo, que informa la duración en milisegundos y la frecuencia equivalente; un calibre de amplitud, en milivoltios; una herramienta de área respecto de la recta que une los extremos, en µV·s; y un compás R-R, que repite el intervalo entre dos latidos para comparar la regularidad del ritmo (Figura 13.1). Las mediciones se calculan sobre los tiempos y valores de las muestras, por lo que no dependen del tamaño del gráfico ni de la ampliación; solo se habilitan cuando la vista muestra las muestras y no su resumen, no se informan a través de un hueco del registro y no se guardan de forma persistente. Sobre la vista filtrada, el visor advierte que permite medir intervalos pero no evaluar la amplitud del QRS."

### C42
ANCLA: Desde este panel, el médico puede descartar un hallazgo automático
COMENTARIO:
[Resumir] "Desde este panel, el médico puede confirmar, descartar o reclasificar un hallazgo automático, con una nota opcional; la detección original se conserva junto a la revisión, con su autor y fecha, y la corrección se refleja en el trazado y en las métricas del informe."

### C43
ANCLA: La pestaña de análisis del estudio (Figura 14) reúne
COMENTARIO:
[Resumir 9.4] Los cuatro párrafos en dos. Además, hay dos "Figura 14": esta y la del informe clínico; renumerar desde acá.

"La pestaña de análisis del estudio (Figura 14) reúne los resultados del procesamiento automático en tres bloques, que se actualizan mientras el estudio está en curso. El bloque de calidad muestra el porcentaje y la duración de señal analizable, marginal y no analizable, y los principales motivos de deterioro, lo que permite distinguir la ausencia de hallazgos de la falta de señal evaluable. El bloque de ritmo y variabilidad presenta la frecuencia cardíaca promedio, mínima y máxima, el total de latidos, las pausas R-R y la más larga, los indicadores SDNN, rMSSD y pNN50 y el tiempo excluido por calidad.

El bloque de hallazgos separa las ectopias supraventriculares (S) y ventriculares (V), con el total, los latidos aislados, la proporción por cada mil latidos y los pares, corridas, bigeminismos y trigeminismos. La frecuencia mínima y máxima, la pausa más larga, el máximo de ectopias en un minuto y la corrida más larga incluyen su fecha y hora y un acceso al tramo del visor que las respalda. Cuando la clasificación de latidos no está disponible, la pantalla lo indica en lugar de mostrar ceros."

### C44
ANCLA: Las métricas utilizadas para preparar el informe incorporan las correcciones
COMENTARIO:
[Resumir] Este párrafo y el siguiente repiten lo dicho en 9.3. Propuesta para ambos:

"Las métricas del borrador incorporan las correcciones del médico sobre los hallazgos. Al finalizar, el PDF se genera en el navegador y el backend verifica que sea un PDF válido, que los datos coincidan con la vista previa y que el borrador no haya cambiado, y lo guarda como una versión inmutable descargable desde el historial; una corrección posterior se refleja en una nueva versión, sin modificar las ya emitidas."

### C45
ANCLA: Figura 16. Pantalla de inicio de la aplicación del paciente.
COMENTARIO:
[Opcional] Si hace falta más espacio, se puede quitar una de las dos capturas de esta figura y dejar la Figura 17, que muestra el flujo central (registro de síntomas).

### C46
ANCLA: La estrategia de pruebas combinó cinco niveles
COMENTARIO:
[Actualizar] Falta el nivel de punta a punta en el navegador. Agregar después de las pruebas de contrato: "pruebas de punta a punta del dashboard con Playwright, que manejan un navegador real contra el stack completo con un inicio de sesión real;" y cambiar "cinco niveles" por "seis niveles".

### C47
ANCLA: Tabla 14. Pruebas automatizadas por componente
COMENTARIO:
[Actualizar la tabla] Conteo al 26/10:
- Backend: pytest, 79 archivos, 1.154 casos. Alcance: ingesta, decodificación, motor de análisis (594 casos, sección 10.6), notificaciones, claves de dispositivos, autenticación, alcances por rol, estudios, informes clínicos, línea de tiempo y seguridad.
- Dashboard: Vitest, 55 archivos, 438 casos.
- Aplicación móvil: Vitest, 6 archivos, 38 casos.
- Nueva fila: Punta a punta, Playwright, 8 archivos: inicio de sesión, tablero, navegación, visor de ECG, resumen del paciente, informe clínico, reclasificación de hallazgos y sesión.
Cambiar también "356 casos" en la Tabla 17.

### C48
ANCLA: A partir de estas mediciones se tomaron las siguientes decisiones
COMENTARIO:
[Acortar y actualizar] La lista repite 11.2, 11.3 y 11.5. Propuesta: "A partir de estas mediciones se eliminó el trabajo que crecía con la duración del estudio (sección 11.3), se corrigió la aceptación de los huecos por desborde del buffer (sección 11.5), se documentó que la dirección de ingesta del equipo no debe incluir el prefijo del proxy del dashboard y se trasladó el backend a la región de la base de datos, dejando la reducción de fondo de la latencia como trabajo futuro (sección 11.2)." Si se conserva, mencionar acá que en estas pruebas el envío se configuró cada 10 minutos (sale de la Tabla 3).

### C49
ANCLA: La arquitectura de comunicación cambió cuando el desarrollo del software
COMENTARIO:
[Resumir 11.1] El cambio de memoria y la autonomía ya están en 3.2. Propuesta:

"Los cambios de hardware de la Tabla 3 llegaron con el software en marcha, y el de la memoria convirtió el envío de datos en parte crítica de la grabación. La solución fue separar ambos sistemas con un contrato basado en tramas autocontenidas y en una regla de confirmación independiente del transporte, por lo que el paso de Bluetooth a WiFi no requirió cambios de fondo en la plataforma, y recalcular los umbrales, como los del watchdog, contra la autonomía medida sobre la placa real."

### C50
ANCLA: Las pruebas con el equipo real mostraron una latencia fija
COMENTARIO:
[Actualizar 11.2] Desde la redacción se tomaron dos medidas más (región iad1 y una transacción por lote). Propuesta para los dos párrafos:

"Las pruebas con el equipo real mostraron una latencia fija de unos cinco segundos por envío, independiente del tamaño del lote (sección 10.3). Sus causas son el arranque en frío de la función en casi todos los envíos, porque el equipo envía con poca frecuencia; la apertura de una conexión nueva a la base de datos en cada solicitud; y la ejecución del procesamiento dentro de la misma invocación, que la plataforma corta sin respuesta al superar su límite de duración, lo que explica los cortes a los 25,7 s.

Como primeras medidas, el equipo de Biomédica amplió los tiempos de espera del firmware; las funciones se trasladaron de São Paulo a la región de Washington D. C., junto a la base de datos y al almacenamiento, porque procesar un lote de 25 segundos de señal llegaba a tardar entre 10 y 36 segundos; y cada lote pendiente pasó a confirmarse en su propia transacción, para que un atraso largo avance aunque supere la duración de una invocación. La solución de fondo, que queda como trabajo futuro, es separar la recepción del procesamiento mediante una cola y trasladar la ingesta a un servicio que se mantenga activo, con conexiones persistentes a la base de datos."

### C51
ANCLA: Inicialmente, cada lote se ubicaba en el tiempo según el momento
COMENTARIO:
[Resumir 11.4] El mecanismo ya se describe en 5.2. Propuesta: "Inicialmente, cada lote se ubicaba en el tiempo según el momento en que llegaba al servidor, con errores de 5,1 s de mediana y picos de 22,8 s, y sin resolver los reinicios. Se acordó con Biomédica que el coprocesador informe la hora NTP y su incertidumbre en cada envío, y el servidor reconstruye la línea de tiempo por arranque con corrección de la deriva (sección 5.2)."

### C52
ANCLA: Queda como limitación que, en estudios largos, la vista general
COMENTARIO:
[Actualizar] Ya no es una limitación: al ampliar, el visor descarga y dibuja las muestras originales (commit 16652b3). Propuesta: "La vista general de un estudio largo usa la envolvente de mínimos y máximos, pero al ampliar el visor dibuja las muestras originales, que son las que usan las mediciones y el informe impreso." Además, el primer párrafo se puede acortar dejando solo los dos valores medidos (22,9 mm/mV y 7,2 a 24,0 mm/s).

### C53
ANCLA: 8. Validación automatizada y con el hardware real
COMENTARIO:
[Actualizar] "1.154 casos de prueba en el backend con cobertura mínima del 65 %, pruebas de punta a punta con Playwright en la integración continua e integración con el equipo real; resta la prueba de carga con múltiples dispositivos." En la fila 6 cambiar "27 tipos de eventos" por "28".

### C54
ANCLA: La segunda es que las mediciones sobre el hardware real
COMENTARIO:
[Resumir 12.2] Se pueden unir la segunda y la tercera lección: "La segunda es que las mediciones reales deben reemplazar a los supuestos lo antes posible: la autonomía del buffer, la compresión, los indicadores del equipo, la escala del visor y la latencia de la plataforma resultaron peores de lo estimado. En particular, los supuestos del modelo serverless (solicitudes cortas y tráfico que mantiene activas las instancias) no coinciden con los de un dispositivo que envía lotes grandes cada cierto tiempo, y las características del tráfico debieron evaluarse antes de elegir dónde ejecutar la ingesta."

### C55
ANCLA: Reducir la latencia de la ingesta separando la recepción
COMENTARIO:
[Actualizar] El aviso periódico ya existe. Cambiar el final por: "...con un servicio que permanezca activo y conexiones persistentes a la base de datos, y reemplazar el disparador de GitHub Actions del aviso de equipo sin contacto por uno que garantice su frecuencia."

### C56
ANCLA: Completar el análisis automático de la señal con la evidencia
COMENTARIO:
[Actualizar] La evidencia de cada métrica, la reclasificación y el recálculo ya están implementados (RF21). Dejar solo la parte nueva: "Validar el análisis automático con señal clínica del chaleco, usar las correcciones de los médicos para entrenar un clasificador de latidos e incorporar la detección de fibrilación auricular."

### C57
ANCLA: Agregar herramientas de medición manual sobre el trazado
COMENTARIO:
[Actualizar] Los calibres, el compás R-R y el área ya están implementados, y al ampliar ya se dibujan las muestras originales. Reemplazar por: "Persistir las mediciones manuales y agregar marcadores con notas sobre el trazado."
