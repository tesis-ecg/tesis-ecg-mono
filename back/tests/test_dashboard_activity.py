"""Series y totales de la home.

El dashboard mostraba cinco tablas y tres KPIs con los deltas en `None` ("TODO:
requieren histórico"). No hacía falta histórico: `alert`, `patient_report`,
`study` y `patient` ya guardan cuándo apareció cada fila. Lo que se prueba acá es
que esas cuentas no se desvíen de los widgets que ya existían — un gráfico que
contradice a la tabla de al lado es peor que no tener gráfico.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from httpx import AsyncClient

from app.db.models.alert import Alert, AlertSeverity
from app.db.models.device import DeviceStatus
from app.db.models.user import User, UserRole
from app.modules.dashboard import dashboard_repository
from app.modules.dashboard.dashboard_time import dashboard_date

URL = "/dashboard/overview"


async def _doctor_user(db: Any, doctor: Any) -> User:
    user = await db.get(User, doctor.user_id)
    assert user is not None
    return user


async def _make_alert(
    db: Any,
    patient: Any,
    severity: AlertSeverity = AlertSeverity.HIGH,
    created_at: datetime | None = None,
) -> Alert:
    """Alerta sin `ecg_event` detrás: alcanza para contar, que es lo que se prueba."""
    alert = Alert(
        patient_id=patient.id,
        event_id=None,
        kind="afib",
        severity=severity,
        message="Ritmo irregular",
    )
    db.add(alert)
    await db.flush()
    if created_at is not None:
        # `created_at` lo pone el default del ORM; para poner una fila en un día
        # pasado hay que pisarlo después del flush.
        alert.created_at = created_at
        await db.flush()
    return alert


async def test_la_serie_siempre_trae_siete_dias_con_ceros(
    db: Any, as_user: Any, make_doctor: Any, make_patient: Any
) -> None:
    """Un día sin actividad tiene que valer cero, no faltar.

    Con la serie dispersa el gráfico dibujaba siete barras la semana que hubo
    algo todos los días y cuatro la que no, y el eje dejaba de ser comparable.
    """
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    await _make_alert(db, patient)
    client: AsyncClient = as_user(await _doctor_user(db, doctor))

    activity = (await client.get(URL)).json()["activity"]

    assert len(activity["days"]) == 7
    assert sum(day["alerts"] for day in activity["days"]) == 1
    assert [day["date"] for day in activity["days"]] == sorted(
        day["date"] for day in activity["days"]
    )


async def test_los_dias_del_dashboard_usan_el_calendario_argentino(
    db: Any, make_doctor: Any, make_patient: Any
) -> None:
    """00:30 UTC todavía pertenece al día anterior en Argentina."""
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    await _make_alert(db, patient, created_at=datetime(2026, 9, 5, 0, 30, tzinfo=UTC))

    alerts, _, _ = await dashboard_repository.count_activity_by_day(
        db, doctor.id, datetime(2026, 9, 4, tzinfo=UTC)
    )

    assert alerts == {date(2026, 9, 4): 1}
    assert dashboard_date(datetime(2026, 9, 5, 1, tzinfo=UTC)) == date(2026, 9, 4)


async def test_el_desglose_por_severidad_suma_las_alertas_pendientes(
    db: Any, as_user: Any, make_doctor: Any, make_patient: Any
) -> None:
    """El donut y el KPI cuentan lo mismo o uno de los dos miente.

    `pendingAlerts` suma además los equipos sin transmitir (alertas sintéticas
    que no viven en la tabla `alert`), así que la comparación se hace contra el
    listado de alertas reales del mismo payload.
    """
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    await _make_alert(db, patient, AlertSeverity.CRITICAL)
    await _make_alert(db, patient, AlertSeverity.HIGH)
    await _make_alert(db, patient, AlertSeverity.HIGH)
    client: AsyncClient = as_user(await _doctor_user(db, doctor))

    body = (await client.get(URL)).json()
    buckets = {item["severity"]: item["count"] for item in body["activity"]["pendingBySeverity"]}

    assert buckets == {"critical": 1, "high": 2, "medium": 0, "low": 0}
    # Las cuatro severidades siempre están, en orden de gravedad: el donut y su
    # leyenda no pueden reordenarse según qué haya pasado esta semana.
    assert [item["severity"] for item in body["activity"]["pendingBySeverity"]] == [
        "critical",
        "high",
        "medium",
        "low",
    ]


async def test_una_alerta_reconocida_sale_del_desglose(
    db: Any, as_user: Any, make_doctor: Any, make_patient: Any
) -> None:
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    alert = await _make_alert(db, patient, AlertSeverity.CRITICAL)
    alert.acknowledged_at = datetime.now(UTC)
    await db.flush()
    client: AsyncClient = as_user(await _doctor_user(db, doctor))

    activity = (await client.get(URL)).json()["activity"]

    assert all(item["count"] == 0 for item in activity["pendingBySeverity"])
    # Pero sigue contando en la serie del día: el gráfico muestra cuánto llegó,
    # no cuánto quedó sin leer.
    assert sum(day["alerts"] for day in activity["days"]) == 1


async def test_cada_medico_ve_solo_lo_suyo(
    db: Any, as_user: Any, make_doctor: Any, make_patient: Any
) -> None:
    mine = await make_doctor()
    theirs = await make_doctor()
    await _make_alert(db, await make_patient(mine))
    await _make_alert(db, await make_patient(theirs))
    await _make_alert(db, await make_patient(theirs))
    client: AsyncClient = as_user(await _doctor_user(db, mine))

    activity = (await client.get(URL)).json()["activity"]

    assert sum(day["alerts"] for day in activity["days"]) == 1
    assert activity["alertsTrend"]["current"] == 1


async def test_el_admin_ve_la_actividad_de_todos(
    db: Any, as_user: Any, make_user: Any, make_doctor: Any, make_patient: Any
) -> None:
    await _make_alert(db, await make_patient(await make_doctor()))
    await _make_alert(db, await make_patient(await make_doctor()))
    admin = await make_user(UserRole.ADMIN)
    client: AsyncClient = as_user(admin)

    activity = (await client.get(URL)).json()["activity"]

    assert activity["alertsTrend"]["current"] == 2


async def test_la_tendencia_separa_las_dos_semanas(
    db: Any, as_user: Any, make_doctor: Any, make_patient: Any
) -> None:
    """`current` es esta semana y `previous` la anterior, sin solaparse."""
    doctor = await make_doctor()
    patient = await make_patient(doctor)
    now = datetime.now(UTC)
    await _make_alert(db, patient, created_at=now - timedelta(days=2))
    await _make_alert(db, patient, created_at=now - timedelta(days=9))
    await _make_alert(db, patient, created_at=now - timedelta(days=10))
    # Fuera de las dos ventanas: no cuenta en ninguna.
    await _make_alert(db, patient, created_at=now - timedelta(days=30))
    client: AsyncClient = as_user(await _doctor_user(db, doctor))

    trend = (await client.get(URL)).json()["activity"]["alertsTrend"]

    assert trend == {"current": 1, "previous": 2}


async def test_la_flota_cuenta_los_asignados_y_los_que_transmiten(
    db: Any, as_user: Any, make_doctor: Any, make_patient: Any, make_device: Any
) -> None:
    """Mismo corte de frescura que el watchdog: los dos widgets no se contradicen."""
    doctor = await make_doctor()
    fresh_patient = await make_patient(doctor)
    stale_patient = await make_patient(doctor)
    fresh, _ = await make_device(fresh_patient, status=DeviceStatus.ASSIGNED)
    stale, _ = await make_device(stale_patient, status=DeviceStatus.ASSIGNED)
    fresh.last_seen_at = datetime.now(UTC)
    stale.last_seen_at = datetime.now(UTC) - timedelta(days=3)
    await db.flush()
    client: AsyncClient = as_user(await _doctor_user(db, doctor))

    body = (await client.get(URL)).json()

    assert body["activity"]["fleet"] == {"assigned": 2, "transmitting": 1}
    assert {item["serial"] for item in body["deviceWatchdog"]} == {stale.serial_number}


async def test_el_overview_limita_los_listados_de_la_home(
    db: Any,
    as_user: Any,
    make_doctor: Any,
    make_patient: Any,
    make_device: Any,
    make_study: Any,
) -> None:
    """La home resume; las pantallas de detalle conservan los listados completos."""
    doctor = await make_doctor()
    now = datetime.now(UTC)
    patients = []
    for index in range(6):
        patient = await make_patient(doctor, last_data_received_at=now - timedelta(minutes=index))
        device, _ = await make_device(
            patient,
            last_seen_at=now,
            last_battery_pct=10,
        )
        await make_study(patient, device, started_at=now - timedelta(hours=index + 1))
        patients.append(patient)

    for index in range(10):
        await _make_alert(
            db,
            patients[index % len(patients)],
            created_at=now - timedelta(minutes=index),
        )

    client: AsyncClient = as_user(await _doctor_user(db, doctor))
    body = (await client.get(URL)).json()

    assert len(body["alerts"]) == 8
    assert len(body["attentionPatients"]) == 4
    assert len(body["runningStudies"]) == 4
    assert len(body["deviceWatchdog"]) == 4


async def test_una_home_vacia_no_rompe_los_graficos(
    db: Any, as_user: Any, make_doctor: Any
) -> None:
    """Un médico recién dado de alta: todo en cero, nada en `None`."""
    doctor = await make_doctor()
    client: AsyncClient = as_user(await _doctor_user(db, doctor))

    activity = (await client.get(URL)).json()["activity"]

    assert len(activity["days"]) == 7
    assert activity["fleet"] == {"assigned": 0, "transmitting": 0}
    assert activity["alertsTrend"] == {"current": 0, "previous": 0}
    assert all(item["count"] == 0 for item in activity["pendingBySeverity"])
