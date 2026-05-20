# Justificación: SIM (LTE-M) como canal principal

## Opciones evaluadas

- **Opción A (elegida):** módulo celular SIM (LTE-M, SIM7080G como candidato técnico) como único canal de comunicación, standalone — SD buffer + envío batch periódico directo al backend, sin BLE ni app
- **Opción B (descartada):** BLE como canal principal + WiFi backup on-demand + app móvil requerida

## Comparación

| Criterio | Opción A: SIM standalone (sin BLE, sin app) | Opción B: BLE + WiFi backup + app requerida |
|---|---|---|
| **Independencia del paciente** | Total — no requiere smartphone ni intervención | Alta dependencia del celular y la app |
| **Cobertura de red** | LTE-M (red celular, cobertura casi total) | Solo cuando el celular está cerca + WiFi disponible |
| **Latencia de datos al cloud** | ~1h (batch periódico) | ~2 seg (real-time via app) |
| **UX para 40-70 años** | Mínima — el dispositivo funciona solo, sin configuración | Requiere mantener la app activa y el celular cerca |
| **Autonomía batería** | Excelente — SIM agrega <5% overhead gracias a PSM | Buena — BLE consume ~10-15 mA, WiFi ~120 mA en burst |
| **Complejidad firmware** | Media-Alta (UART, AT commands, máquina de estados SIM) | Alta (GATT + WiFi on-demand + sync bulk) |
| **Complejidad total del sistema** | Baja — solo firmware + cloud + dashboard | Alta — firmware + app iOS/Android + cloud + dashboard |
| **Costo operativo** | Plan de datos IoT (~$1-10/mes) | Sin costo celular directo (usa datos del teléfono del paciente) |
| **Alertas en tiempo real** | No — delay de hasta 1h para detectar anomalías | Sí — alertas inmediatas via app |
| **Riesgo de pérdida de datos** | Bajo — SD de 128 MB acumula hasta ~2 días sin señal | Bajo — SD acumula, sync al reconectar |
| **Escalabilidad / control** | Alta — backend siempre tiene datos recientes | Media — depende del comportamiento del paciente |

## Factores decisivos para Opción A

1. **Autonomía real del dispositivo**: El Holter funciona completamente solo. No depende de que el paciente tenga su celular cerca, la batería del teléfono cargada, ni la app abierta. El paciente solo carga el dispositivo y lo usa.

2. **Target de usuario**: Pacientes de 40-70 años con potencial baja adopción tecnológica. Eliminar la dependencia del smartphone reduce fricción, errores de uso y gaps de datos por olvido de llevar el celular.

3. **Escenarios clínicos**: El médico necesita datos continuos y confiables. Con SIM, el backend siempre recibe datos cada hora sin importar lo que haga el paciente. Con BLE+App, un paciente que deja el celular en casa puede generar brechas de horas o días.

4. **Monitoreo preventivo continuo**: El caso de uso es monitoreo a largo plazo (semanas/meses). La latencia de 1 hora es clínicamente aceptable para detección de arritmias —el análisis se hace sobre el historial, no en tiempo real.

5. **Consumo energético muy bajo del SIM en PSM**: Los módulos LTE-M (como el SIM7080G candidato) en Power Saving Mode consumen ~3 µA. Solo se activan ~30 seg por hora. El impacto en batería es menor al 5%.

6. **SD como buffer robusto**: Con 128 MB de SD, el dispositivo acumula hasta ~2 días de datos sin señal. Al recuperar cobertura LTE-M, los datos se envían automáticamente. Para usos prolongados sin cobertura, conviene evaluar SD de mayor capacidad (a definir según consumo real).

## Desventajas aceptadas

- **Sin alertas en tiempo real**: Una anomalía cardíaca no se detecta hasta el próximo batch (~1h). Para uso clínico preventivo continuo (no urgencias), esto es aceptable.
- **Costo operativo**: Requiere plan de datos IoT. Con 1NCE (500 MB / $10 USD única vez) alcanza para ~1 año. Para producción, plan IoT local (~$3-5/mes).
- **Complejidad de PCB**: El módulo SIM + SIM slot + antena LTE agregan área y consideraciones de diseño RF — esto corresponde al equipo de Biomédica.
- **Dependencia de cobertura LTE-M**: En zonas sin señal, los datos se acumulan en SD y se envían al recuperar cobertura. No hay pérdida de datos.

## Opción B — Por qué se descartó

BLE+App como canal primario presentaba dos problemas críticos:

1. **Dependencia del comportamiento del paciente**: si deja el celular en otra habitación, cierra la app, la batería del teléfono se agota o iOS suspende la app, los datos no llegan al backend. Para un monitoreo médico confiable a largo plazo esta variabilidad es inaceptable.

2. **Complejidad total del sistema desproporcionada para el scope del TFG**: mantener firmware BLE + app iOS/Android + cloud + dashboard implica cuatro componentes con bugs y testing independientes, mientras que la Opción A reduce el scope a firmware SIM + cloud + dashboard.

La Opción B tendría sentido si:
- La latencia de <2 seg fuera un requerimiento clínico estricto (ej: ICU, monitoreo de emergencias)
- Todos los pacientes fueran tech-savvy y llevarán el celular siempre cerca
- No hubiera opción de usar un módulo celular en el hardware
