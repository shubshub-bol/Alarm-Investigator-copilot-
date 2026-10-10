"""KPI calculations: generate (describe + show code) then execute (run a vetted implementation).

Security: the generated code is shown to the caller for transparency only. It is NEVER passed
to eval/exec. Execution dispatches on calculation_type to the functions below.
"""

import inspect
import uuid
from collections import Counter, OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from alarm_simulator import analysis
from alarm_simulator.analysis_schemas import CalculationFilters, CalculationResult, GenerateResponse
from alarm_simulator.data import DataStore
from alarm_simulator.kpis import CALCULATION_TYPES
from alarm_simulator.models import Alarm
from alarm_simulator.queries import ack_delay_minutes, filter_alarms

MAX_STORED = 1000  # oldest calculations are evicted beyond this
ACK_TARGET_MINUTES = 10
DESCRIPTIONS = {c["name"]: c["description"] for c in CALCULATION_TYPES}


def _alarm_flood_index(alarms: list[Alarm], start: datetime, end: datetime) -> CalculationResult:
    floods = analysis.detect_floods(alarms, threshold=10, window_minutes=10)
    flood_minutes = sum(w.duration_minutes for w in floods)
    pct = round(flood_minutes / ((end - start).total_seconds() / 60) * 100, 4)
    verdict = "no floods" if not floods else ("acceptable (<1% of time)" if pct < 1 else "poor (>=1% of time)")
    return CalculationResult(
        value=pct,
        unit="%",
        interpretation=f"{len(floods)} flood episode(s); {verdict}.",
        details={
            "flood_episodes": len(floods),
            "alarms_in_floods": sum(w.alarm_count for w in floods),
            "total": len(alarms),
        },
    )


def _critical_alarm_density(alarms: list[Alarm], start: datetime, end: datetime) -> CalculationResult:
    days = (end - start).total_seconds() / 86400
    critical = sum(a.severity == "critical" for a in alarms)
    value = round(critical / days, 3)
    return CalculationResult(
        value=value,
        unit="alarms/day",
        interpretation=f"{critical} critical alarms over {round(days, 1)} days.",
        details={"critical_alarms": critical, "days": round(days, 2), "total": len(alarms)},
    )


def _operator_response_efficiency(alarms: list[Alarm], start: datetime, end: datetime) -> CalculationResult:
    delays = [d for a in alarms if (d := ack_delay_minutes(a)) is not None]
    within = sum(d <= ACK_TARGET_MINUTES for d in delays)
    value = round(within / len(delays) * 100, 1) if delays else 0.0
    verdict = "good" if value >= 90 else ("needs attention" if value >= 70 else "poor")
    return CalculationResult(
        value=value,
        unit="%",
        interpretation=f"{value}% of acknowledged alarms acknowledged within {ACK_TARGET_MINUTES} min ({verdict}).",
        details={
            "acknowledged": len(delays),
            "within_target": within,
            "unacknowledged": len(alarms) - len(delays),
            "target_minutes": ACK_TARGET_MINUTES,
        },
    )


def _nuisance_alarm_score(alarms: list[Alarm], start: datetime, end: datetime) -> CalculationResult:
    """score = 100 x (0.4 x chattering share + 0.3 x stale share + 0.3 x recurring share)."""
    n = len(alarms) or 1
    now = end
    counts = Counter((a.asset_id, a.alarm_code) for a in alarms)
    chattering = sum(analysis.is_chattering(a) for a in alarms)
    stale = sum(analysis.is_stale(a, now) for a in alarms)
    recurring = sum(counts[(a.asset_id, a.alarm_code)] >= 5 for a in alarms)
    value = round(100 * (0.4 * chattering / n + 0.3 * stale / n + 0.3 * recurring / n), 1)
    worst = counts.most_common(1)[0][0][1] if counts else None
    return CalculationResult(
        value=value,
        unit="0-100",
        interpretation=f"Nuisance score {value}/100 (higher = noisier).",
        details={
            "chattering": chattering,
            "stale": stale,
            "recurring": recurring,
            "total": len(alarms),
            "most_frequent_code": worst,
        },
    )


IMPLEMENTATIONS: dict[str, Callable[[list[Alarm], datetime, datetime], CalculationResult]] = {
    "alarm_flood_index": _alarm_flood_index,
    "critical_alarm_density": _critical_alarm_density,
    "operator_response_efficiency": _operator_response_efficiency,
    "nuisance_alarm_score": _nuisance_alarm_score,
}


class CalculationRegistry:
    """In-memory store of generated calculations, bounded to MAX_STORED entries."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, GenerateResponse] = OrderedDict()

    def generate(self, calculation_type: str, filters: CalculationFilters) -> GenerateResponse:
        calc = GenerateResponse(
            calculation_id=f"calc-{uuid.uuid4().hex[:12]}",
            calculation_type=calculation_type,  # type: ignore[arg-type]
            description=DESCRIPTIONS[calculation_type],
            language="python",
            code=inspect.getsource(IMPLEMENTATIONS[calculation_type]),
            executable=False,
            filters=filters,
            created_at=datetime.now(UTC),
        )
        self._items[calc.calculation_id] = calc
        while len(self._items) > MAX_STORED:
            self._items.popitem(last=False)
        return calc

    def get(self, calculation_id: str) -> GenerateResponse | None:
        return self._items.get(calculation_id)


def execute(store: DataStore, calculation_type: str, filters: CalculationFilters) -> CalculationResult:
    end = filters.end_time or store.reference_now
    start = filters.start_time or end - timedelta(days=90)
    alarms = filter_alarms(
        store, asset_ids=filters.asset_ids, site=filters.site, unit=filters.unit, start=start, end=end
    )
    return IMPLEMENTATIONS[calculation_type](alarms, start, end)
