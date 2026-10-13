"""The evidence record: what each tool result writes into it, whoever made the call."""

from copilot.evidence import Evidence, NegativeFinding, clear_subject, record_result

PUMP = {
    "asset_id": "AST-BFP-101",
    "name": "Boiler Feed Pump 101",
    "asset_type": "pump",
    "site": "NorthPlant",
    "unit": "Unit 1",
    "criticality": "high",
}
OTHER = {**PUMP, "asset_id": "AST-BFP-102", "name": "Boiler Feed Pump 102"}
GROUPS = [{"key": {"alarm_code": f"C{n}"}, "kpis": {"alarm_count": n}} for n in range(8)]
SUMMARY = {"time_range": {"start_time": "a", "end_time": "b"}, "totals": {"alarm_count": 28}, "groups": GROUPS}


def fresh():
    return Evidence(intent="agent")


def advice(alarm_id, cause="wear", context=None):
    return {
        "alarm_id": alarm_id,
        "priority": {"score": 92.0, "priority": "P1", "priority_label": "Critical"},
        "likely_causes": [cause],
        "recommended_actions": [{"step": 1, "action": "Check"}],
        "source": "rules",
        "related_assets": [],
        "asset_context": context,
        "historical_pattern": None,
    }


def test_a_search_makes_assets_known_but_chooses_none():
    ev = fresh()
    record_result(ev, "search_assets", {"query": "pump"}, {"results": [{**PUMP, "match_score": 0.9}, OTHER]}, 1)
    assert set(ev.known_assets) == {"AST-BFP-101", "AST-BFP-102"}
    assert "match_score" not in ev.known_assets["AST-BFP-101"]
    assert ev.asset is None  # a search with several results never sets the question's asset


def test_a_call_about_one_known_asset_sets_the_questions_asset():
    ev = fresh()
    record_result(ev, "search_assets", {"query": "Boiler Feed Pump 101"}, {"results": [PUMP]}, 1)
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101", "status": "active"}, {"alarms": []}, 2)
    assert ev.asset == PUMP
    assert ev.active_alarms == []


def test_a_call_about_an_asset_not_yet_known_leaves_the_asset_open_until_it_is_known():
    ev = fresh()
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101"}, {"alarms": []}, 1)
    assert ev.asset is None and ev.focused_asset_ids == ["AST-BFP-101"]  # nothing known about it yet
    record_result(ev, "search_assets", {"query": "Boiler Feed Pump 101"}, {"results": [PUMP, OTHER]}, 2)
    assert ev.asset == PUMP  # the earlier call was about it alone, and now it is known


def test_a_later_call_about_another_asset_still_makes_it_a_comparison_once_both_are_known():
    ev = fresh()
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101"}, {"alarms": []}, 1)
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-102"}, {"alarms": []}, 2)
    record_result(ev, "search_assets", {"query": "pump"}, {"results": [PUMP, OTHER]}, 3)
    assert ev.asset is None


def test_metadata_gives_the_asset_and_its_related_assets():
    ev = fresh()
    meta = {**PUMP, "manufacturer": "Sulzer", "model": "HPT-200", "install_date": "2014", "related_assets": [OTHER]}
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, meta, 1)
    assert ev.asset == {**PUMP, "manufacturer": "Sulzer", "model": "HPT-200"}
    assert ev.related_assets == [OTHER]


def test_calls_about_two_different_assets_leave_the_questions_asset_open():
    ev = fresh()
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, {**PUMP, "related_assets": []}, 1)
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-102"}, {**OTHER, "related_assets": []}, 2)
    assert ev.asset is None  # a comparison: neither pump is "the" asset


def test_only_active_alarm_queries_set_the_active_list():
    ev = fresh()
    alarm = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101"}
    record_result(ev, "get_alarms", {"asset_id": "AST-BFP-101", "page_size": 1}, {"alarms": [alarm]}, 1)
    assert ev.active_alarms == []
    record_result(ev, "get_alarms", {"site": "NorthPlant", "status": "active"}, {"alarms": [alarm]}, 2)
    assert ev.active_alarms == [alarm]


def test_a_summary_and_a_trend_of_one_asset_are_recorded_but_not_a_comparison():
    ev = fresh()
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101", "AST-BFP-102"]}, SUMMARY, 1)
    assert ev.alarm_summary is None  # a comparison across assets
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101"]}, SUMMARY, 2)
    assert ev.alarm_summary == {
        "time_range": SUMMARY["time_range"],
        "totals": {"alarm_count": 28},
        "groups": GROUPS[:6],
    }
    trend = {
        "bucket": "weekly",
        "series": [{}, {}],
        "trend": {"direction": "increasing", "first_half_avg": 1.0, "second_half_avg": 2.0, "change_percent": 100.0},
    }
    record_result(ev, "get_alarm_trends", {"asset_ids": ["AST-BFP-101"], "bucket": "weekly"}, trend, 3)
    assert ev.trend == {**trend["trend"], "bucket": "weekly", "points": 2}


def test_a_summary_grouped_by_asset_is_a_comparison_even_with_one_asset():
    ev = fresh()
    by_asset = {"asset_ids": ["AST-BFP-101"], "group_by": ["asset_id", "asset_name", "alarm_code", "alarm_name"]}
    record_result(ev, "summarize_alarms", by_asset, SUMMARY, 1)
    assert ev.alarm_summary is None  # grouped per asset: it compares assets, it does not describe one
    assert ev.asset is None and ev.focused_asset_ids == []  # and it does not choose the question's asset
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101"], "group_by": ["alarm_code"]}, SUMMARY, 2)
    assert ev.alarm_summary is not None and ev.focused_asset_ids == ["AST-BFP-101"]


