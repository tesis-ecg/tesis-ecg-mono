# Justificación: WiFi del domicilio como canal principal

## Opciones evaluadas

- **Opción A (elegida):** **WiFi del domicilio del paciente** como único canal, standalone — SD buffer + envío batch periódico directo al backend, configuración por SoftAP + portal cautivo, sin app móvil
- **Opción B (descartada):** módulo celular SIM (LTE-M, SIM7080G como candidato) como único canal — fue la decisión original del proyecto, ver [documento archivado](08-sim-celular-descartado.md)
- **Opción C (descartada):** BLE como canal principal + app móvil como puente al backend

## Comparación

| Criterio | A: WiFi del domicilio | B: SIM celular (LTE-M) | C: BLE + app requerida |
|---|---|---|---|
| **Independencia del paciente** | Alta — un paso de configuración inicial, después funciona solo | Total — no requiere ninguna acción | Baja — depende del celular y la app activa |
| **Cobertura** | Solo dentro del domicilio | Casi total (red celular) | Solo con el celular cerca |
| **Latencia de datos al cloud** | ~1h estando en casa | ~1h siempre | ~2 seg |
| **UX para 40-70 años** | Un formulario web una vez, asistible por el técnico en la entrega | Ninguna configuración | Requiere instalar app y mantenerla activa |
| **Costo operativo** | **$0** | Plan de datos IoT (~$3-5/mes por equipo) | $0 (usa datos del paciente) |
| **Costo y complejidad de hardware** | **Ninguno — la radio viene en el MCU** | Módulo SIM + slot + antena LTE + diseño RF en PCB | Ninguno |
| **Complejidad firmware** | Media (stack WiFi + HTTP server para provisioning) | Media-Alta (UART, comandos AT, máquina de estados del módem) | Alta (GATT + sync bulk + manejo de background iOS) |
| **Complejidad total del sistema** | Baja — firmware + cloud + dashboard | Baja — firmware + cloud + dashboard | Alta — suma app iOS/Android |
| **Autonomía de batería** | Buena — burst de ~15-20 seg/hora | Buena — burst de ~30 seg/hora con PSM | Buena — BLE ~10-15 mA continuo |
| **Riesgo de pérdida de datos** | Bajo — SD cubre la ventana fuera de casa | Bajo — SD cubre cortes de señal | Bajo — SD acumula, sync al reconectar |
| **Dependencia de terceros** | Router y proveedor de internet del paciente | Cobertura y saldo del operador celular | Sistema operativo del teléfono |

## Factores decisivos para la Opción A

1. **El hardware ya la incluye, sin costo ni área de PCB.** El MCU elegido (familia ESP32) trae WiFi y BLE integrados. La Opción B exige agregar módulo SIM, portasim, antena LTE y las consideraciones de diseño RF asociadas — área, costo unitario y trabajo del equipo de Biomédica que la Opción A no consume.

2. **Costo operativo cero.** Sin plan de datos IoT, sin gestión de SIMs, sin saldo que se agote a mitad de un estudio. Para un trial clínico con múltiples equipos en paralelo, eliminar el costo recurrente por dispositivo simplifica tanto el presupuesto como la logística administrativa.

3. **El caso de uso es domiciliario.** El Holter mide de forma continua durante días o semanas, y el paciente pasa la mayor parte de ese tiempo —incluidas las noches, que es cuando se captura la bioimpedancia según [Requerimientos](../Requerimientos.md)— en su casa. La ventana de transmisión coincide con la ventana de permanencia.

4. **La SD absorbe las salidas del domicilio.** La grabación nunca se interrumpe: sale del domicilio, la SD acumula; vuelve, se drena en el siguiente ciclo horario. Con la SD correctamente dimensionada (ver [Batería y datos](05-bateria-y-datos.md)) la ventana cubierta pasa de horas a meses, con lo cual "el paciente se fue el fin de semana" deja de ser un escenario de riesgo.

