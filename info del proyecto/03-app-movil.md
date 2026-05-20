# App Móvil — No aplica en la arquitectura actual

La arquitectura elegida es **SIM standalone sin BLE**. El dispositivo envía datos directamente al backend vía LTE-M cada hora, sin depender de smartphone ni app.

No se desarrolla app móvil para este proyecto. El acceso a los datos clínicos se realiza exclusivamente a través del **dashboard médico web** (React/Next.js).

Ver [justificación de la decisión de arquitectura](01-justificacion.md).
