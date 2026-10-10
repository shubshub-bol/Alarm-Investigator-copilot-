from fastapi import Request

from alarm_simulator.data import DataStore
from alarm_simulator.errors import ApiError
from alarm_simulator.models import Asset


def get_store(request: Request) -> DataStore:
    store: DataStore = request.app.state.store
    return store


def require_asset(store: DataStore, asset_id: str) -> Asset:
    asset = store.assets_by_id.get(asset_id)
    if asset is None:
        raise ApiError(404, "not_found", f"Asset '{asset_id}' not found.")
    return asset


def split_csv(values: list[str] | None) -> list[str] | None:
    """Accept both ?severity=high&severity=critical and ?severity=high,critical."""
    if not values:
        return None
    return [v.strip() for raw in values for v in raw.split(",") if v.strip()]


def check_allowed(name: str, values: list[str] | None, allowed: set[str]) -> None:
    bad = sorted(set(values or []) - allowed)
    if bad:
        raise ApiError(
            422,
            "validation_error",
            f"Unknown {name}: {', '.join(bad)}.",
            {"problems": [{"field": name, "message": f"allowed values: {', '.join(sorted(allowed))}"}]},
        )
