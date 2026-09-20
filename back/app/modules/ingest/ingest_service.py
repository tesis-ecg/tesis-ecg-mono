"""Recepción de tramas del chaleco.

El request hace lo mínimo indispensable para poder confirmar: valida, archiva
los bytes crudos y crea el `ecg_batch`. La decodificación va después, en
background (`processing.py`) — decodificar 1,8 M de muestras de códigos de
longitud variable dentro del request dejaría al equipo esperando decenas de
segundos y haría timeout.

Ese corte no es solo una optimización: `INTEGRACION.md` §4.6 pide confirmar
**recién después de haber persistido de forma durable**, y separar "guardé tus
bytes" de "los procesé" es exactamente eso.
"""

import asyncio
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.s3 import put_object
from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.db.models.patient import Patient, PatientStudyStatus
from app.db.models.study import Study, StudyStatus
from app.db.models.study_timeline_segment import TimeSyncSource
from app.dependencies.device_dependencies import INGESTABLE_STATUSES, DeviceContext

# `frame_header` y no `decompression`: la ruta del ACK solo valida cabeceras y
# cuenta tramas, y ese módulo no arrastra numpy. Importarlo acá metía numpy en
# cada arranque en frío de la función para contestar un 202 que no lo usa.
from app.ml.frame_header import SAMPLE_RATE_HZ, FrameError, FrameInfo, iter_frames, read_header
from app.ml.status_flags import device_faults
from app.modules.ingest import ingest_repository as repo
from app.modules.ingest.ingest_schemas import (
    DeviceStatusAckOut,
    DeviceStatusInput,
    IngestAckOut,
    IngestFramesInput,
    VestStatusEvent,
)
from app.modules.patient_app.notifications_service import notify_patient_task, vest_message

logger = structlog.get_logger(__name__)


def frames_key(study_id: uuid.UUID, first_seq: int) -> str:
    """Clave estable por lote.

    Lleva el `first_seq` y no un uuid random a propósito: si el equipo
    retransmite el mismo lote, se reescribe el mismo objeto en vez de dejar
    basura huérfana en S3.
    """
    return f"studies/{study_id}/frames/{first_seq:012d}.bin"


@dataclass(frozen=True)
class _Anchor:
    """Ancla temporal de un lote, con de dónde salió y cuánto vale."""

    epoch_ms: int
    source: TimeSyncSource
    uncertainty_ms: int


@dataclass(frozen=True)
class _ParsedFrame:
    info: FrameInfo
    payload: bytes


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message}
    )


def _parse(payload: bytes) -> tuple[list[_ParsedFrame], int]:
    """Separa las tramas utilizables de las que hay que descartar enteras.

    Una trama que no valida no se "recupera parcialmente": serían datos
    inventados presentados como señal del paciente.
    """
    try:
        raw_frames = iter_frames(payload)
    except FrameError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "INGEST_BAD_BODY", "message": str(error)},
        ) from error

    parsed: list[_ParsedFrame] = []
    rejected = 0
    for raw in raw_frames:
        try:
            parsed.append(_ParsedFrame(info=read_header(raw), payload=raw))
        except FrameError:
            rejected += 1
    return parsed, rejected


def _dedupe_by_seq(frames: list[_ParsedFrame]) -> list[_ParsedFrame]:
    """Ordena por `seq` y colapsa repeticiones dentro del mismo cuerpo.

    Ordenar antes de evaluar contigüidad importa: el equipo puede retransmitir
    fuera de orden después de un corte, y una trama que llega desordenada no es
    un hueco.
    """
    by_seq: dict[int, _ParsedFrame] = {}
    for frame in frames:
        by_seq.setdefault(frame.info.seq, frame)
    return [by_seq[seq] for seq in sorted(by_seq)]


