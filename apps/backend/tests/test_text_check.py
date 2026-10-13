"""The check on the model's text: every number and code it mentions must be in the evidence."""

import pytest

from copilot.text_check import support, unsupported

EVIDENCE = (
    "Boiler Feed Pump 101 (AST-BFP-101) had 24 alarms; the most frequent was High Vibration (PMP-VIB-HI). "
    "Latest ALM-000411 at 2026-06-28T18:36:00Z: value 11.07 mm/s vs setpoint 7.1. Trend 1.5 -> 2.1 per week (+43%)."
)


@pytest.mark.parametrize(
    "text",
    [
        "24 High Vibration alarms on Boiler Feed Pump 101",
        "rising 43% to 2.1 a week",
        "11.1 mm/s against a 7.1 mm/s limit",  # 11.07 rounded to one decimal
        "PMP-VIB-HI on AST-BFP-101 (ALM-000411)",
        "BFP\u2011101 is vibrating",  # a non-breaking hyphen, as some models write it
        "no numbers at all",
        "the latest alarm came at 18:36 on 28 June",  # a clock time from an ISO timestamp (T18:36)
    ],
)
def test_text_drawn_from_the_evidence_passes(text):
    assert unsupported(text, EVIDENCE) is None


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("31 alarms in 90 days", "number 31"),
        ("11.2 mm/s", "number 11.2"),
        ("the PMP-XYZ-HI alarm", "code PMP-XYZ-HI"),
    ],
)
def test_an_invented_number_or_code_is_named(text, problem):
    assert unsupported(text, EVIDENCE) == problem


def test_support_counts_the_numbers_and_codes_a_source_holds():
    fact = "In the last 90 days Boiler Feed Pump 101 had 37 alarms; the most frequent was High Vibration (PMP-VIB-HI)"
    assert support("24 alarms in 90 days", fact) == 1  # 90 is there, 24 is not
    assert support("Pump 101 had 37 PMP-VIB-HI alarms in 90 days", fact) == 4
    assert support("no numbers or codes", fact) == 0
