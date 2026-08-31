from enum import StrEnum
from typing import Literal

from pydantic import AnyHttpUrl, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PREVIEW = "preview"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    s3_bucket_name: str
    s3_endpoint_url: str = ""
    # Endpoint con el que se firman las URLs que consume el navegador. En Docker,
    # `s3_endpoint_url` es `http://minio:9000` — un host que solo resuelve dentro
    # de la red de compose. La firma SigV4 incluye el header `Host`, así que la URL
    # no se puede reescribir después: hay que firmarla directamente contra el host
    # público. Vacío = usar `s3_endpoint_url`.
    s3_public_endpoint_url: str = ""
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-east-1"
    environment: Environment = Environment.DEVELOPMENT

    # Auth0
    auth0_domain: str
    auth0_client_id: str
    auth0_client_secret: str
    auth0_audience: str
    auth0_mgmt_client_id: str
    auth0_mgmt_client_secret: str
    auth0_connection: str = "Username-Password-Authentication"

    # Session JWT (our own, not Auth0's)
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = Field(default=60, ge=5, le=1440)
    jwt_issuer: str = "holter-api"
    jwt_audience: str = "holter-dashboard"
    # Audience propia de la app móvil. Separarla es lo que impide que una cookie
    # de sesión del portal sirva como Bearer en `/mobile` y viceversa: los dos
    # tokens los firma el mismo secreto, y sin esto serían intercambiables.
    jwt_mobile_audience: str = "holter-mobile"
    mobile_access_expire_minutes: int = Field(default=60, ge=5, le=1440)
    # 60 días: el paciente usa la app cada tantos días, no todos los días. Un
    # refresh corto lo obligaría a re-loguearse justo cuando llega el aviso.
    mobile_refresh_expire_days: int = Field(default=60, ge=1, le=365)
    auth_rate_limit_secret: str | None = Field(default=None, min_length=32)
    readiness_token: str | None = Field(default=None, min_length=32)

    frontend_url: AnyHttpUrl = AnyHttpUrl("http://localhost:5173")
    s3_presign_expire_seconds: int = Field(default=600, ge=60, le=3600)

    # Ingesta de tramas del chaleco.
    # 8 MB ≈ 32.700 tramas ≈ 4,8 h de backlog a 1,8 tramas/s. Un equipo que
    # estuvo más tiempo sin conexión tiene que trocear el envío — que es lo que
    # hace igual, porque la flash de a bordo aguanta ~9,9 h.
    ingest_max_batch_bytes: int = Field(default=8 * 1024 * 1024, ge=256, le=64 * 1024 * 1024)

    # Dashboard / watchdog
    dashboard_stale_hours: int = 10
    dashboard_low_battery_pct: int = 45
    # Los dos límites alimentan el `default` de un Query(ge=1, le=50), y FastAPI no
    # valida el default: las cotas tienen que estar acá o un .env fuera de rango
    # pasaría sin chistar cuando el FE llama sin query params.
    dashboard_widget_limit: int = Field(default=6, ge=1, le=50)
    dashboard_alerts_limit: int = Field(default=10, ge=1, le=50)

    # Push (Expo). Apagado por defecto: los tests y CI no salen a internet, y
    # con esto en falso el sender es un noop que igual registra qué se habría
    # mandado.
    expo_push_enabled: bool = False
    expo_push_url: str = "https://exp.host/--/api/v2/push/send"
    #: Solo hace falta si el proyecto de Expo tiene push security habilitado.
    expo_access_token: str | None = None
    #: Ventana de silencio por equipo para el aviso de mala colocación. Sin
    #: esto, un chaleco que rebota le manda quince notificaciones al paciente
    #: mientras se lo acomoda.
    vest_status_debounce_minutes: int = Field(default=30, ge=1, le=1440)

    @property
    def is_secure_environment(self) -> bool:
        return self.environment in {Environment.PREVIEW, Environment.PRODUCTION}

    @property
    def rate_limit_secret(self) -> str:
        return self.auth_rate_limit_secret or self.jwt_secret

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.is_secure_environment and self.frontend_url.scheme != "https":
            raise ValueError("FRONTEND_URL debe usar HTTPS fuera de development/test")
        if self.environment == Environment.PRODUCTION and self.s3_endpoint_url.startswith(
            "http://"
        ):
            raise ValueError("S3_ENDPOINT_URL no puede usar HTTP en producción")
        if self.is_secure_environment and (
            len(set(self.jwt_secret)) < 8
            or self.jwt_secret.lower() in {"change-me", "changeme", "secret"}
        ):
            raise ValueError("JWT_SECRET es demasiado predecible para preview/producción")
        if self.is_secure_environment and not self.readiness_token:
            raise ValueError("READINESS_TOKEN es obligatorio en preview/producción")
        return self


# `BaseSettings` values are provided from environment variables at runtime.
settings = Settings()  # type: ignore[call-arg]
