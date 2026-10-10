from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpSettings(BaseSettings):
    """MCP server settings from MCP_* environment variables. Alarm API settings use ALARM_API_*."""

    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 9000
    auth_token: SecretStr | None = None  # clients must send 'Authorization: Bearer <token>'
    allow_unauthenticated: bool = False  # explicit opt-out for local experiments only
    allowed_hosts: list[str] = ["127.0.0.1:*", "localhost:*", "alarm-mcp:*"]  # DNS-rebinding protection
    log_level: str = "INFO"
