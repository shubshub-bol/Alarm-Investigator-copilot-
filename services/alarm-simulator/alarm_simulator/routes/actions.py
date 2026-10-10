"""Operator recommendations and KPI calculation generate/execute."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from alarm_simulator import calculations
from alarm_simulator.analysis_schemas import (
    ExecuteRequest,
    ExecuteResponse,
    GenerateRequest,
    GenerateResponse,
    RecommendationRequest,
    RecommendationResponse,
)
from alarm_simulator.data import DataStore
from alarm_simulator.errors import ApiError
from alarm_simulator.recommendations import recommend
from alarm_simulator.routes.deps import get_store, require_asset

router = APIRouter(tags=["actions"])
Store = Annotated[DataStore, Depends(get_store)]


def get_registry(request: Request) -> calculations.CalculationRegistry:
    registry: calculations.CalculationRegistry = request.app.state.calculations
    return registry


Registry = Annotated[calculations.CalculationRegistry, Depends(get_registry)]


@router.post("/recommendations/operator-actions", response_model=RecommendationResponse)
def operator_actions(store: Store, req: RecommendationRequest) -> RecommendationResponse:
    alarm = store.alarms_by_id.get(req.alarm_id)
    if alarm is None:
        raise ApiError(404, "not_found", f"Alarm '{req.alarm_id}' not found.")
    return recommend(store, alarm, req)


@router.post("/calculation-code/generate", response_model=GenerateResponse)
def generate(store: Store, registry: Registry, req: GenerateRequest) -> GenerateResponse:
    for asset_id in req.filters.asset_ids or []:
        require_asset(store, asset_id)
    return registry.generate(req.calculation_type, req.filters)


@router.post("/calculation-code/execute", response_model=ExecuteResponse)
def execute(store: Store, registry: Registry, req: ExecuteRequest) -> ExecuteResponse:
    calc = registry.get(req.calculation_id)
    if calc is None:
        raise ApiError(404, "not_found", f"Calculation '{req.calculation_id}' not found. Generate it first.")
    filters = req.filters or calc.filters
    for asset_id in filters.asset_ids or []:
        require_asset(store, asset_id)
    return ExecuteResponse(
        calculation_id=calc.calculation_id,
        calculation_type=calc.calculation_type,
        filters=filters,
        result=calculations.execute(store, calc.calculation_type, filters),
        executed_at=datetime.now(UTC),
    )