@dataclass(frozen=True)
class _AckWindow:
    already_stored: list[_ParsedFrame]
    accepted: list[_ParsedFrame]
    #: Tramas que faltan entre el cursor y la primera aceptada. Distinto de cero
    #: es pérdida real e irrecuperable de señal del paciente: el log circular del
    #: equipo dio la vuelta y esas tramas ya no existen.
    gap_frames: int = 0

    @property
    def total_ack(self) -> int:
        return len(self.already_stored) + len(self.accepted)


def _ack_window(frames: list[_ParsedFrame], study: Study) -> _AckWindow:
    """Ventana confirmable, con la semántica go-back-N de `INTEGRACION.md` §4.6.

    Tres reglas:

    1. **Solo contiguo desde el cursor.** Si llegan la 10, la 11 y la 13 con el
       cursor en 9, se confirman 2: la 12 falta y el cursor de lectura del
       equipo no la puede saltear.
    2. **Las ya almacenadas se vuelven a confirmar.** Acá hay una diferencia
       deliberada con el canal BLE del documento, donde un duplicado no se
       re-confirma. Sobre HTTP un lote duplicado significa que *nuestra
       respuesta se perdió*, no que el equipo esté adelantado: si contestáramos
       0, el equipo reintentaría el mismo lote para siempre. Se cuentan aparte
       en `framesDuplicate` para que el reintento siga siendo visible.
    3. **El cursor puede saltar hacia adelante, nunca hacia atrás.** Si la
       primera trama del lote está por encima del cursor, el tramo arranca ahí y
       no en `cursor + 1`, haya habido reinicio o no.

       Esto vale porque go-back-N garantiza que **la primera trama de un lote es
       la más vieja sin confirmar**: si el equipo la manda, es porque no tiene
       nada anterior pendiente. El hueco que queda atrás no lo puede llenar
       nadie, así que esperarlo es esperar para siempre.

       Las dos causas de ese salto son distintas y las dos terminan acá:

       - **Reinicio.** Lo que el equipo no alcanzó a mandar del boot anterior se
         perdió con él (§4.3: "dos tramas *consecutivas por seq* con bootId
         distinto").
       - **Overflow del log circular** (`INTEGRACION.md` §4.6, el defecto que
         este código tuvo hasta septiembre de 2026). Si el equipo pasa más horas
         sin enlace que las que aguanta su flash, el log da la vuelta, el cursor
         de lectura del equipo se adelanta y **el `bootId` no cambia**, porque no
         hubo reinicio: solo se pisó señal. La versión anterior toleraba el salto
         solo con `bootId` nuevo, así que con el mismo boot lo trataba como hueco,
         cortaba, y devolvía el cursor viejo — que el equipo no podía alcanzar.
         Resultado: `framesAccepted = 0` en todos los reintentos y el estudio
         dejaba de archivar hasta que alguien reiniciara el equipo, sin ningún
         síntoma visible. Lo reprodujo Biomédica en
         `test/tools/test_overflow_deadlock.py`.

       Un hueco de verdad —uno en MEDIO del lote— lo sigue cortando el bucle de
       abajo, sin cambios.

       Lo que sigue sin aceptarse es una `seq` **anterior** al cursor bajo otro
       `bootId`. Antes se aceptaba: el cursor se descartaba entero ante cualquier
       cambio de boot y el lote entraba desde su primera trama. Como los objetos
       del estudio se nombran con el `first_seq` del lote (`frames_key`,
       `segment_key`, `envelope_key`), eso **sobreescribía en S3** señal ya
       archivada mientras `samples_count` seguía creciendo: el estudio perdía
       muestras en silencio y quedaba contando las que ya no estaban. Ese caso lo
       ataja `_guard_seq_rewind`, antes de llegar acá.

    El salto se devuelve en `gap_frames` en vez de perderse: es pérdida real de
    señal del paciente y el médico tiene que verla (§9.1, "registro de cada
    overflow con su hora de pared").
    """
    cursor = study.last_ingested_seq

    if cursor is None:
        already: list[_ParsedFrame] = []
        fresh = frames
        expected = frames[0].info.seq if frames else 0
    else:
        already = [f for f in frames if f.info.seq <= cursor]
        fresh = [f for f in frames if f.info.seq > cursor]
        expected = fresh[0].info.seq if fresh else cursor + 1

    accepted: list[_ParsedFrame] = []
    for frame in fresh:
        if frame.info.seq != expected:
            break  # hueco: se corta acá y el resto espera a que el equipo lo llene
        accepted.append(frame)
        expected += 1

    # El hueco se mide contra el cursor, no contra el lote: es cuánta señal se
    # perdió, no cuánta se salteó este envío.
    gap_frames = 0
    if cursor is not None and accepted:
        gap_frames = max(accepted[0].info.seq - (cursor + 1), 0)

    return _AckWindow(already_stored=already, accepted=accepted, gap_frames=gap_frames)


