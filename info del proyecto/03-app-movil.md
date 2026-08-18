# App Móvil — No forma parte del canal de datos

La arquitectura elegida es **WiFi standalone**. El dispositivo envía datos directamente al backend vía la red del domicilio cada hora, sin depender de un smartphone como puente. Ver [justificación de la decisión de arquitectura](01-justificacion.md) y las cuentas en [la comparativa de canales](09-comparativa-canales-de-transmision.md).

## Configuración inicial sin app

El único momento en que interviene un celular es la configuración de la red del domicilio, y se resuelve **desde el navegador**: el dispositivo emite un access point temporal y sirve un portal cautivo con el formulario. No hay nada que instalar, ni store, ni pairing. Ver [Canal WiFi y provisioning](07-wifi-y-provisioning.md#provisioning-inicial--softap--portal-cautivo).

Esto es lo que permite sostener la decisión de no requerir app aun habiendo un paso de configuración a cargo del paciente.

## BLE

El nRF52840 tiene BLE, y el firmware del equipo de Biomédica **ya lo tiene implementado y validado**: un servicio con cuatro características (LIVE / BACKLOG / CONTROL / STATUS), emparejamiento cifrado y autenticado con passkey de 6 dígitos distinto por equipo, y drenaje de backlog confirmado trama a trama por ACK.

Lo que esta arquitectura decide es **no usarlo como camino de datos**, que es distinto de no usarlo. Un canal de datos que depende de que el paciente tenga el celular cerca y la app viva todos los días es demasiado frágil para un registro clínico (ver [la comparativa de canales](09-comparativa-canales-de-transmision.md), opción A).

Los roles principales quedan cubiertos por el canal WiFi:

| Rol | Cómo se resuelve |
|---|---|
| Transporte de datos al backend | **WiFi** — es el camino crítico y no depende de ningún teléfono |
| Vinculación dispositivo ↔ paciente | Portal médico + orden al dispositivo en el ciclo horario |
| Telemetría (batería, buffer, RSSI) | Campos del payload de cada batch |

Y quedan dos roles donde BLE **sí es la mejor herramienta**, y donde conviene usarlo justamente porque ya está hecho:

| Rol | Por qué BLE |
|---|---|
| Verificación de colocación de electrodos | El canal LIVE ya emite la señal en tiempo real. Ver la onda mientras se acomoda el chaleco es más directo que levantar un AP y abrir un portal |
| Configuración inicial del equipo | Emparejar por BLE desde una app es más simple para el paciente que conectarse a `Holter-XXXX` y esperar que se abra el portal cautivo — un flujo que funciona distinto en iOS y en Android |

**La diferencia clave con usar BLE como canal de datos**: si el paciente no abre la app, no se pierde ni un dato. El chaleco sigue subiendo por WiFi. La app suma, no sostiene.

## Alcance de una eventual app de paciente

> **Pendiente de definición de scope.** Este documento describe la arquitectura de comunicación, donde la app no interviene. Sin embargo, el [Módulo 5 de Requerimientos](../Requerimientos.md) sí describe una app de paciente con fines distintos al transporte de datos: diario de eventos y síntomas, asistente de colocación y cuidado del chaleco, notificaciones preventivas y monitor de confianza del equipo. El propio documento aclara la intención: *"aprovechar la app mobile para el seguimiento del paciente en vez de usarlo como puente entre el chaleco y el servidor"*.
>
> Esa app —si se desarrolla— hablaría con el backend, nunca con el dispositivo, y por lo tanto no afecta la arquitectura de comunicación descrita acá. El modelo de datos ya lo contempla con el campo `patient.user_id`. Definir si entra en el scope del TFG es una decisión de producto, no de arquitectura.

El acceso a los datos clínicos se realiza a través del **dashboard médico web** (React).
