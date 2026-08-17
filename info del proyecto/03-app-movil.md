# App Móvil — No forma parte del canal de datos

La arquitectura elegida es **WiFi standalone**. El dispositivo envía datos directamente al backend vía la red del domicilio cada hora, sin depender de un smartphone como puente. Ver [justificación de la decisión de arquitectura](01-justificacion.md).

## Configuración inicial sin app

El único momento en que interviene un celular es la configuración de la red del domicilio, y se resuelve **desde el navegador**: el dispositivo emite un access point temporal y sirve un portal cautivo con el formulario. No hay nada que instalar, ni store, ni pairing. Ver [Canal WiFi y provisioning](07-wifi-y-provisioning.md#provisioning-inicial--softap--portal-cautivo).

Esto es lo que permite sostener la decisión de no requerir app aun habiendo un paso de configuración a cargo del paciente.

## BLE

El MCU de la familia ESP32 tiene BLE integrado, pero **esta arquitectura no lo usa**. Todos los roles que podría cumplir están cubiertos por el canal WiFi y el portal cautivo:

| Rol | Cómo se resuelve |
|---|---|
| Configuración de red | Portal cautivo (SoftAP) |
| Vinculación dispositivo ↔ paciente | Portal médico + orden al dispositivo en el ciclo horario |
| Telemetría (batería, SD, RSSI) | Campos del payload de cada batch |
| Verificación de colocación de electrodos | Puede servirse como preview en vivo desde el mismo `esp_http_server` del portal, vía WebSocket a un canvas en la página |

Mantener un solo stack de radio en el firmware reduce consumo de RAM, superficie de bugs y trabajo de testing.

**BLE queda como vía de evolución** si en el futuro se quiere una app compañera con telemetría en segundo plano, sin que el paciente tenga que desconectarse de su WiFi para hablarle al chaleco.

## Alcance de una eventual app de paciente

> **Pendiente de definición de scope.** Este documento describe la arquitectura de comunicación, donde la app no interviene. Sin embargo, el [Módulo 5 de Requerimientos](../Requerimientos.md) sí describe una app de paciente con fines distintos al transporte de datos: diario de eventos y síntomas, asistente de colocación y cuidado del chaleco, notificaciones preventivas y monitor de confianza del equipo. El propio documento aclara la intención: *"aprovechar la app mobile para el seguimiento del paciente en vez de usarlo como puente entre el chaleco y el servidor"*.
>
> Esa app —si se desarrolla— hablaría con el backend, nunca con el dispositivo, y por lo tanto no afecta la arquitectura de comunicación descrita acá. El modelo de datos ya lo contempla con el campo `patient.user_id`. Definir si entra en el scope del TFG es una decisión de producto, no de arquitectura.

El acceso a los datos clínicos se realiza a través del **dashboard médico web** (React).
