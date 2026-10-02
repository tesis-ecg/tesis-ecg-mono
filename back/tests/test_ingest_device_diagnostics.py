"""Las cuatro cabeceras de diagnóstico del puente WiFi (`INTEGRACION.md` §11.1).

Son el **único** canal por el que este equipo puede avisar que está perdiendo
señal del paciente: backlog pisado, flash que no graba, trama descartada por
CRC, muestras perdidas aguas arriba, front-end que no inicializó. Hasta
septiembre de 2026 el backend las descartaba enteras, así que un equipo que
estaba perdiendo registro se veía idéntico a uno sano.

Lo que se prueba acá es sobre todo lo que NO se hace con ellas, que es la parte
fácil de romper: no rechazar un lote por un byte de diagnóstico, no alertar con
una estimación que Biomédica midió con +98 % de error, y no leer los bits de
lead-off del comparador del ADS1292R, que en esta placa está roto.
"""

from sqlalchemy import select

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.ecg_batch import ECGBatch
from app.ml.status_flags import (
    STATUS_FLAG_AFE_NOT_READY,
    STATUS_FLAG_BACKLOG_OVERFLOW,
    STATUS_FLAG_FLASH_NOT_READY,
    STATUS_FLAG_UPLINK_DOWN,
    STATUS_LEAD_LL_OFF,
    STATUS_LEAD_RA_OFF,
    STATUS_LOSS_SPI_DESYNC,
)
from app.modules.ingest.ingest_service import DEVICE_FAULT_ALERT_KIND
from tests.ingest_helpers import build_frames, post_frames


async def _faults(db, patient_id) -> list[Alert]:
    result = await db.execute(
        select(Alert).where(Alert.patient_id == patient_id, Alert.kind == DEVICE_FAULT_ALERT_KIND)
    )
    return list(result.scalars().all())


