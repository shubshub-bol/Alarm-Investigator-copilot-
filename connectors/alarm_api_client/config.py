from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClientSettings(BaseSettings):
    """Connector settings from ALARM_API_* environment variables (see .env.example).

    The token is a SecretStr, so it prints as '**********' in reprs, logs and dumps.
    """

    model_config = SettingsConfigDict(env_prefix="ALARM_API_", env_file=".env", extra="ignore")

    base_url: str = "http://localhost:8000"
    token: SecretStr
    timeout_seconds: float = Field(10.0, gt=0, le=120)
    max_retries: int = Field(3, ge=0, le=10)
    backoff_seconds: float = Field(0.5, ge=0, le=10)
    max_backoff_seconds: float = Field(8.0, ge=0, le=60)
    client_id: str = "alarm-mcp-server"
