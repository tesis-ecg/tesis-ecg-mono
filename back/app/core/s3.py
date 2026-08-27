"""Clientes S3 compartidos.

Hay **dos** clientes y la diferencia importa:

- `get_s3_client()` firma y habla contra `s3_endpoint_url`, el host que ve el
  backend. Es el que hace put/get/delete desde el servidor.
- `get_presign_client()` firma contra `s3_public_endpoint_url`, el host al que
  llega el navegador. SigV4 incluye el header `Host` en la firma, así que
  reescribir el host de una URL ya firmada la invalida: hay que firmarla
  directamente contra el endpoint público.

En Docker Compose son distintos (`http://minio:9000` vs `http://localhost:9000`);
en AWS real ambos quedan vacíos y boto3 resuelve el endpoint por región.

Los clientes se cachean porque construirlos no es gratis y son thread-safe para
lectura. `reset_s3_clients()` limpia el caché — lo necesitan los tests, que
montan `moto` *después* de que el módulo se importó.
"""

from functools import lru_cache
from typing import Any, cast

import boto3
from botocore.config import Config

from app.core.config import settings


def _build_client(endpoint: str) -> Any:
    # `addressing_style` explícito, nunca "auto": contra AWS real, "auto" arma el
    # host global `<bucket>.s3.amazonaws.com`, que responde 307 hacia la región
    # del bucket. Un cliente boto3 sigue ese redirect solo, pero una URL
    # prefirmada no puede: SigV4 firma el header `Host`, así que el browser
    # recibe un 307 sin headers CORS y, si lo siguiera, la firma ya no valida.
    # Con MinIO hace falta "path": `<bucket>.minio` no resuelve por DNS.
    addressing_style = "path" if endpoint else "virtual"
    client_kwargs: dict[str, object] = {
        "aws_access_key_id": settings.aws_access_key_id,
        "aws_secret_access_key": settings.aws_secret_access_key,
        "region_name": settings.aws_region,
        "config": Config(
            signature_version="s3v4",
            s3={"addressing_style": addressing_style},
        ),
    }
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **client_kwargs)


@lru_cache
def get_s3_client() -> Any:
    """Cliente para operar sobre los objetos desde el backend."""
    return _build_client(settings.s3_endpoint_url)


@lru_cache
def get_presign_client() -> Any:
    """Cliente para firmar URLs que descarga el navegador."""
    return _build_client(settings.s3_public_endpoint_url or settings.s3_endpoint_url)


def reset_s3_clients() -> None:
    """Invalida los clientes cacheados (tests con `moto`, rotación de credenciales)."""
    get_s3_client.cache_clear()
    get_presign_client.cache_clear()


def build_presigned_url(key: str, expires_in: int | None = None) -> str:
    return cast(
        str,
        get_presign_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key},
            ExpiresIn=expires_in or settings.s3_presign_expire_seconds,
        ),
    )


def put_object(key: str, payload: bytes) -> None:
    get_s3_client().put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=payload,
        ContentType="application/octet-stream",
    )


def get_object(key: str) -> bytes:
    response = get_s3_client().get_object(Bucket=settings.s3_bucket_name, Key=key)
    return cast(bytes, response["Body"].read())


def delete_keys(keys: list[str]) -> None:
    client = get_s3_client()
    for start in range(0, len(keys), 1000):  # `delete_objects` acepta hasta 1000 por llamada
        client.delete_objects(
            Bucket=settings.s3_bucket_name,
            Delete={"Objects": [{"Key": key} for key in keys[start : start + 1000]]},
        )


def ensure_bucket() -> None:
    from botocore.exceptions import ClientError

    client = get_s3_client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket_name)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket_name)


def list_keys(prefix: str) -> list[str]:
    """Todas las claves bajo `prefix`, ordenadas (los nombres llevan el seq con padding)."""
    client = get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=settings.s3_bucket_name, Prefix=prefix):
        keys.extend(item["Key"] for item in page.get("Contents", []))
    return sorted(keys)
