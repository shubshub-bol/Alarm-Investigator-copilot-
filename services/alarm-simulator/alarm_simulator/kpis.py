"""KPI and calculation-type catalogue exposed by GET /analytics/kpi-definitions."""

KPI_DEFINITIONS = [
    {"name": "alarm_count", "unit": "alarms", "description": "Number of alarms in the group."},
    {"name": "critical_count", "unit": "alarms", "description": "Number of critical-severity alarms in the group."},
    {
        "name": "recurring_rate",
        "unit": "ratio",
        "description": "Share of alarms whose asset+alarm code occurred more than once in the period.",
    },
    {
        "name": "avg_ack_delay",
        "unit": "minutes",
        "description": "Mean minutes from alarm start to operator acknowledgement (acknowledged alarms only).",
    },
    {
        "name": "suppression_candidate_rate",
        "unit": "ratio",
        "description": "Share of alarms that are chattering (cleared in under 2 minutes) or stale (active > 24 h).",
    },
]

CALCULATION_TYPES = [
    {
        "name": "alarm_flood_index",
        "unit": "%",
        "description": "Percentage of the period spent in alarm flood (more than 10 alarms in 10 minutes, ISA-18.2).",
    },
    {"name": "critical_alarm_density", "unit": "alarms/day", "description": "Critical alarms per day in the period."},
    {
        "name": "operator_response_efficiency",
        "unit": "%",
        "description": "Percentage of acknowledged alarms that were acknowledged within 10 minutes.",
    },
    {
        "name": "nuisance_alarm_score",
        "unit": "0-100",
        "description": "100 x (0.4 x chattering share + 0.3 x stale share + 0.3 x share of alarms repeating 5+ times).",
    },
]

KPI_NAMES = {k["name"] for k in KPI_DEFINITIONS}
CALCULATION_TYPE_NAMES = {c["name"] for c in CALCULATION_TYPES}