def test_the_apis_advice_its_priority_and_its_asset_are_recorded():
    ev = fresh()
    recs = advice("ALM-1", context={**PUMP, "install_date": "2014"})
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, recs, 4)
    assert ev.recommendations["recommended_actions"] == [{"step": 1, "action": "Check"}]
    assert ev.recommendations_step == 4  # the trace step whose fact states this advice
    assert ev.priority == {"score": 92.0, "priority": "P1", "priority_label": "Critical", "alarm_id": "ALM-1"}
    assert ev.asset == PUMP


def test_advice_without_an_asset_context_records_the_advice_and_chooses_no_asset():
    ev = fresh()
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, advice("ALM-1"), 2)
    assert ev.recommendations["likely_causes"] == ["wear"] and ev.recommendations_step == 2
    assert ev.asset is None and ev.known_assets == {} and ev.focused_asset_ids == []


def test_advice_for_the_same_alarm_again_is_the_latest_lookup():
    ev = fresh()
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, advice("ALM-1", "wear"), 2)
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, advice("ALM-1", "dirt"), 5)
    assert ev.recommendations["likely_causes"] == ["dirt"] and ev.recommendations_step == 5


def test_advice_for_two_different_alarms_leaves_the_advice_open():
    ev = fresh()
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, advice("ALM-1"), 2)
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-2", "x": 1}, advice("ALM-2", "heat"), 3)
    assert ev.recommendations is None and ev.recommendations_step is None  # neither alarm is "the" one
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, advice("ALM-1"), 4)
    assert ev.recommendations is None and ev.recommendations_step is None  # and it stays open


def test_a_score_sets_the_priority_only_when_none_is_known():
    ev = fresh()
    score = {"alarm_id": "ALM-1", "score": 80.0, "priority": "P2", "priority_label": "High", "factors": [], "alarm": {}}
    record_result(ev, "score_alarm_priority", {"alarm_id": "ALM-1"}, score, 1)
    assert ev.priority == {
        "alarm_id": "ALM-1",
        "score": 80.0,
        "priority": "P2",
        "priority_label": "High",
        "factors": [],
    }
    record_result(ev, "score_alarm_priority", {"alarm_id": "ALM-2"}, {**score, "alarm_id": "ALM-2", "score": 99.0}, 2)
    assert ev.priority["alarm_id"] == "ALM-1"  # which alarm matters most is a playbook's judgement


def test_an_alarms_details_give_the_focus_alarm_and_its_asset():
    ev = fresh()
    detail = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration", "asset": PUMP, "meta": {}}
    record_result(ev, "get_alarm_details", {"alarm_id": "ALM-1"}, detail, 1)
    assert ev.focus_alarm == {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration"}
    assert ev.asset == PUMP


def test_a_second_alarms_details_do_not_replace_the_focus_alarm():
    ev = fresh()
    first = {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101", "alarm_name": "High Vibration", "asset": PUMP}
    second = {"alarm_id": "ALM-2", "asset_id": "AST-BFP-102", "alarm_name": "Low Flow", "asset": OTHER}
    record_result(ev, "get_alarm_details", {"alarm_id": "ALM-1"}, first, 1)
    record_result(ev, "get_alarm_details", {"alarm_id": "ALM-2"}, second, 2)
    assert ev.focus_alarm["alarm_id"] == "ALM-1"  # the first one looked at stays the focus
    assert ev.asset is None  # but the two alarms are on different assets
    assert set(ev.known_assets) == {"AST-BFP-101", "AST-BFP-102"}


def test_other_tools_record_nothing_beyond_their_fact():
    ev = fresh()
    record_result(ev, "analyze_alarm_floods", {"asset_ids": ["AST-BFP-101"]}, {"flood_windows": []}, 1)
    assert ev == fresh()


def test_correlation_records_nothing_the_playbook_judges_its_pairs():
    ev = fresh()
    record_result(ev, "correlate_alarms", {"asset_ids": ["AST-BFP-101"]}, {"pairs": [{"relation": "precedes"}]}, 1)
    assert ev == fresh()


def test_clearing_the_subject_keeps_what_was_found_about_the_plant():
    ev = fresh()
    record_result(ev, "get_asset_metadata", {"asset_id": "AST-BFP-101"}, {**PUMP, "related_assets": [OTHER]}, 1)
    record_result(ev, "summarize_alarms", {"asset_ids": ["AST-BFP-101"]}, SUMMARY, 2)
    record_result(ev, "get_operator_recommendations", {"alarm_id": "ALM-1"}, advice("ALM-1", context=PUMP), 3)
    record_result(ev, "get_alarm_details", {"alarm_id": "ALM-1"}, {"alarm_id": "ALM-1", "asset_id": "AST-BFP-101"}, 4)
    ev.notes.append("a note")
    ev.missing.append("a gap")
    ev.negatives.append(NegativeFinding(text="No asset matches 'X'.", step=1, tool="search_assets"))
    clear_subject(ev)
    cleared = ("asset", "focus_alarm", "priority", "recommendations", "recommendations_step", "alarm_summary", "trend")
    assert all(getattr(ev, name) is None for name in cleared)
    assert (ev.related_assets, ev.focused_asset_ids, ev.advised_alarm_ids, ev.active_alarms) == ([], [], [], [])
    assert set(ev.known_assets) == {"AST-BFP-101"} and ev.notes == ["a note"] and ev.missing == ["a gap"]
    assert [n.text for n in ev.negatives] == ["No asset matches 'X'."]  # what was found about the plant stays


def test_a_saved_chat_from_before_still_loads():
    old = {"intent": "investigate_asset", "asset": PUMP}  # saved before known_assets existed
    assert Evidence.model_validate(old).known_assets == {}
