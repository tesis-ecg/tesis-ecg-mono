# Batería, almacenamiento y tiempos de transferencia

> **Cifras a validar en Fase 1.** El MCU concreto de la familia ESP32 todavía no está definido y las estimaciones de consumo varían de forma significativa entre modelos (C3 / C6 / S3) y según la agresividad del light sleep. Las tablas de abajo son órdenes de magnitud para dimensionar batería y SD, no resultados de medición.

## Consumo energético del sistema

| Componente | Consumo promedio estimado | Notas |
|---|---|---|
| AFE (ej: ADS1292R) | ~0.3-1 mA | Siempre encendido |
| MCU ESP32 activo / light sleep | ~8-20 mA | Light sleep entre tareas, activo al escribir SD. **Término dominante** |
| Escritura SD (intermitente) | ~2 mA promedio | Buffer en RAM, escribe cada 4-8 seg |
| Radio WiFi TX/RX (cuando activa) | ~100-140 mA | Solo durante el ciclo de envío (~15-20 seg/hora) |
| Radio WiFi apagada | ~0 | Entre envíos (~59 min/hora) |
| **Total promedio** | **~12-25 mA** | El WiFi agrega solo ~0.7 mA promedio |

### El costo del WiFi es la radio prendida, no el burst

El burst de transmisión resulta prácticamente neutro en consumo, y conviene explicitarlo porque es contraintuitivo:

| Canal | Corriente en TX | Duración por ciclo | Energía por ciclo | Promedio agregado |
|---|---|---|---|---|
| WiFi | ~120 mA | ~15-20 seg | ~0.67 mAh | **~0.67 mA** |
| SIM LTE-M (opción descartada) | ~70 mA | ~30 seg | ~0.58 mAh | ~0.58 mA |

El WiFi consume casi el doble de corriente pero transmite en la mitad del tiempo, así que la energía por batch es equivalente. **El cambio de canal no penaliza la batería.**

Lo que sí la penaliza es el cambio de MCU: la familia ESP32 tiene un consumo base más alto que el nRF52840 del diseño anterior (~8-20 mA contra ~3-8 mA en el estado de grabación continua, y deep sleep de ~10 µA contra ~1.5 µA). Como la grabación es always-on y el envío dura 20 segundos por hora, **el término dominante de la autonomía es el MCU grabando, no la radio transmitiendo**. Es el trade-off real de esta decisión de hardware y es donde hay que poner el esfuerzo de optimización.

## Recomendación de batería

| Batería | Autonomía estimada (a ~15 mA) | Tamaño típico | Recomendación |
|---|---|---|---|
| 500 mAh | ~33 h (~1.4 días) | 40x30x4 mm | Mínimo viable |
| **800 mAh** | **~53 h (~2.2 días)** | **50x34x5 mm** | **Recomendada para MVP** |
| 1000 mAh | ~67 h (~2.8 días) | 50x34x6 mm | Si el chaleco lo permite |
| 1200 mAh | ~80 h (~3.3 días) | 55x38x6 mm | Margen para consumo real peor al estimado |

**Recomendación: 800-1000 mAh LiPo.** Un escalón por encima de lo dimensionado para el diseño anterior, para absorber el mayor consumo base del ESP32. Entra en un módulo de ~5x4 cm acoplable al chaleco.

### Optimización principal: light sleep

Lo que más impacta la autonomía es implementar **light sleep del MCU entre ciclos de escritura a SD**. En la familia ESP32 la diferencia entre CPU activa continua (~25-40 mA) y light sleep con wake-ups periódicos (~130 µA de base más el tiempo activo) es de un orden de magnitud, y define si el equipo dura 1 día o 3.

Segunda optimización: mantener la radio WiFi apagada por completo fuera del ciclo de envío (no en modem sleep, apagada). El stack WiFi asociado y en power save sigue consumiendo del orden de mA por escuchar beacons — un costo permanente que esta arquitectura no necesita pagar, porque solo se transmite una vez por hora.

---

## Volúmenes de datos

