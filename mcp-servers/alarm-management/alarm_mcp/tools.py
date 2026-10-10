"""MCP tool definitions for the Alarm Management API.

Every tool: validated typed inputs -> Alarm API call(s) through the connector -> output validated
against a contract in schemas.py -> result that carries the trace id and API call log.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from alarm_mcp.runtime import AppContext, ToolRun
from alarm_mcp.schemas import (
    AlarmDetailOutput,
    AlarmListOutput,
    AssetMetadataOutput,
    CorrelationOutput,
    FloodOutput,
    KpiDefinitionsOutput,
    KpiOutput,
    PriorityOutput,
    RationalizationOutput,
    RecommendationOutput,
    SearchAssetsOutput,
    Severity,
    SummaryOutput,
    TrendsOutput,
)

ID_PATTERN = r"^[A-Za-z0-9._-]{1,64}$"
READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)

# ---------------------------------------------------------------- shared input types
AssetId = Annotated[str, Field(pattern=ID_PATTERN, description="Asset id from search_assets, e.g. 'AST-BFP-101'")]
AlarmId = Annotated[str, Field(pattern=ID_PATTERN, description="Alarm id from get_alarms, e.g. 'ALM-000123'")]
AssetIds = Annotated[list[AssetId] | None, Field(min_length=1, max_length=50, description="Limit to these asset ids")]
Site = Annotated[str | None, Field(max_length=64, description="Site name, e.g. 'NorthPlant' or 'EastRefinery'")]
Unit = Annotated[str | None, Field(max_length=64, description="Unit name, e.g. 'Unit 1'")]
StartTime = Annotated[datetime | None, Field(description="ISO-8601 start (inclusive). Alternative to lookback_days.")]
EndTime = Annotated[datetime | None, Field(description="ISO-8601 end (exclusive). Alternative to lookback_days.")]
LookbackDays = Annotated[
    int | None,
    Field(ge=1, le=365, description="Analyse the last N days of data, measured from the Alarm API's data clock."),
]
Severities = Annotated[list[Severity] | None, Field(description="Only these severities")]
AlarmCodes = Annotated[list[Annotated[str, Field(pattern=ID_PATTERN)]] | None, Field(max_length=20)]
KpiName = Literal["alarm_count", "critical_count", "recurring_rate", "avg_ack_delay", "suppression_candidate_rate"]
GroupField = Literal["alarm_name", "alarm_code", "alarm_type", "asset_id", "asset_name", "severity", "site", "unit"]
CalculationType = Literal[
    "alarm_flood_index", "critical_alarm_density", "operator_response_efficiency", "nuisance_alarm_score"
]


def _run(ctx: Context[AppContext], tool: str) -> ToolRun:
    return ToolRun(ctx.request_context.lifespan_context, tool, ctx.request_context.meta)


def _scope(asset_ids: list[str] | None, site: str | None, unit: str | None, time_range: dict | None) -> dict:
    body = {"asset_ids": asset_ids, "site": site, "unit": unit, "time_range": time_range}
    return {k: v for k, v in body.items() if v is not None}


def register_tools(mcp: MCPServer) -> None:  # noqa: C901 - one flat list of tool definitions
    # ------------------------------------------------------------------- assets
    @mcp.tool(title="Search assets", annotations=READ_ONLY)
    async def search_assets(
        query: Annotated[str, Field(min_length=1, max_length=100, description="Name, id or type words")],
        ctx: Context[AppContext],
        limit: Annotated[int, Field(ge=1, le=50)] = 5,
        site: Site = None,
        unit: Unit = None,
        asset_type: Annotated[str | None, Field(max_length=32, description="e.g. pump, motor, compressor")] = None,
    ) -> SearchAssetsOutput:
        """Resolve an asset name (e.g. 'Boiler Feed Pump 101') to asset ids, best match first.
        Call this first whenever the user names equipment; pass the returned asset_id to other tools."""
        run = _run(ctx, "search_assets")
        data = await run.api(run.client.search_assets, query, limit=limit, site=site, unit=unit, asset_type=asset_type)
        return run.output(SearchAssetsOutput, data)

    @mcp.tool(title="Get asset metadata", annotations=READ_ONLY)
    async def get_asset_metadata(asset_id: AssetId, ctx: Context[AppContext]) -> AssetMetadataOutput:
        """Asset details: type, site/unit, criticality, manufacturer, the RELATED assets connected to it
        (drive motor, upstream vessel, valves...), its possible alarm codes and alarm statistics."""
        run = _run(ctx, "get_asset_metadata")
        return run.output(AssetMetadataOutput, await run.api(run.client.get_asset_metadata, asset_id))

    # ------------------------------------------------------------------- alarms
    @mcp.tool(title="Get alarms", annotations=READ_ONLY)
    async def get_alarms(
        ctx: Context[AppContext],
        asset_id: AssetId | None = None,
        site: Site = None,
        unit: Unit = None,
        status: Literal["active", "cleared"] | None = None,
        severity: Severities = None,
        alarm_codes: AlarmCodes = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=200)] = 50,
        sort_by: Literal["start_time", "severity", "asset_id", "alarm_id"] = "start_time",
        sort_order: Literal["asc", "desc"] = "desc",
        fetch_all: Annotated[bool, Field(description="Follow pagination and return every match")] = False,
        max_items: Annotated[int, Field(ge=1, le=2000, description="Cap when fetch_all is true")] = 500,
    ) -> AlarmListOutput:
        """List alarm events with filters (asset, site, unit, status, severity, alarm code, time window).
        Use status='active' for alarms that are still on. Newest first by default."""
        run = _run(ctx, "get_alarms")
        time_range = await run.time_range(start_time, end_time, lookback_days) or {}
        filters: dict[str, Any] = {
            "asset_id": asset_id,
            "site": site,
            "unit": unit,
            "status": status,
            "severity": severity,
            "alarm_code": alarm_codes,
            "sort_by": sort_by,
            "sort_order": sort_order,
            **time_range,
        }
        if fetch_all:
            result = await run.all_pages(page_size=page_size, max_items=max_items, **filters)
            data = {
                "alarms": result.items,
                "total_items": result.total_items,
                "page": None,
                "has_next": result.truncated,
                "pages_fetched": result.pages_fetched,
                "truncated": result.truncated,
            }
        else:
            body = await run.api(run.client.list_alarms, page=page, page_size=page_size, **filters)
            pagination = body["pagination"]
            data = {
                "alarms": body["data"],
                "total_items": pagination["total_items"],
                "page": pagination["page"],
                "has_next": pagination["has_next"],
                "pages_fetched": 1,
                "truncated": pagination["has_next"],
            }
        return run.output(AlarmListOutput, data)

    @mcp.tool(title="Get alarm details", annotations=READ_ONLY)
    async def get_alarm_details(alarm_id: AlarmId, ctx: Context[AppContext]) -> AlarmDetailOutput:
        """One alarm with its duration, minutes-to-acknowledge and asset summary."""
        run = _run(ctx, "get_alarm_details")
        return run.output(AlarmDetailOutput, await run.api(run.client.get_alarm, alarm_id))

    # ----------------------------------------------------------------- analysis
    @mcp.tool(title="Summarize alarms", annotations=READ_ONLY)
    async def summarize_alarms(
        ctx: Context[AppContext],
        asset_ids: AssetIds = None,
        site: Site = None,
        unit: Unit = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
        severity: Severities = None,
        alarm_types: list[Literal["process", "safety", "device"]] | None = None,
        group_by: Annotated[list[GroupField], Field(max_length=4)] = ["alarm_name"],  # noqa: B006 - copied by pydantic
        kpis: Annotated[list[KpiName], Field(min_length=1)] = ["alarm_count", "recurring_rate", "avg_ack_delay"],  # noqa: B006
    ) -> SummaryOutput:
        """Count alarms and compute KPIs (count, critical count, recurring rate, average minutes to
        acknowledge, suppression-candidate rate), grouped e.g. by alarm_name. Default window: 90 days."""
        run = _run(ctx, "summarize_alarms")
        body = _scope(asset_ids, site, unit, await run.time_range(start_time, end_time, lookback_days))
        body.update({"group_by": group_by, "kpis": kpis})
        if severity:
            body["severity"] = severity
        if alarm_types:
            body["alarm_types"] = alarm_types
        return run.output(SummaryOutput, await run.api(run.client.alarm_summary, body))

    @mcp.tool(title="Get alarm trends", annotations=READ_ONLY)
    async def get_alarm_trends(
        ctx: Context[AppContext],
        asset_ids: AssetIds = None,
        site: Site = None,
        unit: Unit = None,
        alarm_codes: AlarmCodes = None,
        severity: Severities = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
        bucket: Literal["hourly", "daily", "weekly"] = "weekly",
        metrics: Annotated[list[KpiName], Field(min_length=1)] = ["alarm_count"],  # noqa: B006
    ) -> TrendsOutput:
        """Alarm counts per time bucket and whether they are increasing, decreasing or stable
        (first half vs second half). Filter by alarm_codes to ask 'is THIS alarm getting worse?'."""
        run = _run(ctx, "get_alarm_trends")
        body = _scope(asset_ids, site, unit, await run.time_range(start_time, end_time, lookback_days))
        body.update({"bucket": bucket, "metrics": metrics})
        if alarm_codes:
            body["alarm_codes"] = alarm_codes
        if severity:
            body["severity"] = severity
        return run.output(TrendsOutput, await run.api(run.client.alarm_trends, body))

    @mcp.tool(title="Correlate alarms", annotations=READ_ONLY)
    async def correlate_alarms(
        asset_ids: Annotated[list[AssetId], Field(min_length=1, max_length=20)],
        ctx: Context[AppContext],
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
        lag_window_minutes: Annotated[int, Field(ge=1, le=240)] = 15,
        severity_threshold: Severity = "low",
        min_support: Annotated[int, Field(ge=1, le=100)] = 2,
    ) -> CorrelationOutput:
        """Find alarms that fire together within lag_window_minutes and which one usually comes FIRST
        (a likely contributing factor). Pass the asset plus its related_assets from get_asset_metadata."""
        run = _run(ctx, "correlate_alarms")
        body = _scope(asset_ids, None, None, await run.time_range(start_time, end_time, lookback_days))
        body.update(
            {
                "correlation_method": "cooccurrence",
                "lag_window_minutes": lag_window_minutes,
                "severity_threshold": severity_threshold,
                "min_support": min_support,
            }
        )
        return run.output(CorrelationOutput, await run.api(run.client.correlate_alarms, body))

    @mcp.tool(title="Analyze alarm floods", annotations=READ_ONLY)
    async def analyze_alarm_floods(
        ctx: Context[AppContext],
        asset_ids: AssetIds = None,
        site: Site = None,
        unit: Unit = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
        threshold_count: Annotated[int, Field(ge=2, le=1000)] = 10,
        rolling_window_minutes: Annotated[int, Field(ge=1, le=1440)] = 10,
    ) -> FloodOutput:
        """Detect alarm floods (more than threshold_count alarms within rolling_window_minutes; ISA-18.2
        uses 10 in 10) and the alarm that initiated each flood."""
        run = _run(ctx, "analyze_alarm_floods")
        body = _scope(asset_ids, site, unit, await run.time_range(start_time, end_time, lookback_days))
        body.update({"threshold_count": threshold_count, "rolling_window_minutes": rolling_window_minutes})
        return run.output(FloodOutput, await run.api(run.client.flood_analysis, body))

    @mcp.tool(title="Find rationalization candidates", annotations=READ_ONLY)
    async def find_rationalization_candidates(
        ctx: Context[AppContext],
        asset_ids: AssetIds = None,
        site: Site = None,
        unit: Unit = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
        recurrence_threshold: Annotated[int, Field(ge=2, le=1000)] = 5,
        stale_minutes_threshold: Annotated[int, Field(ge=1)] = 1440,
        chatter_seconds_threshold: Annotated[int, Field(ge=1, le=3600)] = 120,
    ) -> RationalizationOutput:
        """Rank recurring, chattering, stale or mostly-unacknowledged alarms, with a score and a suggested
        action. Safety alarms are never marked suppression-eligible."""
        run = _run(ctx, "find_rationalization_candidates")
        body = _scope(asset_ids, site, unit, await run.time_range(start_time, end_time, lookback_days))
        body.update(
            {
                "recurrence_threshold": recurrence_threshold,
                "stale_minutes_threshold": stale_minutes_threshold,
                "chatter_seconds_threshold": chatter_seconds_threshold,
            }
        )
        return run.output(RationalizationOutput, await run.api(run.client.rationalization_candidates, body))

    # ----------------------------------------------------- priority & guidance
    @mcp.tool(title="Score alarm priority", annotations=READ_ONLY)
    async def score_alarm_priority(alarm_id: AlarmId, ctx: Context[AppContext]) -> PriorityOutput:
        """Priority 0-100 and band P1-P4 for one alarm, with the factors that produced it (severity,
        asset criticality, active/acknowledged state, recurrence, concurrent alarms nearby)."""
        run = _run(ctx, "score_alarm_priority")
        return run.output(PriorityOutput, await run.api(run.client.priority_score, alarm_id))

    @mcp.tool(title="Get operator recommendations", annotations=READ_ONLY)
    async def get_operator_recommendations(
        alarm_id: AlarmId,
        ctx: Context[AppContext],
        include_related: bool = True,
        include_asset_context: bool = True,
        include_historical_pattern: bool = True,
    ) -> RecommendationOutput:
        """The Alarm API's likely causes and recommended operator actions for one alarm, with related-asset
        status and 90-day history. This guidance comes from the API catalogue and MUST be checked
        against the site operating procedures before it is presented as instructions."""
        run = _run(ctx, "get_operator_recommendations")
        data = await run.api(
            run.client.operator_recommendations,
            alarm_id,
            include_related=include_related,
            include_asset_context=include_asset_context,
            include_historical_pattern=include_historical_pattern,
        )
        return run.output(RecommendationOutput, data)

    # --------------------------------------------------------------------- KPIs
    @mcp.tool(title="Calculate KPI", annotations=READ_ONLY)
    async def calculate_kpi(
        calculation_type: CalculationType,
        ctx: Context[AppContext],
        asset_ids: AssetIds = None,
        site: Site = None,
        unit: Unit = None,
        start_time: StartTime = None,
        end_time: EndTime = None,
        lookback_days: LookbackDays = None,
    ) -> KpiOutput:
        """Generate and then execute a KPI calculation (two API calls) and return the value with its
        interpretation. Types: alarm_flood_index, critical_alarm_density, operator_response_efficiency,
        nuisance_alarm_score."""
        run = _run(ctx, "calculate_kpi")
        time_range = await run.time_range(start_time, end_time, lookback_days) or {}
        filters = {k: v for k, v in {"asset_ids": asset_ids, "site": site, "unit": unit, **time_range}.items() if v}
        generated = await run.api(run.client.generate_calculation, calculation_type, filters)
        executed = await run.api(run.client.execute_calculation, generated["calculation_id"])
        data = {
            "calculation_id": generated["calculation_id"],
            "calculation_type": generated["calculation_type"],
            "description": generated["description"],
            "filters": executed["filters"],
            **executed["result"],
        }
        return run.output(KpiOutput, data)

    @mcp.tool(title="List KPI definitions", annotations=READ_ONLY)
    async def list_kpi_definitions(ctx: Context[AppContext]) -> KpiDefinitionsOutput:
        """The KPIs and calculation types the Alarm API supports, with units and definitions."""
        run = _run(ctx, "list_kpi_definitions")
        return run.output(KpiDefinitionsOutput, await run.api(run.client.kpi_definitions))