async def test_the_four_headers_are_archived_with_the_batch(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (
        await post_frames(
            client,
            device,
            api_key,
            build_frames(900),
            lead_flags=STATUS_LEAD_RA_OFF,
            loss_flags=STATUS_LOSS_SPI_DESYNC,
            status_flags=STATUS_FLAG_UPLINK_DOWN,
            backlog_seconds=600,
        )
    ).json()

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.device_lead_flags == STATUS_LEAD_RA_OFF
    assert batch.device_loss_flags == STATUS_LOSS_SPI_DESYNC
    assert batch.device_status_flags == STATUS_FLAG_UPLINK_DOWN
    assert batch.device_backlog_seconds == 600


async def test_a_firmware_that_does_not_send_them_still_ingests(
    client, s3, db, make_patient, make_device
) -> None:
    """Son aditivas: su ausencia es "firmware viejo", no un error.

    `NULL` y 0 no son lo mismo y por eso las columnas son nullable: 0 sería
    afirmar que el equipo reportó "todo en orden", y lo que pasó es que no
    reportó nada.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(client, device, api_key, build_frames(900))

    assert response.status_code == 202
    batch = await db.get(ECGBatch, response.json()["batchId"])
    assert batch is not None
    assert batch.device_status_flags is None
    assert batch.device_backlog_seconds is None


async def test_an_out_of_range_header_is_dropped_and_the_batch_still_lands(
    client, s3, db, make_patient, make_device
) -> None:
    """Un byte de diagnóstico roto no puede costar minutos de registro.

    Es el trade-off explícito de `_in_range`: lo que se pierde descartando es una
    lectura; lo que se perdería rechazando es señal del paciente que el equipo va
    a tener que retransmitir.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    response = await post_frames(
        client, device, api_key, build_frames(900), status_flags=999, backlog_seconds=70_000
    )

    assert response.status_code == 202
    batch = await db.get(ECGBatch, response.json()["batchId"])
    assert batch is not None
    assert batch.device_status_flags is None
    assert batch.device_backlog_seconds is None


async def test_a_grave_state_raises_a_critical_alert(
    client, s3, db, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    await post_frames(
        client, device, api_key, build_frames(900), status_flags=STATUS_FLAG_FLASH_NOT_READY
    )

    alerts = await _faults(db, patient.id)
    assert len(alerts) == 1
    assert alerts[0].severity is AlertSeverity.CRITICAL
    # El mensaje tiene que mandar a servicio técnico, no a recolocar electrodos:
    # la causa es del equipo y el paciente no puede hacer nada con ella.
    assert "servicio" in alerts[0].message


async def test_the_afe_fault_wins_over_the_others(
    client, s3, db, make_patient, make_device
) -> None:
    """Bit 6 antes que cualquier otro: sin adquisición no hay nada que grabar."""
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    await post_frames(
        client,
        device,
        api_key,
        build_frames(900),
        status_flags=STATUS_FLAG_FLASH_NOT_READY | STATUS_FLAG_AFE_NOT_READY,
    )

    alerts = await _faults(db, patient.id)
    assert len(alerts) == 1
    assert "front-end" in alerts[0].message


async def test_a_repeated_state_does_not_alert_twice(
    client, s3, db, make_patient, make_device
) -> None:
    """Los bits 2, 4 y 6 son ESTADOS: el equipo los repite en cada lote.

    Sin debounce, una flash rota le pondría al médico una alerta crítica cada
    diez minutos durante quince días.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(2600)

    await post_frames(client, device, api_key, frames[:2], status_flags=STATUS_FLAG_FLASH_NOT_READY)
    await post_frames(
        client, device, api_key, frames[2:4], status_flags=STATUS_FLAG_FLASH_NOT_READY
    )

    assert len(await _faults(db, patient.id)) == 1


async def test_a_healthy_device_raises_nothing(client, s3, db, make_patient, make_device) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    await post_frames(client, device, api_key, build_frames(900), status_flags=0)

    assert await _faults(db, patient.id) == []


async def test_the_broken_lead_off_comparator_bits_are_ignored(
    client, s3, db, make_patient, make_device
) -> None:
    """Los bits 0 y 1 de `leadOffFlags` no disparan nada, y es a propósito.

    Biomédica midió que el comparador del ADS1292R no funciona en esta placa: con
    el conector de electrodos entero desconectado el chip sigue informando que
    están bien puestos, y cuando sí dispara es con el electrodo de tierra —cuya
    pérdida no invalida la señal— informándolo como si se hubiera soltado RA.
    Usarlos mandaría a recolocar el electrodo equivocado.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (
        await post_frames(
            client,
            device,
            api_key,
            build_frames(900),
            lead_flags=STATUS_LEAD_RA_OFF | STATUS_LEAD_LL_OFF,
        )
    ).json()

    # Se archivan para poder diagnosticar, pero no generan ninguna alerta.
    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.device_lead_flags == STATUS_LEAD_RA_OFF | STATUS_LEAD_LL_OFF
    assert await _faults(db, patient.id) == []
    result = await db.execute(select(Alert).where(Alert.patient_id == patient.id))
    assert list(result.scalars().all()) == []


async def test_backlog_seconds_never_raises_an_alert(
    client, s3, db, make_patient, make_device
) -> None:
    """La estimación del firmware va ~2× alta y no se usa para decidir nada.

    Biomédica la midió con +98 % de error con electrodo seco y explicó por qué no
    la van a corregir: el firmware asume 280 muestras por trama y la señal real
    de esta placa comprime a 141. Alertar con eso diría "20 minutos" cuando son
    10. Se archiva como diagnóstico y nada más.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)

    body = (
        await post_frames(
            client, device, api_key, build_frames(900), backlog_seconds=65_535, status_flags=0
        )
    ).json()

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.device_backlog_seconds == 65_535
    result = await db.execute(select(Alert).where(Alert.patient_id == patient.id))
    assert list(result.scalars().all()) == []


async def test_an_overflow_gap_is_marked_as_confirmed_by_the_device(
    client, s3, db, make_patient, make_device
) -> None:
    """El bit 0 del STATUS es lo que convierte un hueco inferido en uno con causa.

    Sin él el hueco igual es real —las tramas no llegaron— pero no se sabe por
    qué. Con él, el equipo está confirmando que pisó backlog sin confirmar.
    """
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(3000, first_seq=0)

    await post_frames(client, device, api_key, frames[:2])
    body = (
        await post_frames(
            client, device, api_key, frames[4:6], status_flags=STATUS_FLAG_BACKLOG_OVERFLOW
        )
    ).json()

    batch = await db.get(ECGBatch, body["batchId"])
    assert batch is not None
    assert batch.preceding_seq_gap_frames == 2
    assert batch.device_status_flags == STATUS_FLAG_BACKLOG_OVERFLOW
