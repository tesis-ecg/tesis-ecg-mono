from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str
    s3_bucket_name: str
    s3_endpoint_url: str = ""
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-east-1"
    environment: str = "development"


# `BaseSettings` values are provided from environment variables at runtime.
settings = Settings()  # type: ignore[call-arg]