async def _resolve_study(
    db: AsyncSession, ctx: DeviceContext, first: FrameInfo, epoch_anchor_ms: int
) -> tuple[Study, Patient]:
    """`serial → device.patient_id → estudio in_progress`, creándolo si no hay.

    Es el flujo real: el chaleco se enciende y empieza a grabar; nadie abre un
    estudio a mano antes.

    Devuelve también el paciente porque el caller le actualiza el estado de
    seguimiento: es la única parte del sistema que sabe que llegó señal.
    """
    device = ctx.device
    if device.patient_id is None:
        raise _conflict(
            "DEVICE_UNASSIGNED",
            "El dispositivo no tiene un paciente asignado.",
        )
    patient = await repo.get_active_patient(db, device.patient_id)
    if patient is None:
        raise _conflict("PATIENT_NOT_FOUND", "El paciente asignado no está activo.")

    study = await repo.get_open_study_for_update(db, patient.id, device.id)
    if study is not None:
        return study, patient

    started_at = datetime.fromtimestamp((epoch_anchor_ms + first.t0_ms) / 1000, tz=UTC)
    study = await repo.create_study(
        db,
        patient_id=patient.id,
        device_id=device.id,
        started_at=started_at,
        sample_rate=SAMPLE_RATE_HZ,
    )
    return study, patient


#: Kind de la alerta que avisa que el equipo rebobinó su numeración.
SEQ_REWIND_ALERT_KIND = "study_seq_rewind"


async def _is_seq_rewind(
    db: AsyncSession, study: Study, frames: list[_ParsedFrame], boot_id: int
) -> bool:
    """¿El equipo rebobinó su `seq` bajo un boot nuevo? (`INTEGRACION.md` §11.6).

    Los cursores del log viven en la metadata de la flash. Si cambia el formato
    de esa metadata —que es lo que pasa al actualizar el firmware— el equipo
    arranca `writeSeq_` en 0. Sus tramas caen entonces enteras por debajo de
    nuestro cursor, `_ack_window` las cuenta como ya almacenadas, y el ACK
    devuelve el cursor viejo: el equipo daría por entregado un lote que **no se
    archivó** y lo borraría de su flash. Pérdida permanente y silenciosa.

    Mirando solo los números ese caso es idéntico a una retransmisión legítima
    bajo otro `bootId`. Lo que los separa es si las tramas están archivadas.
    Hay que preguntar por el prefijo que cae debajo del cursor, no solo cuando
    el lote entero cae ahí: después de un reinicio un solo POST puede contener
    `0..cursor` y también tramas nuevas. Si se contara ese prefijo como
    duplicado, el ACK confirmaría y el equipo borraría señal nueva nunca
    archivada.
    """
    cursor = study.last_ingested_seq
    if cursor is None or study.last_boot_id is None or study.last_boot_id == boot_id:
        return False
    frames_at_or_before_cursor = [frame for frame in frames if frame.info.seq <= cursor]
    if not frames_at_or_before_cursor:
        return False
    # Retransmisión legítima: el prefijo ya está archivado, aunque haya llegado
    # repartido entre varios batches; se lo re-confirma y el equipo puede seguir
    # drenando. Si falta cualquier tramo del prefijo, es señal de un seq
    # rebobinado y el estudio anterior no puede absorberla.
    return not await repo.has_archived_seq_range(
        db,
        study.id,
        frames_at_or_before_cursor[0].info.seq,
        frames_at_or_before_cursor[-1].info.seq,
    )


