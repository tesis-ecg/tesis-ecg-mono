# Escenarios críticos, seguridad y regulatorio

## Escenarios de operación

| Escenario | Comportamiento |
|---|---|
| **Operación normal** | SD graba continuamente → cada 1h SIM envía batch → backend confirma → SD limpia datos enviados |
| Sin señal celular (temporal) | SD sigue acumulando. Al recuperar señal, envía batches pendientes (más antiguos primero) |
| Fallo de envío SIM | Datos permanecen en SD. Reintento en próximo ciclo. Backoff exponencial tras 3 fallos (1h→2h→4h) |
| SIM sin datos/saldo | Igual que sin señal: SD acumula. 128 MB ≈ ~2 días de margen |
| SD llena (90%) | Rotación FIFO de archivos más antiguos no enviados. Prioriza envío inmediato si hay señal |
| Batería baja Holter | Campo `battery_pct` en próximo batch SIM → alerta en dashboard médico |
| Anomalía detectada | Backend detecta en el batch y notifica al médico vía dashboard. Delay máximo de 1h (aceptable para monitoreo preventivo) |

### Principio de diseño

La **SD card es el buffer primario y el seguro final**. Sin importar el estado de la señal celular, el Holter siempre graba en SD. Con 128 MB hay margen para ~2 días de datos sin enviar — adecuado para los escenarios de pérdida de señal urbanos/semiurbanos. Nunca se pierden datos dentro de esa ventana.

---

## Seguridad por capa

### SIM/Celular (Holter → Cloud)
- TLS 1.2 obligatorio (módulos LTE-M como el SIM7080G soportan TLS nativo)
- Certificado del servidor validado contra CA root en el módulo
- API key única por dispositivo en header `X-API-Key` (grabada en flash del XIAO Nordic durante provisioning)
- Sin flujo JWT/login — autenticación stateless por request

### Datos en tránsito (Dashboard médico ↔ Cloud)
- TLS 1.3 para todas las comunicaciones HTTPS

### Datos en reposo
- **Cloud**: AES-256 en PostgreSQL y S3
- **SD card**: Datos sin encriptar (tradeoff de rendimiento). El dispositivo es físicamente del paciente. Si ANMAT lo requiere, se puede agregar encriptación AES por bloque

---

## Cumplimiento regulatorio

| Regulación | Alcance | Estado |
|---|---|---|
| **Ley 25.326** (Argentina) | Protección de Datos Personales | Requerido |
| **ANMAT** | Producto médico clase II — documentar protocolo de comunicación SIM y flujo de datos como parte del expediente técnico | Requerido |
| **HIPAA** | Estándar de salud de EE.UU. | Considerar para expansión futura |

---

## Verificación y testing

1. **Ciclo SIM completo**: SD graba → SIM envía → backend confirma → SD limpia. Probar con señal intermitente — datos deben acumularse y enviarse al recuperar señal
2. **Backoff exponencial**: Simular 3 fallos consecutivos de envío, verificar que el intervalo crece (1h→2h→4h)
3. **Acumulación en SD**: Desactivar SIM por X horas, verificar que al recuperar señal se envían todos los batches pendientes en orden
4. **Cloud**: Test de carga con datos simulados de múltiples dispositivos simultáneos
5. **End-to-end**: Señal de generador → AFE → XIAO Nordic → SD → SIM → Cloud → Dashboard médico
