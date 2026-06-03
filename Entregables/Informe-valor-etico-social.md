# Informe de Valor Ético y Social

**Proyecto Final de Carrera — Ingeniería en Informática**
**Universidad Austral — Facultad de Ingeniería**

**Título del proyecto:** Holter Wearable ECG — Dispositivo de monitoreo cardíaco continuo integrado en prenda textil

**Alumnos (Ing. en Informática):** Tomás Serra y Martín Barreiro
**Director:** Federico Ruiz

**Equipo interdisciplinario (Ing. Biomédica):** Gonzalo Oxoby y Juan Bautista Buthet
**Director:** Dr. Federico Bustos

**Fecha:** Junio de 2026

---

## 1. Resumen del proyecto

El presente Trabajo Final de Grado consiste en el desarrollo de un dispositivo wearable tipo Holter ECG integrado en una prenda textil (chaleco/top) con electrodos secos, orientado al monitoreo cardíaco preventivo y continuo en pacientes ambulatorios. El sistema captura tres canales de ECG mediante un front-end analógico controlado por un microcontrolador de bajo consumo (XIAO Nordic), almacena la señal en memoria local (microSD) y la transmite de forma autónoma cada hora a un backend en la nube a través de un módulo celular LTE-M, sin requerir aplicación móvil ni conectividad WiFi del paciente. Un dashboard web permite al equipo médico acceder a los registros para su evaluación.

El equipo de Ing. en Informática se ocupa del firmware embebido, el backend (FastAPI + PostgreSQL + S3), el dashboard médico y la gestión de datos. El equipo de Ing. Biomédica desarrolla el hardware (PCB, AFE), los electrodos y la prenda textil.

---

## 2. Autoevaluación ética y social

### 2.1 Principios generales de la ética: objeto y fin

El **objeto** del proyecto —el desarrollo de un dispositivo de monitoreo cardíaco continuo— y su **fin** —contribuir a la detección temprana de arritmias y patologías cardíacas, mejorando el diagnóstico y la calidad de vida de los pacientes— son éticamente legítimos y compatibles con los principios generales de la ética. El proyecto busca un bien objetivo: la salud de las personas, en un dominio donde el diagnóstico oportuno tiene impacto directo sobre la morbilidad y la mortalidad cardiovascular, principal causa de muerte en Argentina y en el mundo.

No se identifican circunstancias propias del diseño del proyecto que modifiquen negativamente la valoración ética de la acción. El dispositivo está orientado a un uso médico legítimo, prescrito por profesionales, y no presenta usos duales relevantes ni aplicaciones que puedan derivarse hacia fines moralmente cuestionables.

### 2.2 Impacto positivo, ético y social sobre los stakeholders

**Pacientes.** El sistema reduce la fricción del estudio Holter tradicional: la prenda textil con electrodos secos elimina la incomodidad de los electrodos adhesivos, mejora la adherencia al estudio prolongado y permite registros de mayor duración. Esto se traduce en mayor probabilidad de capturar eventos arrítmicos esporádicos que un Holter convencional de 24 hs puede no detectar.

**Profesionales médicos.** El dashboard centraliza los registros y facilita la revisión, reduciendo el tiempo entre la captura del evento y la decisión clínica. La transmisión continua vía celular elimina la dependencia de que el paciente retorne físicamente con el dispositivo para descargar los datos.

**Sistema de salud.** Al ser un dispositivo de bajo costo relativo y operación autónoma (sin app móvil ni WiFi), favorece el acceso a poblaciones con menor alfabetización digital o sin conectividad domiciliaria, lo que aporta a la equidad en el acceso a tecnología diagnóstica.

**Bien común.** El proyecto se inscribe en una línea de medicina preventiva y monitoreo remoto que contribuye a desplazar el sistema de salud desde un modelo reactivo hacia uno proactivo, en línea con el desarrollo humano integral que promueve el Ideario de la Universidad Austral.

### 2.3 Medidas para minimizar efectos negativos

El proyecto contempla mitigaciones específicas frente a los riesgos previsibles:

**Privacidad y protección de datos de salud.** Los registros de ECG son datos sensibles bajo la Ley 25.326 de Protección de Datos Personales. El backend cifra los datos en tránsito (TLS) y en reposo (S3 server-side encryption), el acceso al dashboard requiere autenticación y se mantienen registros de auditoría de acceso. Se prevé una política de retención que permita al paciente solicitar la eliminación de sus datos.

**Seguridad del paciente.** El dispositivo es un coadyuvante diagnóstico, no un reemplazo del juicio médico. La documentación y la interfaz aclaran que la interpretación del ECG y cualquier decisión clínica corresponden al profesional tratante. El dispositivo no emite diagnósticos automáticos ni alertas vinculantes al paciente.

