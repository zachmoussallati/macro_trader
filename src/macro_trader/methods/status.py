"""Method lifecycle status enum.

A method moves through these states over its lifetime:

    DEVELOPMENT -> SHADOW -> PRODUCTION
                            ^
                            |
    BASELINE ---------------+

`BASELINE` is the simpler, conservative implementation that runs by default and
drives decisions. `SHADOW` is an enhancement (more complex / experimental) that
runs in parallel on the same inputs but does NOT drive decisions. Once the
shadow has demonstrated improvement over the baseline through enough
`MethodComparator` runs to satisfy a `PromotionCriteria`, an operator may
manually promote it to `PRODUCTION`. The previously-driving method (typically
the baseline) is then either kept as a continuous fallback or marked
`DEPRECATED`.

Promotion is ALWAYS a manual decision. The framework only computes whether a
method is eligible.
"""

from __future__ import annotations

from enum import StrEnum


class MethodStatus(StrEnum):
    """Lifecycle status for a registered method."""

    DEVELOPMENT = "development"
    """Method is being built. Not yet wired into any pipeline."""

    BASELINE = "baseline"
    """Simpler, conservative version. Runs in production by default and drives
    decisions for its component unless an enhancement has been promoted."""

    SHADOW = "shadow"
    """Enhancement running in parallel on identical inputs. Outputs are recorded
    and compared against the baseline, but do NOT drive any decision."""

    PRODUCTION = "production"
    """Promoted enhancement that now drives decisions for its component. There
    must be at most one PRODUCTION method per component at any time."""

    DEPRECATED = "deprecated"
    """Retired method. Kept in the registry for historical reproducibility."""

    @classmethod
    def terminal(cls) -> frozenset[MethodStatus]:
        """Statuses from which no further transition is expected."""
        return frozenset({cls.DEPRECATED})

    @classmethod
    def drives_decisions(cls) -> frozenset[MethodStatus]:
        """Statuses whose methods drive decisions (one of these runs in prod)."""
        return frozenset({cls.BASELINE, cls.PRODUCTION})
