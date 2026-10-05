"""Opt-in load reproduction against PostgreSQL and the local S3 server (docker compose `s3`).

RUN_LOCAL_LOAD=1 TEST_DATABASE_URL=postgresql+asyncpg://holter:holter@127.0.0.1:5435/holter_test \
  .venv/bin/python -m pytest -s tests/test_ingest_local_load.py
"""

import os
import time
import uuid
from collections.abc import Iterator

import pytest

from app.core.config import settings
from app.core.s3 import ensure_bucket, get_s3_client, reset_s3_clients
from app.db.models.ecg_batch import ECGBatch, ProcessingStatus
from app.modules.ingest import ingest_service
from app.modules.ingest.processing import process_batch
from tests.ingest_helpers import build_frames, post_frames

pytestmark = pytest.mark.skipif(os.getenv("RUN_LOCAL_LOAD") != "1", reason="opt-in local load")


@pytest.fixture
def real_s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    bucket = f"holter-load-{uuid.uuid4().hex[:16]}"
    monkeypatch.setattr(settings, "s3_endpoint_url", "http://127.0.0.1:9000")
    monkeypatch.setattr(settings, "s3_public_endpoint_url", "http://127.0.0.1:9000")
    monkeypatch.setattr(settings, "aws_access_key_id", "minioadmin")
    monkeypatch.setattr(settings, "aws_secret_access_key", "minioadmin")
    monkeypatch.setattr(settings, "s3_bucket_name", bucket)
    reset_s3_clients()
    ensure_bucket()
    try:
        yield
    finally:
        client = get_s3_client()
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for item in page.get("Contents", []):
                client.delete_object(Bucket=bucket, Key=item["Key"])
        client.delete_bucket(Bucket=bucket)
        reset_s3_clients()


async def test_batch_sizes_and_sustained_uploads_against_local_services(
    client, real_s3, db, make_patient, make_device, monkeypatch
) -> None:
    patient = await make_patient()
    device, key = await make_device(patient=patient)
    frames = build_frames(600_000)
    assert len(frames) >= 1152
    elapsed_parse = 0.0
    elapsed_lock = 0.0
    elapsed_archive = 0.0
    parse = ingest_service._parse
    lock = ingest_service.repo.get_device_for_update
    archive = ingest_service.put_object

    def timed_parse(payload: bytes):
        nonlocal elapsed_parse
        started = time.perf_counter()
        result = parse(payload)
        elapsed_parse += time.perf_counter() - started
        return result

    async def timed_lock(*args):
        nonlocal elapsed_lock
        started = time.perf_counter()
        result = await lock(*args)
        elapsed_lock += time.perf_counter() - started
        return result

    def timed_archive(key: str, payload: bytes):
        nonlocal elapsed_archive
        started = time.perf_counter()
        result = archive(key, payload)
        elapsed_archive += time.perf_counter() - started
        return result

    monkeypatch.setattr(ingest_service, "_parse", timed_parse)
    monkeypatch.setattr(ingest_service.repo, "get_device_for_update", timed_lock)
    monkeypatch.setattr(ingest_service, "put_object", timed_archive)

    cursor = 0
    for size in (16, 48, 64, 128, 256, *([64] * 10)):
        batch = frames[cursor : cursor + size]
        assert len(batch) == size
        cursor += size
        parse_before, lock_before, archive_before = elapsed_parse, elapsed_lock, elapsed_archive
        started = time.perf_counter()
        response = await post_frames(client, device, key, batch, boot_id=0)
        ack_s = time.perf_counter() - started
        assert response.status_code == 202, response.text
        assert response.json()["framesAccepted"] == size
        batch_id = response.json()["batchId"]
        started = time.perf_counter()
        await process_batch(db, batch_id)
        processing_s = time.perf_counter() - started
        row = await db.get(ECGBatch, batch_id)
        assert row is not None and row.processing_status is ProcessingStatus.DONE
        print(
            f"size={size} ack={ack_s:.3f}s validation={elapsed_parse - parse_before:.3f}s "
            f"device_lock={elapsed_lock - lock_before:.3f}s "
            f"durable_s3={elapsed_archive - archive_before:.3f}s "
            f"post_ack_processing={processing_s:.3f}s"
        )
