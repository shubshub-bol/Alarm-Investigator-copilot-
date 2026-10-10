"""App factory. Run with:  uvicorn alarm_simulator.main:create_app --factory --port 8000"""

import logging

from fastapi import Depends, FastAPI

from alarm_simulator import __version__
from alarm_simulator.auth import require_bearer_token
from alarm_simulator.config import DEMO_TOKEN, Settings
from alarm_simulator.data import DataStore
from alarm_simulator.errors import install_error_handlers
from alarm_simulator.observability import configure_logging, trace_middleware
from alarm_simulator.routes import alarms, analysis, analytics, assets, health

log = logging.getLogger("alarm_simulator")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    if settings.api_token == DEMO_TOKEN:
        log.warning("using the public demo token; set SIM_API_TOKEN to change it")

    app = FastAPI(title="Alarm Management API Simulator", version=__version__)
    app.state.settings = settings
    app.state.store = DataStore.load(settings.seed_data_path)

    install_error_handlers(app)
    app.middleware("http")(trace_middleware)

    # /health is public; everything else needs the bearer token.
    app.include_router(health.router)
    protected = [Depends(require_bearer_token)]
    for module in (assets, alarms, analysis, analytics):
        app.include_router(module.router, dependencies=protected)
    return app
