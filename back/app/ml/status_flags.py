"""Bits de diagnóstico del paquete de STATUS del equipo (`INTEGRACION.md` §3.1).

Vive al lado de `frame_header.py` por el mismo motivo que aquél: son constantes
del contrato con el firmware, no lógica nuestra, y tienen que poder cruzarse
contra `include/HolterProtocol.h` del repo del equipo sin buscarlas en tres
archivos. Igual que `frame_header`, **no importa numpy**: las lee la ruta del
ACK, que es la que no puede pagar ese import en un arranque en frío.

El equipo manda un STATUS por segundo, pero el POST ocurre cada varios minutos.
El puente acumula los flags **con OR** desde el último POST confirmado y los
manda en cuatro cabeceras (§11.1). Consecuencia que atraviesa todo este módulo:
lo que llega es *«qué pasó desde el último POST que nos entregaron»*, no *«qué
está pasando ahora»*, y **un mismo evento puede llegar en dos POST seguidos**.
Por eso todo lo que se derive de acá tiene que ser idempotente: dos
`BACKLOG_OVERFLOW` consecutivos son UN overflow, no dos.
"""

from __future__ import annotations

import enum

# --------------------------------------------------------------------------- #
# `statusFlags` — el byte que importa
# --------------------------------------------------------------------------- #

#: Se pisó backlog sin confirmar. **Evento.** Es la causa confirmada por el
#: equipo de un hueco de `seq`: se perdió señal porque la desconexión duró más
#: que el buffer de la flash.
STATUS_FLAG_BACKLOG_OVERFLOW = 0x01
#: Timeout de SPI escribiendo la flash. **Evento.** Falla intermitente de hardware.
STATUS_FLAG_FLASH_WRITE_TIMEOUT = 0x02
#: La flash nunca inicializó. **Estado.** El equipo no está grabando NADA.
STATUS_FLAG_FLASH_NOT_READY = 0x04
#: Se descartó una trama por CRC inválido. **Evento.** Hueco real en el registro.
STATUS_FLAG_CORRUPT_FRAME = 0x08
#: Los coeficientes del filtro no pasaron la verificación. **Estado.** No filtra
#: ni detecta QRS.
STATUS_FLAG_FILTER_BAD = 0x10
#: Hubo que re-inicializar el ADC. **Evento.** El tramo queda comprometido.
STATUS_FLAG_ADC_REINIT = 0x20
#: El front-end nunca inicializó. **Estado.** El equipo no adquiere nada.
STATUS_FLAG_AFE_NOT_READY = 0x40
#: El equipo graba pero lo grabado no sale. **Estado.** Se limpia solo cuando un
#: lote sale de verdad — o sea que si lo estamos leyendo, ya se resolvió.
STATUS_FLAG_UPLINK_DOWN = 0x80

# --------------------------------------------------------------------------- #
# `sampleLossFlags` — pérdida ANTES de la flash, irrecuperable
# --------------------------------------------------------------------------- #

#: La cola interna se llenó. Sostenido = el equipo no da abasto.
STATUS_LOSS_QUEUE_FULL = 0x01
#: Se perdió una conversión del ADC. Sostenido = problema de firmware o carga.
STATUS_LOSS_ADC_DROP = 0x02
#: Trama SPI del ADC con sincronismo inválido. Sostenido = bus degradado.
STATUS_LOSS_SPI_DESYNC = 0x04

# --------------------------------------------------------------------------- #
# `leadOffFlags`
# --------------------------------------------------------------------------- #

