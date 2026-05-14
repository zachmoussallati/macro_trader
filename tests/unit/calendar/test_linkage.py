"""Unit tests for calendar linkage rules."""

from __future__ import annotations

import pytest

from macro_trader.calendar.linkage import instruments_for_subject


@pytest.mark.unit
@pytest.mark.parametrize(
    "subject,expected",
    [
        ("EIA Weekly Petroleum Status Report", ["BZ", "CL", "HO", "RB"]),
        ("EIA Weekly Natural Gas Storage", ["NG"]),
        ("USDA WASDE", ["ZC", "ZS", "ZW"]),
        ("US CPI Release", ["GC", "SI"]),
        ("FOMC Decision", []),  # commodities affected indirectly
        ("Random subject with no rule", []),
    ],
)
def test_instruments_for_subject(subject: str, expected: list[str]) -> None:
    assert instruments_for_subject(subject) == expected


@pytest.mark.unit
def test_linkage_case_insensitive() -> None:
    assert instruments_for_subject("eia weekly petroleum status") == [
        "BZ",
        "CL",
        "HO",
        "RB",
    ]
