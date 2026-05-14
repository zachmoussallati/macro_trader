"""Event → instrument / series linkage rules.

Coarse mapping for Stage 2. Stage 4's empirical sensitivity layer refines
these — until then, rule-based mapping by event subject is enough for the
dashboard and signal masking to work.
"""

from __future__ import annotations

# Stable mapping from event subject (case-insensitive substring) to a tuple
# of affected ``instrument_id``s.
SUBJECT_TO_INSTRUMENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ----- Energy releases -----
    ("EIA Weekly Petroleum", ("CL", "BZ", "HO", "RB")),
    ("EIA Weekly Natural Gas", ("NG",)),
    ("EIA Short-Term Energy Outlook", ("CL", "BZ", "NG", "HO", "RB")),
    ("OPEC", ("CL", "BZ", "HO", "RB")),
    # ----- Agricultural releases -----
    ("USDA WASDE", ("ZC", "ZS", "ZW")),
    ("USDA Crop Progress", ("ZC", "ZS", "ZW")),
    ("USDA Grain Stocks", ("ZC", "ZS", "ZW")),
    ("USDA Prospective Plantings", ("ZC", "ZS", "ZW")),
    # ----- US macro -----
    ("US CPI", ("GC", "SI")),
    ("US Core CPI", ("GC", "SI")),
    ("US PCE", ("GC", "SI")),
    ("US Nonfarm Payrolls", ("GC", "SI")),
    ("US Unemployment", ("GC", "SI")),
    ("US GDP", ()),
    ("US Industrial Production", ("CL", "BZ", "HG", "ALI", "NG")),
    # ----- Central banks -----
    ("FOMC", ()),  # commodities affected indirectly; will populate when we add FX
    ("FOMC Decision", ()),
    ("ECB", ()),
    ("BoE", ()),
    # ----- Inventory / weather -----
    ("NOAA Drought", ("ZC", "ZS", "ZW")),
    ("Hurricane", ("CL", "BZ", "NG", "RB")),
)


def instruments_for_subject(subject: str) -> list[str]:
    """Return affected instruments for an event subject. Empty list if no
    rule matches (use case: events that affect not-yet-modelled asset
    classes)."""
    subject_lower = subject.lower()
    out: set[str] = set()
    for key, instruments in SUBJECT_TO_INSTRUMENTS:
        if key.lower() in subject_lower:
            out.update(instruments)
    return sorted(out)


def series_for_subject(subject: str) -> list[str]:
    """Stage 2 stub: subjects don't yet map to series. Stage 4 will populate
    based on the macro_data.series ``affected_instruments`` relation."""
    return []
