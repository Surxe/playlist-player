"""Current-line lookup, against the case table shared with the page's clock.js."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from playlist_player.lyrics import current_index, line_times, parse_lrc

CASES = json.loads((Path(__file__).parent.parent / "fixtures" / "timeline_cases.json").read_text())[
    "cases"
]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_shared_case_table(case):
    index = current_index(
        case["times"], case["position_ms"], lead_ms=case["lead_ms"], offset_ms=case["offset_ms"]
    )
    assert index == case["expected"]


def test_the_table_covers_the_cases_the_plan_names():
    names = " | ".join(case["name"] for case in CASES)
    for required in ("before the first", "exactly on", "between", "after the last",
                     "positive offset", "negative offset"):  # fmt: skip
        assert required in names


def test_lookup_over_parsed_lines():
    lines = parse_lrc("[00:01.00] one\n[00:02.00]\n[00:03.00] three\n")
    times = line_times(lines)
    assert times == [1000, 2000, 3000]
    assert lines[current_index(times, 2500)].text == ""  # inside the gap: show nothing
    assert lines[current_index(times, 3000)].text == "three"
