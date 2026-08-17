from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    environment: str = "development"

    # Auth0
    auth0_domain: str
    auth0_client_id: str
    auth0_client_secret: str
    auth0_audience: str
    auth0_mgmt_client_id: str
    auth0_mgmt_client_secret: str
    auth0_connection: str = "Username-Password-Authentication"

    # Session JWT (our own, not Auth0's)
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    frontend_url: str = "http://localhost:5173"

    # Dashboard / watchdog
    dashboard_stale_hours: int = 10
    dashboard_low_battery_pct: int = 45
    # Los dos límites alimentan el `default` de un Query(ge=1, le=50), y FastAPI no
    # valida el default: las cotas tienen que estar acá o un .env fuera de rango
    # pasaría sin chistar cuando el FE llama sin query params.
    dashboard_widget_limit: int = Field(default=6, ge=1, le=50)
    dashboard_alerts_limit: int = Field(default=10, ge=1, le=50)


# `BaseSettings` values are provided from environment variables at runtime.
settings = Settings()  # type: ignore[call-arg]
