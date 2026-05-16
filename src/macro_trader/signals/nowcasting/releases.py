"""Release definitions for the nowcasting signal family.

Each release has:

- ``release_id``: stable string key.
- ``target_fred``: FRED series ID for the release value (e.g. PAYEMS).
- ``lead_indicators``: tuple of FRED series IDs whose values are
  available before the release lands.
- ``affected_instruments``: tuple of instrument IDs that respond
  to surprises in this release.
- ``frequency``: how often the release lands (used for the consensus
  lookup window).

Six releases ship in Stage 5. Adding a new release is a one-tuple
addition; the rest of the pipeline picks it up automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Frequency = Literal["monthly", "weekly", "quarterly"]


@dataclass(slots=True, frozen=True)
class ReleaseSpec:
    release_id: str
    target_fred: str
    lead_indicators: tuple[str, ...]
    affected_instruments: tuple[str, ...]
    frequency: Frequency
    name: str
    # Whether to expect a `consensus` value in calendar metadata. Most
    # high-impact releases do; weekly EIA does not.
    has_consensus: bool = True
    references: tuple[str, ...] = field(default_factory=tuple)


DEFAULT_RELEASES: tuple[ReleaseSpec, ...] = (
    ReleaseSpec(
        release_id="NFP",
        target_fred="FRED:PAYEMS",
        lead_indicators=("FRED:ICSA", "FRED:UNRATE"),
        affected_instruments=("GC", "SI"),
        frequency="monthly",
        name="Nonfarm Payrolls",
    ),
    ReleaseSpec(
        release_id="CPI",
        target_fred="FRED:CPIAUCSL",
        lead_indicators=("FRED:DCOILWTICO", "FRED:DTWEXBGS"),
        affected_instruments=("GC", "SI"),
        frequency="monthly",
        name="Consumer Price Index",
    ),
    ReleaseSpec(
        release_id="ISM_MFG",
        target_fred="FRED:NAPMPMI",
        lead_indicators=("FRED:INDPRO", "FRED:UNRATE"),
        affected_instruments=("HG", "ALI", "CL"),
        frequency="monthly",
        name="ISM Manufacturing PMI",
    ),
    ReleaseSpec(
        release_id="RETAIL_SALES",
        target_fred="FRED:RSAFS",
        lead_indicators=("FRED:UMCSENT",),
        affected_instruments=(),
        frequency="monthly",
        name="Advance Retail Sales",
    ),
    ReleaseSpec(
        release_id="GDP",
        target_fred="FRED:GDPC1",
        lead_indicators=("FRED:INDPRO", "FRED:PAYEMS", "FRED:RSAFS"),
        affected_instruments=(),
        frequency="quarterly",
        name="GDP Advance",
    ),
    ReleaseSpec(
        release_id="EIA_PETROLEUM",
        target_fred="EIA:PET.WCRSTUS1.W",  # treated as the target even though it's an alt-data series
        lead_indicators=(),
        affected_instruments=("CL", "BZ"),
        frequency="weekly",
        name="EIA Weekly Petroleum Status",
        has_consensus=False,  # EIA weekly doesn't have a consensus row
    ),
)


__all__ = ["DEFAULT_RELEASES", "Frequency", "ReleaseSpec"]
