import uuid
from dataclasses import dataclass
from datetime import datetime

from app.modules._base_schema import CamelModel


class IngestAckOut(CamelModel):
    """El ACK del protocolo, no un simple "recibido".

    `framesAccepted` es un **delta contiguo**: cuántas tramas nuevas puede dar
    por entregadas el equipo, contadas desde la más vieja sin confirmar y
    cortando en el primer hueco de `seq` (`INTEGRACION.md` §4.6). Si llegan la
    10, la 11 y la 13, se confirman 2 — la 12 falta y el cursor de lectura del
    equipo no la puede saltear.
    """

    framesReceived: int
    framesAccepted: int
    framesRejected: int
    framesDuplicate: int
    lastAcceptedSeq: int | None
    batchId: uuid.UUID | None
    studyId: uuid.UUID
    serverTime: datetime


@dataclass(frozen=True)
class IngestFramesInput:
    payload: bytes
    received_at: datetime