async def _recover_from_seq_rewind(db: AsyncSession, study: Study, patient: Patient) -> None:
    """Cierra el estudio cuya numeración quedó atrás y deja lugar a uno nuevo.

    **Por qué automático y no un `409`.** Hasta septiembre de 2026 esto devolvía
    `409 STUDY_SEQ_REWIND` y ahí se terminaba: la señal no se perdía —un 409 no
    es un ACK, así que el equipo no borra nada— pero el equipo reintentaba el
    mismo lote indefinidamente y el estudio no volvía a avanzar solo. Si el corte
    duraba lo suficiente, el backlog de esa sesión daba la vuelta y **ahí sí** se
    perdía registro. La regla operativa ("actualizar el firmware solo con el
    estudio cerrado") sigue siendo buena práctica, pero no puede ser lo único que
    separe al paciente de un estudio trunco.

    **Cerrar con lotes pendientes es seguro.** La guarda de estudio terminal de
    `processing.py` solo protege `ended_at` y `duration_ms`: los lotes que
    quedaron en `PENDING` se siguen drenando y `samples_count` sigue creciendo.
    Nada de lo ya archivado se pierde ni queda huérfano.

    El estudio nuevo lo crea `_resolve_study` en la llamada siguiente, porque
    `get_open_study_for_update` filtra por `IN_PROGRESS` y éste ya no lo está.
    """
    now = datetime.now(UTC)
    study.status = StudyStatus.COMPLETED
    study.ended_at = now

    db.add(
        Alert(
            patient_id=patient.id,
            event_id=None,
            kind=SEQ_REWIND_ALERT_KIND,
            severity=AlertSeverity.HIGH,
            message=(
                "El equipo reinició su numeración de tramas (habitualmente, una "
                "actualización de firmware con el estudio abierto). El estudio en curso "
                "se cerró y la señal nueva se archiva en uno nuevo. Lo que el equipo "
                "tenía sin subir del estudio anterior se perdió."
            ),
        )
    )
    await logger.awarning(
        "study_seq_rewind_recovered",
        study_id=str(study.id),
        patient_id=str(patient.id),
        last_ingested_seq=study.last_ingested_seq,
        last_boot_id=study.last_boot_id,
    )