5. **El provisioning no requiere app.** SoftAP + portal cautivo funciona desde cualquier celular, tablet o notebook con navegador — sin instalar nada, sin store, sin pairing. Se resuelve en la entrega, con el técnico al lado. Esto es lo que permite mantener la decisión de **no desarrollar app móvil** aun habiendo un paso de configuración.

6. **La latencia de 1 hora ya estaba aceptada.** El diseño original de la Opción B tenía la misma latencia. No hay regresión en este eje: el sistema nunca fue de monitoreo en tiempo real, y el análisis se hace sobre el historial.

## Desventajas aceptadas

- **Sin cobertura fuera del domicilio.** Es la diferencia real contra la Opción B y hay que enunciarla sin adornos: mientras el paciente está fuera de casa, el backend no recibe datos nuevos. Se acepta porque (a) la grabación continúa intacta en SD y no se pierde ni un segundo de señal, (b) el uso clínico es preventivo y no de urgencia, con latencia de 1 h ya asumida, y (c) la SD dimensionada cubre ausencias de semanas.
- **Tener WiFi pasa a ser criterio de inclusión del trial.** Los pacientes sin conexión en el domicilio no pueden participar bajo esta arquitectura. Es una restricción de reclutamiento a documentar en el protocolo del ensayo.
- **Un paso de configuración a cargo del paciente.** Mitigado por el portal cautivo (sin app) y porque se hace en la entrega, asistido. Se agrega el riesgo de que el paciente cambie de router o de contraseña durante el estudio, lo cual requiere repetir el provisioning.
- **Dependencia del router del paciente.** Un corte de internet domiciliario detiene la transmisión (no la grabación). El dashboard lo detecta como ausencia de sincronización y lo eleva al equipo de soporte vía el watchdog.
- **Solo 2.4 GHz.** La familia ESP32 no asocia a redes de 5 GHz — a documentar en el instructivo de soporte.

## Opción B — Por qué se descartó

Fue la arquitectura original del proyecto y está documentada en detalle en [08-sim-celular-descartado.md](08-sim-celular-descartado.md). Sigue siendo, técnicamente, la opción con mejor cobertura, y su ventaja sobre la Opción A es real y no discutida: transmite desde cualquier lado.

Se descartó cuando el diseño de hardware dejó de incluir el módulo SIM. Con el MCU de la familia ESP32, la radio WiFi ya está en el chip, mientras que el canal celular exige sumar módulo, antena, portasim, diseño RF y un plan de datos por equipo. Ese costo —en dinero, en área de PCB y en trabajo del equipo de Biomédica— no se justifica frente a un beneficio, la cobertura fuera del domicilio, que el caso de uso domiciliario no necesita y que la SD cubre.

**Queda como vía de evolución natural**: si en algún momento el producto requiere monitoreo ambulatorio real fuera del hogar, el canal celular es la respuesta y el documento archivado conserva el análisis completo.

## Opción C — Por qué se descartó

BLE + app como canal primario presentaba dos problemas críticos:

1. **Dependencia del comportamiento del paciente**: si deja el celular en otra habitación, cierra la app, se le agota la batería del teléfono o iOS suspende la app en background, los datos no llegan al backend. Para monitoreo médico confiable a largo plazo esa variabilidad es inaceptable.

2. **Complejidad total desproporcionada para el scope del TFG**: firmware BLE + app iOS/Android + cloud + dashboard son cuatro componentes con bugs y testing independientes, contra tres en las opciones A y B.

Notar que la Opción A **no hereda** estos problemas: el chaleco habla directo con el backend por el router del domicilio, sin teléfono en el medio. El único punto de contacto con un celular es el formulario de configuración inicial, que se hace una vez y desde el navegador.

BLE queda disponible en el chip pero **sin uso en esta arquitectura**. Ver [App móvil](03-app-movil.md).
