from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from alarm_simulator.data import DataStore
from alarm_simulator.queries import alarm_statistics, filter_alarms, search_assets
from alarm_simulator.routes.deps import get_store, require_asset
from alarm_simulator.schemas import (
    AlarmStatistics,
    AssetMetadata,
    AssetSearchHit,
    AssetSearchResponse,
    AssetSummary,
)

router = APIRouter(tags=["assets"])
Store = Annotated[DataStore, Depends(get_store)]


@router.get("/assets/search", response_model=AssetSearchResponse)
def search(
    store: Store,
    query: Annotated[str, Query(min_length=1, max_length=100, description="Asset name, id or type words")],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    site: str | None = None,
    unit: str | None = None,
    asset_type: str | None = None,
) -> AssetSearchResponse:
    hits = search_assets(store, query, site=site, unit=unit, asset_type=asset_type)
    return AssetSearchResponse(
        query=query,
        total=len(hits),
        results=[AssetSearchHit(**AssetSummary.of(a).model_dump(), match_score=s) for a, s in hits[:limit]],
    )


@router.get("/assets/{asset_id}/metadata", response_model=AssetMetadata)
def metadata(store: Store, asset_id: Annotated[str, Path(max_length=64)]) -> AssetMetadata:
    asset = require_asset(store, asset_id)
    related = [AssetSummary.of(store.assets_by_id[r]) for r in asset.related_assets if r in store.assets_by_id]
    codes = [d.alarm_code for d in store.definitions if d.asset_type == asset.asset_type]
    return AssetMetadata(
        **asset.model_dump(exclude={"related_assets"}),
        related_assets=related,
        alarm_codes=codes,
        alarm_statistics=AlarmStatistics.model_validate(alarm_statistics(filter_alarms(store, asset_ids=[asset_id]))),
    )
