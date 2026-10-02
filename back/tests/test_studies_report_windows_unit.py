"""Casos puros del lector de tiras del informe, sin requerir PostgreSQL."""

import struct
from datetime import UTC, datetime
from types import SimpleNamespace

from app.modules.studies import studies_service


def _study_with_envelope() -> SimpleNamespace:
    return SimpleNamespace(
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        sample_rate=16,
        samples_count=64,
        ecg_pyramid_levels=[
            {
                "samplesPerBucket": 16,
                "chunks": [{"key": "level.f32", "pointCount": 8}],
            }
        ],
    )


def test_envelope_fallback_reads_finest_level_and_marks_no_raw(monkeypatch) -> None:
    study = _study_with_envelope()
    payload = struct.pack("<8f", -1.0, 1.0, -2.0, 2.0, -3.0, 3.0, -4.0, 4.0)
    reads: list[tuple[str, int, int]] = []

    def get_range(key: str, start: int, end: int) -> bytes:
        reads.append((key, start, end))
        return payload[start : end + 1]

    monkeypatch.setattr(studies_service, "_get_ecg_object_range", get_range)
    start = int(study.started_at.timestamp() * 1000)

    timestamps, samples, gaps = studies_service._read_envelope_window(
        study, [], start, start + 4_000
    )

    assert reads == [("level.f32", 0, len(payload) - 1)]
    assert samples == [-1.0, 1.0, -2.0, 2.0, -3.0, 3.0, -4.0, 4.0]
    assert timestamps == [
        start,
        start,
        start + 1_000,
        start + 1_000,
        start + 2_000,
        start + 2_000,
        start + 3_000,
        start + 3_000,
    ]
    assert gaps == []