#: Electrodo RA despegado, según el comparador del ADS1292R.
#:
#: **NO USAR.** Biomédica midió que ese comparador no funciona en esta placa:
#: con el conector de electrodos entero desconectado el chip sigue informando
#: que están bien puestos, y en 897 s con un despegue deliberado de RA estos dos
#: bits estuvieron en 0 el 100 % del tiempo. Cuando sí disparan es con el
#: electrodo de tierra —cuya pérdida no invalida la señal— e informan como si se
#: hubiera soltado RA, o sea mandarían a recolocar el equivocado. Se dejan
#: definidos para que quede escrito por qué no se leen.
STATUS_LEAD_RA_OFF = 0x01
#: Electrodo LL despegado. Misma advertencia que `STATUS_LEAD_RA_OFF`.
STATUS_LEAD_LL_OFF = 0x02
#: Electrodo RLD/tierra despegado. No invalida la señal: degrada el rechazo de
#: modo común, pero el par RA-LL sigue midiendo una diferencia real.
STATUS_LEAD_RLD_OFF = 0x04
#: Hubo saturación del ADC. La marca por muestra dentro de la trama es más
#: precisa que este bit.
STATUS_LEAD_ADC_SATURATED = 0x08
#: Electrodo del canal 2 (precordial) despegado. El estudio sigue con la
#: derivación de miembros.
STATUS_LEAD_CH2_OFF = 0x10
#: **El único aviso real de electrodo suelto que tiene este equipo.** Lo levanta
#: un detector sobre la señal cruda (entrada flotando → ruido de banda alta).
#:
#: OJO con la semántica: en el paquete de STATUS es el veredicto vigente, pero
#: **en la cabecera acumulada significa «estuvo suelto en algún momento desde el
#: último POST confirmado»**, que puede ser un tramo de horas. No son
#: equivalentes: puede estar en 1 con el electrodo ya recolocado. Dice "la
#: entrada está flotando", no cuál electrodo se soltó, y no invalida la señal.
STATUS_LEAD_SIGNAL_SUSPECT = 0x20


class DeviceFaultKind(enum.StrEnum):
    """Los estados que sacan el equipo de servicio.

    Son **estados** y no eventos: el equipo los repite en cada STATUS mientras
    la condición esté, así que la alerta que salga de acá necesita debounce o
    inunda al médico. La causa es de hardware o servicio técnico, nunca del
    paciente: el mensaje tiene que decir "el equipo tiene una falla, no lo use",
    no "revise los electrodos".
    """

    FLASH_NOT_READY = "flash_not_ready"
    FILTER_BAD = "filter_bad"
    AFE_NOT_READY = "afe_not_ready"


#: Los tres estados graves, con el bit que los levanta y qué decirle al operador.
DEVICE_FAULTS: tuple[tuple[int, DeviceFaultKind, str], ...] = (
    (
        STATUS_FLAG_AFE_NOT_READY,
        DeviceFaultKind.AFE_NOT_READY,
        "El equipo no consiguió inicializar el front-end de adquisición: no está "
        "midiendo nada. Es una falla del equipo, no de la colocación. Sacarlo de servicio.",
    ),
    (
        STATUS_FLAG_FLASH_NOT_READY,
        DeviceFaultKind.FLASH_NOT_READY,
        "La memoria del equipo nunca inicializó: está adquiriendo pero no graba nada. "
        "Sacarlo de servicio.",
    ),
    (
        STATUS_FLAG_FILTER_BAD,
        DeviceFaultKind.FILTER_BAD,
        "Los coeficientes del filtro del equipo no pasaron la verificación: no filtra "
        "ni detecta QRS. Sacarlo de servicio.",
    ),
)


def device_faults(status_flags: int | None) -> list[tuple[DeviceFaultKind, str]]:
    """Los estados graves presentes en el byte, del más grave al menos.

    El orden es el de `DEVICE_FAULTS` y no es casual: el bit 6 (`AFE_NOT_READY`)
    hay que leerlo antes que cualquier otro, porque sin adquisición no hay nada
    que grabar y los otros dos son consecuencias posibles de lo mismo.
    """
    if not status_flags:
        return []
    return [(kind, message) for bit, kind, message in DEVICE_FAULTS if status_flags & bit]


def has_backlog_overflow(status_flags: int | None) -> bool:
    """¿El equipo confirma que pisó backlog sin confirmar?

    Es lo que convierte un hueco de `seq` inferido en uno con causa confirmada.
    """
    return bool(status_flags and status_flags & STATUS_FLAG_BACKLOG_OVERFLOW)


def has_corrupt_frame(status_flags: int | None) -> bool:
    """¿Se descartó alguna trama por CRC desde el último POST confirmado?"""
    return bool(status_flags and status_flags & STATUS_FLAG_CORRUPT_FRAME)
