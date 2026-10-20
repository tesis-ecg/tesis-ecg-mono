"""Almacenamiento de las etiquetas por latido (`app/ml/beat_labels.py`).

Un objeto de S3 por bloque del motor (`studies/{id}/ml/beats.{inicio}.f1.bin`),
listado en `study.ml_state["beatLabels"]`:

    {"format": 1, "classifier": "beats-1.0.0", "fromSample": int,
     "chunks": [{key, startSampleIndex, sampleCount, beatCount, tier, byteLength, sha256}]}

**Por qué un chunk por bloque y niveles.** La fila del estudio viaja entera en
cada lote (egress de Neon): una lista de un chunk por bloque de 5 min serían
4.300 entradas en 15 días. Cuando hay `TIER_FANOUT` chunks consecutivos del
mismo nivel se funden en uno del nivel siguiente, sin tocar los anteriores: la
lista queda en menos de `TIER_FANOUT * niveles` (72 a 15 días) y cada latido se
reescribe 3 veces como mucho. Al cerrar el estudio se funde todo en uno.

Hay chunk aunque el bloque no tenga latidos (`beatCount` 0): sin él no se puede
verificar que el estudio entero fue clasificado.

**Cobertura.** `fromSample` es el inicio del primer bloque clasificado de la
racha vigente. Si el motor analizó bloques con la clasificación apagada
(`ml_beat_classification_enabled`) la racha se reinicia en el próximo bloque
clasificado: las etiquetas anteriores quedan fuera, y quien las lea (el informe
Holter) ve un estudio parcial y no ceros.

Todo acá es bloqueante (S3): se llama desde un hilo o, como el resto de
`ml_persistence`, con la fila del estudio tomada.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from app.core.s3 import get_object, put_object
from app.db.models.study import Study
from app.ml.beat_labels import (
    BEAT_LABEL_FORMAT,
    CLASSIFIER_VERSION,
    BeatLabelRecords,
    BeatLabelStream,
    decode_beat_labels,
    empty_records,
    encode_beat_labels,
)

#: Chunks consecutivos del mismo nivel que se funden en uno del siguiente.
TIER_FANOUT = 24

STATE_KEY = "beatLabels"


def chunk_key(study_id: uuid.UUID, start: int, end: int | None = None) -> str:
    """Clave del chunk de un bloque, o del fundido `[start, end)` de varios.

    El fundido lleva su fin en la clave: no pisa al primer chunk que reemplaza,
    que la base sigue referenciando si la transacción que lo registra se
    revierte (mismo criterio que `ml_persistence.templates_key`).
    """
    span = f"{start:012d}" if end is None else f"{start:012d}-{end:012d}"
    return f"studies/{study_id}/ml/beats.{span}.f{BEAT_LABEL_FORMAT}.bin"


def _meta(key: str, payload: bytes, **extra: object) -> dict[str, Any]:
    return {
        "key": key,
        "byteLength": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        **extra,
    }


def append_block(
    study_id: uuid.UUID,
    state: Mapping[str, Any] | None,
    records: BeatLabelRecords,
    *,
    block_start: int,
    block_end: int,
    cursor_before: int,
) -> dict[str, Any]:
    """Agrega las etiquetas de un bloque al estado y funde los niveles que se llenaron.

    `cursor_before` es el cursor del motor antes del bloque
    (`study.ml_analyzed_samples`). Si no coincide con el final del último chunk
    hubo bloques analizados sin clasificar, y la racha se reinicia acá.
    """
    chunks: list[dict[str, Any]] = [dict(chunk) for chunk in (state or {}).get("chunks", [])]
    contiguous = bool(chunks) and (
        int(chunks[-1]["startSampleIndex"]) + int(chunks[-1]["sampleCount"]) == cursor_before
    )
    from_sample = int((state or {}).get("fromSample", block_start)) if contiguous else block_start
    if not contiguous:
        chunks = []

    payload = encode_beat_labels(records)
    key = chunk_key(study_id, block_start)
    put_object(key, payload)
    chunks.append(
        _meta(
            key,
            payload,
            startSampleIndex=block_start,
            sampleCount=block_end - block_start,
            beatCount=int(records.size),
            tier=0,
        )
    )
    chunks = _promote(study_id, chunks)
    return {
        "format": BEAT_LABEL_FORMAT,
        "classifier": CLASSIFIER_VERSION,
        "fromSample": from_sample,
        "chunks": chunks,
    }


def _promote(study_id: uuid.UUID, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Funde cada racha de `TIER_FANOUT` chunks consecutivos del mismo nivel al siguiente."""
    while True:
        tier = int(chunks[-1]["tier"])
        run = 0
        for chunk in reversed(chunks):
            if int(chunk["tier"]) != tier:
                break
            run += 1
        if run < TIER_FANOUT:
            return chunks
        chunks = [*chunks[:-run], _fuse(study_id, chunks[-run:], tier + 1)]


def _fuse(study_id: uuid.UUID, chunks: Sequence[Mapping[str, Any]], tier: int) -> dict[str, Any]:
    start = int(chunks[0]["startSampleIndex"])
    end = int(chunks[-1]["startSampleIndex"]) + int(chunks[-1]["sampleCount"])
    payload = b"".join(get_object(str(chunk["key"])) for chunk in chunks)
    key = chunk_key(study_id, start, end)
    put_object(key, payload)
    return _meta(
        key,
        payload,
        startSampleIndex=start,
        sampleCount=end - start,
        beatCount=sum(int(chunk["beatCount"]) for chunk in chunks),
        tier=tier,
    )


def fuse_all(study: Study) -> dict[str, Any] | None:
    """El estado con todos los chunks fundidos en uno (cierre del estudio).

    None si no hay etiquetas o ya es un solo chunk.
    """
    state = (study.ml_state or {}).get(STATE_KEY)
    if not isinstance(state, dict):
        return None
    chunks = list(state.get("chunks", []))
    if len(chunks) <= 1:
        return None
    tier = max(int(chunk["tier"]) for chunk in chunks) + 1
    return {**state, "chunks": [_fuse(study.id, chunks, tier)]}


def load_beat_labels(study: Study) -> BeatLabelStream | None:
    """Todas las etiquetas del estudio, o None si el estudio nunca fue clasificado.

    **Bloqueante** (S3): llamar desde un hilo. `until_sample` es el final del
    último chunk guardado, acotado por el cursor del motor: lo que está después
    no se clasificó y, para las métricas, es "pendiente" y no "sin ectópicas".
    No alcanza con el cursor: con la clasificación apagada el motor sigue
    avanzando sin escribir etiquetas, y el cursor afirmaría una cobertura que
    los chunks no tienen.
    """
    state = (study.ml_state or {}).get(STATE_KEY)
    if not isinstance(state, dict):
        return None
    format_ = int(state.get("format", BEAT_LABEL_FORMAT))
    parts = [
        decode_beat_labels(get_object(str(chunk["key"])), format=format_)
        for chunk in state.get("chunks", [])
    ]
    records = np.concatenate(parts) if parts else empty_records()
    from_sample = int(state.get("fromSample", 0))
    chunks = state.get("chunks", [])
    stored_until = (
        int(chunks[-1]["startSampleIndex"]) + int(chunks[-1]["sampleCount"])
        if chunks
        else from_sample
    )
    return BeatLabelStream(
        records=records,
        from_sample=from_sample,
        until_sample=min(stored_until, int(study.ml_analyzed_samples)),
        classifier=str(state.get("classifier", CLASSIFIER_VERSION)),
    )
