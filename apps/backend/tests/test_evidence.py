"""The evidence record: what each tool result writes into it, whoever made the call."""

from copilot.evidence import Evidence, record_result

PUMP = {
    "asset_id": "AST-BFP-101",
    "name": "Boiler Feed Pump 101",
    "asset_type": "pump",
    "site": "NorthPlant",
    "unit": "Unit 1",
    "criticality": "high",
}
OTHER = {**PUMP, "asset_id": "AST-BFP-102", "name": "Boiler Feed Pump 102"}


def fresh():
    return Evidence(intent="agent")


def test_a_search_makes_assets_known_but_chooses_none():
    ev = fresh()
    record_result(ev, "search_assets", {"query": "pump"}, {"results": [{**PUMP, "match_score": 0.9}, OTHER]})
    assert set(ev.known_assets) == {"AST-BFP-101", "AST-BFP-102"}
    assert "match_score" not in ev.known_assets["AST-BFP-101"]
    assert ev.asset is None  # a search with several results never sets the question's asset


def test_a_call_about_one_known_asset_sets_the_questions_asset():
    ev = fresh()
    record_result(ev, "search_assets", {"query": "Boiler Feed Pump 101"}, {"results": [PUMP]})
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101", "status": "active"}, {"alarms": []})
    assert ev.asset == PUMP
    assert ev.active_alarms == []


def test_metadata_gives_the_asset_and_its_related_assets():
    ev = fresh()
    meta = {**PUMP, "manufacturer": "Sulzer", "model": "HPT-200", "install_date": "2014", "related_assets": [OTHER]}
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, meta)
    assert ev.asset == {**PUMP, "manufacturer": "Sulzer", "model": "HPT-200"}
    assert ev.related_assets == [OTHER]


def test_calls_about_two_different_assets_leave_the_questions_asset_open():
    ev = fresh()
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, {**PUMP, "related_assets": []})
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-102"}, {**OTHER, "related_assets": []})
    assert ev.asset is None  # a comparison: neither pump is "the" asset


def test_only_active_alarm_queries_set_the_active_list():
    ev = fresh()
    alarm = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101"}
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101", "page_size": 1}, {"alarms": [alarm]})
    assert ev.active_alarms == []
    record_result(ev, "get_alarms", {"site": "NorthPlant", "status": "active"}, {"alarms": [alarm]})
    assert ev.active_alarms == [alarm]


def test_a_summary_and_a_trend_of_one_asset_are_recorded_but_not_a_comparison():
    ev = fresh()
    groups = [{"key": {"alarm_code": f"C{n}"}, "kpis": {"alarm_count": n}} for n in range(8)]
    summary = {"time_range": {"start_time": "a", "end_time": "b"}, "totals": {"alarm_count": 28}, "groups": groups}
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101", "AST-BFP-102"]}, summary)
    assert ev.alarm_summary is None  # a comparison across assets
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101"]}, summary)
    assert ev.alarm_summary == {
        "time_range": summary["time_range"],
        "totals": {"alarm_count": 28},
        "groups": groups[:6],
    }
    trend = {
        "bucket": "weekly",
        "series": [{}, {}],
        "trend": {"direction": "increasing", "first_half_avg": 1.0, "second_half_avg": 2.0, "change_percent": 100.0},
    }
    record_result(ev, "get_alarm_trends", {"asset_ids": ["AST-BFP-101"], "bucket": "weekly"}, trend)
    assert ev.trend == {**trend["trend"], "bucket": "weekly", "points": 2}


def test_the_apis_advice_its_priority_and_its_asset_are_recorded():
    ev = fresh()
    recs = {
        "alarm_id": "ALM-1",
        "priority": {"score": 92.0, "priority": "P1", "priority_label": "Critical"},
        "likely_causes": ["wear"],
        "recommended_actions": [{"step": 1, "action": "Check"}],
        "source": "rules",
        "related_assets": [],
        "asset_context": {**PUMP, "install_date": "2014"},
        "historical_pattern": None,
    }
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, recs)
    assert ev.recommendations["recommended_actions"] == [{"step": 1, "action": "Check"}]
    assert ev.priority == {"score": 92.0, "priority": "P1", "priority_label": "Critical", "alarm_id": "ALM-1"}
    assert ev.asset == PUMP


def test_a_score_sets_the_priority_only_when_none_is_known():
    ev = fresh()
    score = {"alarm_id": "ALM-1", "score": 80.0, "priority": "P2", "priority_label": "High", "factors": [], "alarm": {}}
    record_result(ev, "score_alarm_priority", {"alarm_id": "ALM-1"}, score)
    assert ev.priority == {
        "alarm_id": "ALM-1",
        "score": 80.0,
        "priority": "P2",
        "priority_label": "High",
        "factors": [],
    }
    record_result(ev, "score_alarm_priority", {"alarm_id": "ALM-2"}, {**score, "alarm_id": "ALM-2", "score": 99.0})
    assert ev.priority["alarm_id"] == "ALM-1"  # which alarm matters most is a playbook's judgement


def test_an_alarms_details_give_the_focus_alarm_and_its_asset():
    ev = fresh()
    detail = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration", "asset": PUMP, "meta": {}}
    record_result(ev, "get_alarm_details", {"alarm_id": "ALM-1"}, detail)
    assert ev.focus_alarm == {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration"}
    assert ev.asset == PUMP


def test_other_tools_record_nothing_beyond_their_fact():
    ev = fresh()
    record_result(ev, "analyze_alarm_floods", {"asset_ids": ["AST-BFP-101"]}, {"flood_windows": []})
    assert ev == fresh()


def test_a_saved_chat_from_before_still_loads():
    old = {"intent": "investigate_asset", "asset": PUMP}  # saved before known_assets existed
    assert Evidence.model_validate(old).known_assets == {}
