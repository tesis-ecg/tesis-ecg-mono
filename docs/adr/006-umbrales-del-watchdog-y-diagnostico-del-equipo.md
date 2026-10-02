# ADR-006: Umbrales del watchdog y qué se lee del diagnóstico del equipo

## Estado

Aceptado (17 de septiembre de 2026).

## Contexto

El equipo de Ingeniería Biomédica reportó, con mediciones sobre la placa real
(`../Holter-ECG-System/INTEGRACION.md`), tres cosas que invalidaban supuestos
que el backend tenía cableados:

1. **La autonomía offline no son 9,94 h.** Ese número sale de una señal de
   PhysioNet. Sobre esta placa el ratio de compresión depende de cuánta
   interferencia de red entra, y eso depende de cómo quede puesto el chaleco:
   **5,1 h con el chaleco flojo**, 7,1 con gel, 8,6 bien puesto (§9.1).
2. **El puente WiFi manda cuatro cabeceras de diagnóstico** desde septiembre de
   2026 (§11.1) y el backend las descartaba enteras. Son el único canal por el
   que este equipo puede avisar que está perdiendo señal del paciente.
3. **Dos de esas señales no se pueden usar tal cual**: el comparador de lead-off
   del ADS1292R no funciona en esta placa, y la estimación de backlog del
   firmware va ~2× alta.

Hasta acá el sistema tenía **un solo umbral**, `dashboard_stale_hours = 10`, sin
comentario que lo ligara a nada. Coincidía casi exactamente con las 9,94 h, o sea
que el aviso llegaba **después** de que el log circular ya había empezado a pisar
señal sin subir.

## Decisión

### Dos umbrales, dimensionados contra las 5,1 h

- `device_stale_hours = 1` — **aviso**. El puente despacha un lote cada 10 min,
  así que una hora ya son seis ventanas perdidas: no dispara por ruido y avisa
  temprano.
- `device_critical_hours = 4` — **crítico**. Deja ~1 h de margen antes del corte
  medido de 5,1 h.

Se dimensiona contra el chaleco flojo y no contra el caso bueno porque **un
paciente con el chaleco flojo durante 15 días es el caso normal, no el extremo**.
Usar las 9,94 h era usar el doble del margen que existe.

No se agrega ningún job periódico: los dos umbrales se aplican en las consultas
que ya existen del dashboard y de la app del paciente. La consecuencia conocida
es que un equipo callado "aparece" cuando alguien abre el dashboard, no antes.
Cerrar eso pide un cron y una alerta persistente, y queda como trabajo separado.

### Qué se lee de las cuatro cabeceras, y qué no

| Cabecera | Qué se hace |
|---|---|
| `X-Device-Status-Flags` | Se persiste. Los bits 2, 4 y 6 (flash / filtros / AFE sin inicializar) generan una alerta `device_fault` **crítica**, con debounce. El bit 0 confirma la causa de un hueco de `seq`; el bit 3 genera un evento de trama descartada |
| `X-Device-Loss-Flags` | Se persiste. Es pérdida anterior a la flash, irrecuperable |
| `X-Device-Lead-Flags` | Se persiste. **No dispara nada** |
| `X-Device-Backlog-Seconds` | Se persiste. **No alerta** |

**Los bits 0 y 1 de lead-off no se leen.** Biomédica midió que el comparador del
ADS1292R no funciona en esta placa: con el conector de electrodos entero
desconectado el chip sigue informando que están bien puestos, y en 897 s con un
despegue deliberado de RA esos bits estuvieron en 0 el **100 %** del tiempo.
Cuando sí disparan es con el electrodo de tierra —cuya pérdida no invalida la
señal— e informándolo como si se hubiera soltado RA. Usarlos mandaría a recolocar
el electrodo equivocado.

El bit 5 (`SIGNAL_SUSPECT`) es el único aviso real que tiene el equipo, pero **en
la cabecera su semántica no es la del paquete de STATUS**: ahí significa "estuvo
suelto en algún momento desde el último POST confirmado", que puede ser un tramo
de horas. Se archiva como contexto del lote; no se usa para pedirle al paciente
que se recoloque nada, porque puede estar en 1 con el electrodo ya bien puesto.

**`X-Device-Backlog-Seconds` no alimenta ningún umbral.** El firmware estima los
segundos como `tramas × 280 muestras / 500 SPS`; las 280 son de PhysioNet y la
señal real de esta placa comprime a 141 con electrodo seco. Error medido: **+98 %**
con electrodo seco, +44 % con gel. Alertar con eso diría "20 minutos" cuando son
10. Biomédica explicó además por qué no lo van a "arreglar" bajando la constante:
un valor fijo no puede servir para los dos electrodos y el equipo no sabe con cuál
lo pusieron. El atraso exacto se deriva restando el `t0Ms` de las tramas contra
`X-Device-Uptime-Ms` del mismo POST, que están en el mismo dominio.

### Las fallas graves son del equipo, no del paciente

`device_fault` es `CRITICAL` y **no manda push al paciente**. No hay nada que
pueda hacer con eso, y decirle que su equipo está roto sin poder darle un
reemplazo es angustia sin acción. El mensaje dice "el equipo tiene una falla,
sacarlo de servicio", nunca "revise los electrodos": versiones anteriores del
firmware reportaban un diagnóstico inventado de electrodos cuando el problema era
el cableado SPI o el propio chip.

Debounce de `device_fault_debounce_minutes = 60`, porque los bits 2, 4 y 6 son
**estados**: el equipo los repite en cada lote mientras la condición esté.

## Consecuencias

- Un equipo desconectado se ve a la hora en vez de a las diez, con dos niveles de
  gravedad en vez de uno.
- Los eventos que llegan repetidos en dos POST seguidos se cuentan como uno solo;
  el documento del firmware lo pide explícitamente y el debounce lo implementa.
- Las cuatro columnas nuevas de `ecg_batch` son nullable: `NULL` significa
  "firmware anterior a septiembre de 2026", que es distinto de "todo en orden".
- Un byte de diagnóstico fuera de rango se **descarta**, no rechaza el lote: lo
  que se pierde descartando es una lectura; lo que se perdería rechazando son
  minutos de registro del paciente.
- Sigue faltando el aviso proactivo sin que nadie abra el dashboard.
