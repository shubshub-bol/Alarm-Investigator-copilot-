"""python -m copilot  - start the copilot backend API (reads .env)."""

import uvicorn

from copilot.api import create_app
from copilot.config import CopilotSettings

if __name__ == "__main__":
    settings = CopilotSettings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
