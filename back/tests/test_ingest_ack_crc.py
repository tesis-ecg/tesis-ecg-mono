"""`lastAcceptedCrc32`: de qué trama es el cursor del ACK.

`INTEGRACION.md` §11.6, pendiente 8.
"""

import struct

from tests.ingest_helpers import build_frames, post_frames


def _crc_field(frame: bytes) -> int:
    # Los bytes 20 a 23 de la trama: el campo CRC de la cabecera, no los últimos cuatro.
    return int(struct.unpack_from("<I", frame, 20)[0])


async def test_the_ack_names_the_crc_of_its_cursor_frame(
    client, s3, make_patient, make_device
) -> None:
    patient = await make_patient()
    device, api_key = await make_device(patient=patient)
    frames = build_frames(900)
    assert len(frames) >= 2

    first = (await post_frames(client, device, api_key, frames[:2])).json()
    assert first["lastAcceptedSeq"] == 1
    assert first["lastAcceptedCrc32"] == _crc_field(frames[1])

    # La misma respuesta se perdió y el equipo reintenta: el cursor no cambia.
    retry = (await post_frames(client, device, api_key, frames[:2])).json()
    assert retry["framesDuplicate"] == 2
    assert retry["lastAcceptedCrc32"] == _crc_field(frames[1])

    # Un lote que no trae la trama del cursor (solo la 0, ya archivada): el CRC
    # sale de lo archivado.
    older = (await post_frames(client, device, api_key, frames[:1])).json()
    assert older["lastAcceptedSeq"] == 1
    assert older["lastAcceptedCrc32"] == _crc_field(frames[1])
