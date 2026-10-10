"""python -m alarm_simulator"""

import uvicorn

from alarm_simulator.config import Settings
from alarm_simulator.main import create_app

if __name__ == "__main__":
    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning")
