from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from alarm_simulator import __version__
from alarm_simulator.data import DataStore
from alarm_simulator.routes.deps import get_store

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    reference_now: str
    data: dict[str, int]


@router.get("/health", response_model=HealthResponse)
def health(store: Annotated[DataStore, Depends(get_store)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="alarm-api-simulator",
        version=__version__,
        reference_now=store.reference_now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        data={"sites": len(store.sites), "assets": len(store.assets), "alarms": len(store.alarms)},
    )