> **Inconsistencia a resolver en Fase 1.** Esta sección usa la estimación de referencia de 250 Hz × 3 canales × 16 bits. Sin embargo, [Requerimientos.md](../Requerimientos.md) especifica que el ADSR exporta **72 bits cada 2 ms** (24 bits de estado de electrodos + 24 de ECG + 24 de impedancia), lo que equivale a 500 Hz y ~4.500 bytes/seg — aproximadamente **el triple** de lo estimado acá. Hay que cerrar este número con el equipo de Biomédica, porque afecta el dimensionamiento de la SD, los tiempos de transferencia y el consumo. Las tablas de abajo son el escenario optimista.

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

En operación normal (paciente en el domicilio, WiFi disponible) la SD retiene solo el batch de la hora en curso (~2.7 MB), que se borra tras la confirmación del backend. La SD actúa como buffer de seguridad mientras el paciente está fuera de alcance del router:

| Escenario | SD ocupada |
|---|---|
| Operación normal (envío cada 1h) | ~2.7 MB (solo la hora en curso) |
| Un día completo fuera del domicilio | ~65 MB |
| Fin de semana largo fuera | ~200 MB |
| Un mes sin conectar (patológico) | ~1.95 GB |

**Recomendación: microSD de 4-8 GB.** Es un cambio importante respecto de los 128 MB del diseño anterior, y la razón es que cambió la naturaleza del hueco de conectividad: con LTE-M los cortes eran de minutos u horas y 128 MB (~2 días) alcanzaban; con WiFi domiciliario el hueco es "el paciente no está en casa", que puede ser un fin de semana o unas vacaciones.

El costo de sobredimensionar es nulo —las microSD de 128 MB prácticamente no se fabrican y una de 8 GB cuesta lo mismo o menos— y el beneficio es que la pregunta "¿qué pasa si el paciente se va una semana?" deja de tener respuesta interesante: con 8 GB hay ~4 meses de margen, más que la duración de cualquier estudio del trial.

La rotación FIFO al 90% de capacidad se mantiene como red de seguridad, pero con este dimensionamiento no debería activarse nunca en uso normal.

---

## Tiempos de transferencia

### WiFi: Holter → Cloud

Los datos comprimidos de la SD se envían automáticamente cada hora y se borran tras confirmación:

| Dato | Valor |
|---|---|
| Datos por batch (1 hora comprimido) | ~2.7 MB |
| Asociación WiFi + DHCP + handshake TLS | ~3-5 seg |
| Tiempo de TX por batch | ~5-10 seg (throughput HTTPS real en ESP32: ~300 KB/s - 1 MB/s) |
| **Ciclo completo con radio encendida** | **~15-20 seg** |
| Datos por día (con overhead HTTP/TLS ~20%) | ~78 MB |
| Datos por mes | ~2.4 GB |
| SD ocupada en operación normal | ~2.7 MB (solo la hora en curso) |

### Impacto en la conexión del paciente

**Costo operativo del proyecto: $0** — no hay plan de datos IoT. El tráfico corre por la conexión domiciliaria del paciente: ~78 MB/día, ~2.4 GB/mes. Sobre una conexión de banda ancha típica es despreciable, y los bursts de 20 segundos por hora no compiten de forma perceptible con el uso normal del hogar.

Aun así, corresponde **informarlo explícitamente en el consentimiento del paciente**: el equipo usa su internet y su electricidad. Es transparencia básica y evita fricción si el paciente tiene una conexión medida o un plan con límite de datos.

### Recuperación tras una ausencia prolongada

Al volver el paciente al domicilio, el dispositivo envía los batches pendientes del más antiguo al más nuevo. Un fin de semana acumulado (~200 MB) se drena en unos pocos minutos de radio encendida, repartidos en ciclos sucesivos. Conviene que el firmware, al detectar backlog, encadene envíos en lugar de esperar una hora entre cada uno — pero con un tope de ciclos consecutivos para no vaciar la batería de golpe si el backlog es grande.
