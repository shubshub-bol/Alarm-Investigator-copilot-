from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "seed_data.json"
DEMO_TOKEN = "demo-token"  # noqa: S105 - public token from the supplied Postman collections, not a secret


class Settings(BaseSettings):
    """Simulator settings. Read from environment variables prefixed with SIM_ (e.g. SIM_API_TOKEN)."""

    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    api_token: str = DEMO_TOKEN
    seed_data_path: Path = DEFAULT_SEED_PATH
    host: str = "0.0.0.0"  # noqa: S104 - binding all interfaces is intended inside a container
    port: int = 8000
    log_level: str = "INFO"
