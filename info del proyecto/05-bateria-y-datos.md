# Batería, almacenamiento y tiempos de transferencia

## Consumo energético del sistema

| Componente | Consumo promedio | Notas |
|---|---|---|
| AFE (ej: ADS1292R) | ~0.3-1 mA | Siempre encendido |
| XIAO Nordic activo / light sleep | ~3-8 mA | Light sleep entre tareas, activo al escribir SD |
| Escritura SD (intermitente) | ~2 mA promedio | Buffer en RAM, escribe cada 4-8 seg |
| Módulo SIM TX (cuando activo) | ~60-80 mA | Solo durante bursts de envío (~30 seg/hora) |
| Módulo SIM PSM (deep sleep) | ~3 µA | Entre envíos (~59 min/hora) |
| **Total promedio** | **~6-12 mA** | SIM agrega solo ~0.6 mA promedio gracias al PSM |

## Recomendación de batería

| Batería | Autonomía estimada (a ~12 mA) | Tamaño típico | Recomendación |
|---|---|---|---|
| 300 mAh | ~25 h (1 día) | 30x20x4 mm | Mínimo viable |
| **500 mAh** | **~42 h (~2 días)** | **40x30x4 mm** | **Recomendada para MVP** |
| 800 mAh | ~67 h (~3 días) | 50x34x5 mm | Mejor balance tamaño/duración |
| 1000 mAh | ~83 h (~3.5 días) | 50x34x6 mm | Si el chaleco lo permite |

### Autonomía en modo SIM

El módulo SIM (LTE-M, SIM7080G como candidato) agrega ~0.6 mA promedio al consumo (24 envíos/día × 30 seg × 70 mA). Impacto < 5%.

| Batería | Autonomía sin SIM (~12 mA) | Autonomía con SIM (~12.6 mA) | Diferencia |
|---|---|---|---|
| 500 mAh | ~42 h (~1.7 días) | ~40 h (~1.7 días) | -2 h |
| 800 mAh | ~67 h (~2.8 días) | ~63 h (~2.6 días) | -4 h |
| 1000 mAh | ~83 h (~3.5 días) | ~79 h (~3.3 días) | -4 h |

**Recomendación: 500-800 mAh LiPo.** Es lo que usan la mayoría de wearables médicos de pecho. Entra en un módulo de ~5x4 cm acoplable al chaleco.

### Optimización principal: light sleep

Lo que más impacta la autonomía es implementar **light sleep** del XIAO Nordic entre ciclos de escritura a SD y durante los ~59 min que el SIM duerme en PSM. Esto baja el consumo promedio del MCU significativamente respecto al modo activo continuo.

---

## Volúmenes de datos

### Datos generados por el ECG

- Muestreo: 250 Hz, 16 bits (2 bytes por muestra), 3 canales
- Raw: ~1.500 bytes/seg
- Con compresión (delta encoding, ~50%): ~750 bytes/seg

| Período | Raw (sin compresión) | Comprimido (~50%) |
|---|---|---|
| 1 hora | ~5.4 MB | ~2.7 MB |
| 1 día | ~129 MB | ~65 MB |
| 1 semana | ~903 MB | ~455 MB |
| 1 mes | ~3.9 GB | ~1.95 GB |

### Almacenamiento en SD — ciclo normal vs. acumulación

En operación normal (señal celular disponible) la SD retiene solo el batch de la hora en curso (~2.7 MB), que se borra tras la confirmación del backend. La SD actúa como buffer de seguridad en caso de pérdida de señal:

| Escenario | SD ocupada |
|---|---|
| Operación normal (envío cada 1h) | ~2.7 MB (solo la hora en curso) |
| 24h sin señal celular | ~65 MB |
| Capacidad máxima (128 MB) | ~2 días de acumulación sin enviar (~47 horas) |

La microSD de **128 MB** cubre el caso de uso esperado (cobertura LTE-M en zonas urbanas y semiurbanas de Argentina, donde las pérdidas de señal típicamente son de minutos u horas, no días). En caso de requerirse mayor margen (uso en zonas rurales prolongado), conviene evaluar SD de mayor capacidad. Al llegar al 90% se activa la rotación FIFO de los archivos más antiguos no enviados.

---

## Tiempos de transferencia

### SIM (LTE-M): Holter → Cloud

Los datos comprimidos de la SD se envían automáticamente cada hora y se borran tras confirmación:

| Dato | Valor |
|---|---|
| Datos por batch (1 hora comprimido) | ~2.7 MB |
| Tiempo de TX por batch | ~30-45 seg (LTE-M a ~500 KB/s) |
| Datos por día (con overhead HTTP/TLS ~20%) | ~78 MB |
| Datos por mes | ~2.4 GB |
| SD ocupada en operación normal | ~2.7 MB (solo la hora en curso) |
| SD ocupada si 24h sin señal | ~65 MB |

**Plan de datos recomendado**: mínimo 3 GB/mes. 1NCE (500 MB única vez) no es suficiente para uso continuo de 3 canales — evaluar planes IoT de operadores locales (~3-5 GB/mes).
