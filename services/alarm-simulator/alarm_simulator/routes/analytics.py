from fastapi import APIRouter
from pydantic import BaseModel

from alarm_simulator.kpis import CALCULATION_TYPES, KPI_DEFINITIONS

router = APIRouter(tags=["analytics"])


class KpiDefinition(BaseModel):
    name: str
    unit: str
    description: str


class KpiDefinitionsResponse(BaseModel):
    kpis: list[KpiDefinition]
    calculation_types: list[KpiDefinition]


@router.get("/analytics/kpi-definitions", response_model=KpiDefinitionsResponse)
def kpi_definitions() -> KpiDefinitionsResponse:
    return KpiDefinitionsResponse(
        kpis=[KpiDefinition(**k) for k in KPI_DEFINITIONS],
        calculation_types=[KpiDefinition(**c) for c in CALCULATION_TYPES],
    )