async def ingest_frames(
    ctx: DeviceContext,
    input_data: IngestFramesInput,
    db: AsyncSession,
    background: BackgroundTasks | None = None,
) -> IngestAckOut:
    if len(input_data.payload) > settings.ingest_max_batch_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": "INGEST_BATCH_TOO_LARGE",
                "message": (
                    f"El lote supera {settings.ingest_max_batch_bytes} bytes. Enviarlo en partes."
                ),
            },
        )

    parsed, rejected = _parse(input_data.payload)
    received = len(input_data.payload) // 256

    # El ancla del tramo: instante UTC en que el `millis()` del equipo valía 0.
    # Con las cabeceras del puente las dos cifras de la resta las mide el mismo
    # lado del enlace, así que la latencia del pedido queda afuera de la hora
    # del paciente (`docs/integracion-ingesta-con-horario.md`).
    epoch_anchor_ms, sync_source, sync_uncertainty_ms = ctx.boot_epoch_ms(input_data.received_at)

    if not parsed:
        # Todas las tramas fallaron la validación. Igual hay que resolver el
        # estudio para poder reportar el hueco contra algo.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "INGEST_NO_VALID_FRAMES",
                "message": f"Ninguna de las {received} tramas pasó la validación.",
            },
        )

    # El filtro por bootId va ANTES de deduplicar: un lote puede cruzar un
    # reinicio del equipo, y dos tramas de boots distintos pueden compartir
    # `seq`. Deduplicar primero haría desaparecer una de las dos en silencio.
    # Solo se procesa el tramo del primer bootId; el resto llega en el request
    # siguiente, con su propia ancla temporal — mezclarlos bajo una sola ancla
    # daría horas de pared incorrectas.
    boot_id = parsed[0].info.boot_id
    frames = _dedupe_by_seq([f for f in parsed if f.info.boot_id == boot_id])

    # La autenticación leyó el equipo sin lock. Se vuelve a cargar dentro de la
    # transacción del caso de uso para competir de forma segura con otra ingesta
    # y con assign/unassign/reassign, que bloquean la misma fila primero.
    locked_device = await repo.get_device_for_update(db, ctx.device.id)
    if locked_device is None:
        raise _conflict("DEVICE_NOT_INGESTABLE", "El dispositivo ya no está disponible.")
    if locked_device.status not in INGESTABLE_STATUSES:
        raise _conflict(
            "DEVICE_NOT_INGESTABLE",
            f"El dispositivo está en estado '{locked_device.status.value}'.",
        )
    ctx = replace(ctx, device=locked_device)

    study, patient = await _resolve_study(db, ctx, frames[0].info, epoch_anchor_ms)
    if await _is_seq_rewind(db, study, frames, boot_id):
        await _recover_from_seq_rewind(db, study, patient)
        # El cerrado ya no matchea `get_open_study_for_update`, así que esto crea
        # uno nuevo. Arranca con `last_ingested_seq = None`, o sea que el lote
        # entra entero desde su primera trama, que es lo correcto: para el estudio
        # nuevo no hay nada anterior.
        study, patient = await _resolve_study(db, ctx, frames[0].info, epoch_anchor_ms)
    window = _ack_window(frames, study)

    ctx.device.last_seen_at = input_data.received_at
    if ctx.battery_pct is not None:
        ctx.device.last_battery_pct = ctx.battery_pct
    if ctx.firmware_version:
        ctx.device.firmware_version = ctx.firmware_version

    # Los estados graves del equipo viajan en `X-Device-Status-Flags` del mismo
    # POST. Es una query indexada que solo corre cuando hay un bit prendido, o
    # sea nunca en un equipo sano.
    await _raise_device_faults(db, ctx, input_data.received_at)

    # El paciente también tiene telemetría, y hasta acá nadie la escribía: su
    # `study_status` se quedaba en el valor del alta y `last_data_received_at`
    # en NULL para siempre. El dashboard cuenta pacientes por ese campo, así que
    # sin esto el KPI "Pacientes activos" daba 0 con estudios corriendo.
    #
    # Se escribe con cualquier lote de tramas válidas, incluso si todas resultan
    # duplicadas: que el equipo esté retransmitiendo también es señal de vida.
    patient.last_data_received_at = input_data.received_at
    if study.status is StudyStatus.IN_PROGRESS:
        patient.study_status = PatientStudyStatus.ACTIVE

    batch_id: uuid.UUID | None = None
    if window.accepted:
        batch_id = await _store_batch(
            db,
            ctx,
            input_data,
            study,
            window,
            boot_id,
            _Anchor(epoch_anchor_ms, sync_source, sync_uncertainty_ms),
        )
        if background is not None:
            from app.modules.ingest.processing import process_batch_task

            background.add_task(process_batch_task, batch_id)

    if frames[0].info.simulated and not study.is_simulated:
        # Una sola trama de banco contamina el estudio entero: no se puede
        # archivar como clínico (INTEGRACION.md §7.3). No hay camino de vuelta.
        study.is_simulated = True

    await db.commit()

    await logger.ainfo(
        "ingest_batch_received",
        serial=ctx.device.serial_number,
        study_id=str(study.id),
        received=received,
        accepted=len(window.accepted),
        duplicate=len(window.already_stored),
        rejected=rejected,
    )

    last_seq = window.accepted[-1].info.seq if window.accepted else study.last_ingested_seq
    return IngestAckOut(
        framesReceived=received,
        framesAccepted=window.total_ack,
        framesRejected=rejected,
        framesDuplicate=len(window.already_stored),
        lastAcceptedSeq=last_seq,
        batchId=batch_id,
        studyId=study.id,
        serverTime=datetime.now(UTC),
    )


