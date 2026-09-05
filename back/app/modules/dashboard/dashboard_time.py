"""Calendario civil usado por los gráficos diarios del dashboard."""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

DASHBOARD_TIMEZONE_NAME = "America/Argentina/Buenos_Aires"
_DASHBOARD_TIMEZONE = ZoneInfo(DASHBOARD_TIMEZONE_NAME)


def dashboard_date(moment: datetime) -> date:
    """Fecha de calendario argentina para un instante con zona horaria."""
    return moment.astimezone(_DASHBOARD_TIMEZONE).date()


def dashboard_day_start_utc(day: date) -> datetime:
    """Medianoche argentina convertida a UTC para filtrar ``timestamptz``."""
    return datetime.combine(day, time.min, tzinfo=_DASHBOARD_TIMEZONE).astimezone(UTC)
