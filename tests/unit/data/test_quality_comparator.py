"""Unit tests for DataQualityComparator metrics."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from macro_trader.data.quality.comparator import DataQualityComparator
from macro_trader.data.quality.methods import (
    IsolationForestOutlier,
    ZScoreOutlier,
)


@pytest.mark.unit
def test_comparator_basic_run() -> None:
    rng = np.random.default_rng(0)
    series = rng.normal(size=200)
    series[100] = 50.0  # one shared outlier

    a = ZScoreOutlier()
    b = IsolationForestOutlier(contamination=0.05, random_state=0)
    a.fit(series)
    b.fit(series)

    comparator = DataQualityComparator()
    result = comparator.compare(
        a,
        b,
        series,
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert "flag_rate_a" in result.metrics
    assert "flag_rate_b" in result.metrics
    assert "agreement_iou" in result.metrics
    assert "agreement_correlation" in result.metrics
    assert result.metrics["n_observations"] == 200.0


@pytest.mark.unit
def test_comparator_handles_identical_masks() -> None:
    a = ZScoreOutlier()
    b = ZScoreOutlier()
    comparator = DataQualityComparator()
    result = comparator.compare(
        a,
        b,
        np.zeros(120),
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 1, 2, tzinfo=UTC),
    )
    # Both masks are all-False on constant input → trivially agree.
    assert result.metrics["agreement_iou"] == 1.0
    assert result.metrics["agreement_correlation"] == 1.0
