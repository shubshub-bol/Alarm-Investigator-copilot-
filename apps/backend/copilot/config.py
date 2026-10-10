from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CopilotSettings(BaseSettings):
    """Backend settings from the environment / .env (names match .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    host: str = Field("127.0.0.1", validation_alias=AliasChoices("COPILOT_HOST", "host"))
    port: int = Field(8080, validation_alias=AliasChoices("COPILOT_PORT", "port"))
    cors_origins: list[str] = Field(
        ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
        validation_alias=AliasChoices("COPILOT_CORS_ORIGINS", "cors_origins"),
    )
    mcp_server_url: str = Field(
        "http://localhost:9000/mcp", validation_alias=AliasChoices("MCP_SERVER_URL", "mcp_server_url")
    )
    mcp_auth_token: SecretStr | None = Field(None, validation_alias=AliasChoices("MCP_AUTH_TOKEN", "mcp_auth_token"))
    llm_provider: Literal["gemini", "offline"] = Field(
        "offline", validation_alias=AliasChoices("LLM_PROVIDER", "llm_provider")
    )
    llm_model: str = Field("gemini-flash-latest", validation_alias=AliasChoices("LLM_MODEL", "llm_model"))
    llm_api_key: SecretStr | None = Field(None, validation_alias=AliasChoices("LLM_API_KEY", "llm_api_key"))
    llm_thinking_level: str | None = Field(
        "LOW", validation_alias=AliasChoices("LLM_THINKING_LEVEL", "llm_thinking_level")
    )
    db_path: Path = Field(Path(".data/copilot.sqlite3"), validation_alias=AliasChoices("COPILOT_DB_PATH", "db_path"))
    log_level: str = Field("INFO", validation_alias=AliasChoices("LOG_LEVEL", "log_level"))