**Cumplimiento regulatorio.** Se sigue el marco de ANMAT para dispositivos médicos clase II y se documentan los procesos en línea con las exigencias regulatorias aplicables, aunque la certificación formal excede el alcance del Trabajo Final.

**Confiabilidad técnica.** La microSD funciona como buffer de seguridad: si la red celular falla, los datos se acumulan localmente hasta dos días y se reenvían al recuperar señal, evitando la pérdida de información clínica.

**Sustentabilidad.** El uso de un único módulo celular de bajo consumo (PSM con ~3 µA en reposo) y una batería Li-Po de 500-800 mAh permite una autonomía de 2-3 días, reduciendo la frecuencia de recarga y el impacto ambiental asociado al ciclo de vida de la batería.

### 2.4 Compatibilidad con los derechos humanos

El proyecto es compatible con los derechos humanos fundamentales, en particular con el derecho a la salud (art. 25 de la Declaración Universal). No se identifican aspectos que puedan vulnerar derechos de los pacientes ni de terceros:

- **Consentimiento informado.** El uso del dispositivo está mediado por la prescripción médica y supone el consentimiento explícito del paciente respecto al tratamiento de sus datos.
- **No discriminación.** El diseño no presupone capacidades técnicas particulares del paciente: la operación es completamente autónoma, sin necesidad de smartphone, lo que favorece el acceso de adultos mayores y de personas con menor alfabetización digital.
- **Equidad.** El público objetivo (pacientes de 40-70 años en Argentina) está deliberadamente orientado a un segmento donde la prevalencia de patologías cardiovasculares es alta y donde el costo y la accesibilidad del estudio Holter convencional pueden ser una barrera.
- **Dignidad.** La prenda textil con electrodos secos preserva la comodidad y la dignidad del paciente durante el monitoreo prolongado, evitando irritaciones y la estigmatización visible asociada al cableado de equipos médicos tradicionales.

### 2.5 Integridad académica

El trabajo se desarrolla bajo estándares de integridad académica. Todas las fuentes consultadas —bibliografía técnica, normativa regulatoria, hojas de datos de componentes, publicaciones científicas sobre ECG y monitoreo cardíaco— se citan en formato APA en el documento `Bibliografia.md` y se integran en el Plan de Trabajo y la documentación final. El código fuente, los esquemáticos y la documentación técnica son producción original del equipo; cuando se utilizan librerías de código abierto, frameworks o componentes de terceros (FastAPI, React, shadcn/ui, Radix, etc.) se respetan sus licencias y se atribuye su autoría. No se incurre en fraude, plagio ni en presentación de resultados no verificables.

---

## 3. Informe de Discernimiento Ético

Tras la autoevaluación realizada en el punto anterior, **no se identifican aspectos éticos del proyecto que puedan resultar cuestionables** y que requieran un informe de discernimiento ético específico.

**Fundamento.** El proyecto presenta un objeto (un dispositivo médico de monitoreo) y un fin (la detección temprana de patologías cardíacas) éticamente legítimos. Los riesgos previsibles —privacidad de datos de salud, seguridad del paciente, cumplimiento regulatorio— son riesgos generales asociados a cualquier dispositivo médico conectado, y el proyecto incorpora mitigaciones explícitas para cada uno de ellos (cifrado, autenticación, marco regulatorio ANMAT, Ley 25.326, función coadyuvante al juicio médico). No se trata de aspectos éticamente controvertidos que admitan posturas razonadas en conflicto, sino de buenas prácticas técnicas y regulatorias estándar en la industria de dispositivos médicos.

El proyecto no involucra: experimentación en humanos fuera de un marco clínico controlado, uso dual con aplicaciones militares o de vigilancia, manipulación de información que pudiera afectar la autonomía del paciente, ni decisiones automatizadas con impacto clínico sin intervención profesional. Por estas razones, se considera que el presente Informe de Valor Ético y Social es suficiente y no corresponde elaborar un Informe de Discernimiento Ético adicional.

---

## 4. Conclusión

El proyecto Holter Wearable ECG es compatible con el Ideario de la Universidad Austral en tanto contribuye al bien común mediante el desarrollo de tecnología que mejora la salud y la calidad de vida de las personas, promueve el acceso equitativo a herramientas de diagnóstico, respeta la dignidad del paciente y se desarrolla bajo estándares de integridad académica y profesional. La autoevaluación realizada evidencia que el trabajo no presenta aspectos éticos cuestionables y que el equipo ha considerado de manera explícita el impacto social y las implicancias éticas de su actividad profesional.

---

**Firma de los alumnos:**

Tomás Serra — _________________________

Martín Barreiro — _________________________

**Visto bueno del director:**

Federico Ruiz — _________________________