async def _store_batch(
    db: AsyncSession,
    ctx: DeviceContext,
    input_data: IngestFramesInput,
    study: Study,
    window: _AckWindow,
    boot_id: int,
    anchor: _Anchor,
) -> uuid.UUID:
    """Archiva los bytes crudos y deja el lote listo para procesar.

    Solo se archiva el tramo **aceptado**. Las tramas posteriores a un hueco no
    se guardan porque el equipo las va a retransmitir igual (go-back-N reenvía
    desde la más vieja sin confirmar): guardarlas sería duplicar trabajo y dejar
    objetos que después hay que reconciliar.
    """
    accepted = window.accepted
    first_info = accepted[0].info
    last_info = accepted[-1].info

    key = frames_key(study.id, first_info.seq)
    body = b"".join(frame.payload for frame in accepted)
    # `put_object` es boto3 sincrónico: llamarlo derecho bloquea el event loop
    # entero mientras dura el handshake TLS y la subida. Va a un thread y se
    # espera antes del commit — la durabilidad antes del ACK no se toca, que es
    # lo que hace segura esta ingesta; lo que se saca es el bloqueo.
    await asyncio.to_thread(put_object, key, body)

    n_samples = sum(frame.info.n_samples for frame in accepted)
    duration_ms = (last_info.t0_ms + last_info.duration_ms) - first_info.t0_ms

    batch = ECGBatch(
        device_id=ctx.device.id,
        study_id=study.id,
        received_at=input_data.received_at,
        batch_timestamp=(anchor.epoch_ms + first_info.t0_ms) // 1000,
        duration_seconds=max(duration_ms // 1000, 0),
        sample_rate=SAMPLE_RATE_HZ,
        num_channels=first_info.n_channels,
        num_samples=n_samples,
        compression_type="rice-frame-v1",
        s3_key=key,
        frames_s3_key=key,
        file_size_bytes=len(body),
        processing_status=ProcessingStatus.PENDING,
        firmware_version=ctx.firmware_version or ctx.device.firmware_version,
        boot_id=boot_id,
        device_uptime_ms=ctx.uptime_ms,
        epoch_anchor_ms=anchor.epoch_ms,
        bridge_epoch_ms=ctx.bridge_epoch_ms,
        time_sync_source=anchor.source,
        time_sync_uncertainty_ms=anchor.uncertainty_ms,
        first_seq=first_info.seq,
        last_seq=last_info.seq,
        frames_count=len(accepted),
        frames_rejected=0,
        frames_duplicate=len(window.already_stored),
        # El hueco solo se conoce acá: el procesamiento ve el lote aislado y no
        # sabe contra qué cursor entró.
        preceding_seq_gap_frames=window.gap_frames,
        device_lead_flags=ctx.lead_flags,
        device_loss_flags=ctx.loss_flags,
        device_status_flags=ctx.status_flags,
        device_backlog_seconds=ctx.backlog_seconds,
    )
    await repo.create_batch(db, batch)

    study.last_ingested_seq = last_info.seq
    study.last_boot_id = boot_id
    return batch.id


# --------------------------------------------------------------------------- #
# Estado del equipo fuera del ciclo de envío
# --------------------------------------------------------------------------- #

#: Tipo de alerta que produce este canal. No cuelga de ningún `ecg_event`: la
#: señal de ese momento todavía está en la flash del chaleco.
VEST_ALERT_KIND = "vest_misplaced"

#: Falla del equipo que lo saca de servicio (`INTEGRACION.md` §3.1, `statusFlags`
#: bits 2, 4 y 6). Es un problema de hardware o de servicio técnico, **nunca del
#: paciente**: el mensaje tiene que decir "el equipo tiene una falla, no lo use",
#: no "revise los electrodos".
DEVICE_FAULT_ALERT_KIND = "device_fault"


async def _raise_device_faults(db: AsyncSession, ctx: DeviceContext, now: datetime) -> None:
    """Alerta los estados graves que el equipo reporta en `X-Device-Status-Flags`.

    Son **estados**, no eventos: el equipo los repite en cada STATUS mientras la
    condición esté, así que sin debounce una flash rota inundaría al médico con
    una alerta cada diez minutos. Se emite una por ventana de
    `device_fault_debounce_minutes` y por equipo.

    Solo se mira el byte de `statusFlags`. Los bits 0 y 1 de `leadOffFlags` no se
    leen en ninguna parte del sistema, y es a propósito: Biomédica midió que el
    comparador del ADS1292R no funciona en esta placa —0 % de detección en los
    dos electrodos cuya pérdida sí invalida la señal, y disparo espurio con el de
    tierra informando el electrodo equivocado— así que usarlos mandaría a
    recolocar el electrodo que no era.

    No hay push al paciente: no hay nada que pueda hacer con esto, y decirle que
    su equipo está roto sin poder darle un reemplazo es angustia sin acción.
    """
    faults = device_faults(ctx.status_flags)
    if not faults:
        return
    device = ctx.device
    if device.patient_id is None:
        # Sin paciente no hay a quién colgarle la alerta. Igual queda en el log,
        # que es donde lo va a ver quien prepara los equipos.
        await logger.awarning(
            "device_fault_unassigned",
            device_id=str(device.id),
            serial=device.serial_number,
            faults=[kind.value for kind, _ in faults],
        )
        return

    window_start = now - timedelta(minutes=settings.device_fault_debounce_minutes)
    if await repo.get_recent_alert(db, device.patient_id, DEVICE_FAULT_ALERT_KIND, window_start):
        return

    # Una sola alerta aunque haya varios bits: los tres estados se resuelven con
    # la misma acción (sacar el equipo de servicio) y el orden de `device_faults`
    # ya pone primero el más grave.
    kind, message = faults[0]
    db.add(
        Alert(
            patient_id=device.patient_id,
            event_id=None,
            kind=DEVICE_FAULT_ALERT_KIND,
            severity=AlertSeverity.CRITICAL,
            message=f"{device.serial_number}: {message}",
        )
    )
    await logger.awarning(
        "device_fault",
        device_id=str(device.id),
        serial=device.serial_number,
        fault=kind.value,
        status_flags=ctx.status_flags,
    )


_VEST_MESSAGES = {
    VestStatusEvent.SIGNAL_QUALITY_BAD: (
        "El chaleco viene registrando con mala calidad de señal desde hace {minutes} min."
    ),
    VestStatusEvent.LEAD_OFF: (
        "El chaleco perdió contacto con la piel hace {minutes} min. Puede estar mal colocado."
    ),
}


def _vest_message(event: VestStatusEvent, duration_seconds: int) -> str:
    minutes = max(1, round(duration_seconds / 60))
    return _VEST_MESSAGES[event].format(minutes=minutes)


async def report_device_status(
    ctx: DeviceContext,
    input_data: DeviceStatusInput,
    db: AsyncSession,
    background: BackgroundTasks,
) -> DeviceStatusAckOut:
    """Registra el aviso del chaleco y despierta al paciente si hace falta.

    Cuatro decisiones que valen la pena:

    - La alerta se crea con `event_id = NULL`. El chaleco está contando algo que
      pasa **ahora**, y la señal correspondiente recién va a existir en el
      próximo envío. Forzar un `ecg_event` sería inventar coordenadas.
    - La colocación se guarda en el equipo (`device.placement_ok`) además de
      quedar como alerta. La alerta es el registro de que pasó; la columna es el
      estado actual, que es lo que la app dibuja. Sin ella, "ya me lo acomodé"
      no tiene dónde escribirse y la pantalla tiene que adivinarlo por tiempo.
    - Hay debounce por paciente, pero **dentro del episodio**: se absorbe el
      aviso repetido de un chaleco que ya venía mal, no el primero de un
      episodio nuevo. Un equipo que rebota mientras alguien se lo acomoda no
      puede vaciarle la batería al celular; un chaleco que se acomodó y se
      volvió a soltar a los cinco minutos sí tiene que avisar.
    - El push va en background. La respuesta al equipo no puede depender de que
      `exp.host` conteste, porque el chaleco tiene la radio prendida esperándola.
    """
    device = ctx.device
    now = input_data.received_at
    device.last_seen_at = now
    if input_data.data.batteryPct is not None:
        device.last_battery_pct = input_data.data.batteryPct
    elif ctx.battery_pct is not None:
        device.last_battery_pct = ctx.battery_pct
    if ctx.firmware_version:
        device.firmware_version = ctx.firmware_version
    if input_data.data.sqi is not None:
        device.last_sqi = input_data.data.sqi

    event = input_data.data.event

    # Los estados graves del equipo van por acá también, y no solo por la
    # ingesta: justamente el caso que importa —la flash que no monta— es uno en
    # el que NO hay tramas, así que no hay ningún POST de ingesta donde verlo.
    await _raise_device_faults(db, ctx, now)

    # `alive` es un latido y nada más: escribe telemetría y sale antes de tocar
    # la colocación. Si siguiera de largo, `placement_ok` quedaría en `False`
    # —porque no es `signal_recovered`— y la app del paciente le diría que el
    # chaleco está mal puesto cada vez que el equipo dice que está vivo.
    if event is VestStatusEvent.ALIVE:
        await db.commit()
        return DeviceStatusAckOut(notified=False, alertId=None, serverTime=now)

    # Se lee antes de pisarlo: es lo que distingue "sigue mal" de "se volvió a
    # soltar", y de eso depende si el debounce corresponde.
    was_bad = device.placement_ok is False
    device.placement_ok = event is VestStatusEvent.SIGNAL_RECOVERED
    device.placement_reported_at = now

    # `signal_recovered` cierra el episodio: se guarda la telemetría y nada más.
    # Avisarle al paciente que "ya está bien" cuando probablemente ni vio el
    # aviso anterior es ruido.
    if event is VestStatusEvent.SIGNAL_RECOVERED or device.patient_id is None:
        await db.commit()
        return DeviceStatusAckOut(notified=False, alertId=None, serverTime=now)

    window_start = now - timedelta(minutes=settings.vest_status_debounce_minutes)
    recent = (
        await repo.get_recent_alert(db, device.patient_id, VEST_ALERT_KIND, window_start)
        if was_bad
        else None
    )
    if recent is not None:
        await db.commit()
        await logger.ainfo(
            "vest_status_debounced",
            device_id=str(device.id),
            vest_event=event.value,
            alert_id=str(recent.id),
        )
        return DeviceStatusAckOut(notified=False, alertId=recent.id, serverTime=now)

    alert = Alert(
        patient_id=device.patient_id,
        event_id=None,
        kind=VEST_ALERT_KIND,
        severity=AlertSeverity.HIGH,
        message=_vest_message(event, input_data.data.durationSeconds),
    )
    db.add(alert)
    await db.flush()
    alert_id = alert.id
    patient_id = device.patient_id
    await db.commit()

    background.add_task(notify_patient_task, patient_id, vest_message(alert_id, now.isoformat()))
    await logger.ainfo(
        "vest_status_alert",
        device_id=str(device.id),
        vest_event=event.value,
        duration_seconds=input_data.data.durationSeconds,
        alert_id=str(alert_id),
    )
    return DeviceStatusAckOut(notified=True, alertId=alert_id, serverTime=now)
