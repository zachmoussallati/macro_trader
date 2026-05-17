"""Centroid-anchored regime labeling tests."""

from __future__ import annotations

import numpy as np
import pytest

from macro_trader.regime.labeling import (
    DEFAULT_ANCHOR_CENTROIDS,
    anchor_centroids_matrix,
    map_centroids_to_labels,
)


@pytest.mark.unit
def test_anchor_centroids_match_named_regimes() -> None:
    assert set(DEFAULT_ANCHOR_CENTROIDS.keys()) == {
        "risk_on_growth",
        "risk_off_defensive",
        "stagflation",
        "carry_friendly",
        "vol_spike",
    }


@pytest.mark.unit
def test_anchor_centroids_matrix_shape() -> None:
    feature_cols = ["growth", "inflation", "vix_level"]
    named = ["risk_on_growth", "stagflation"]
    m = anchor_centroids_matrix(feature_cols, named)
    assert m.shape == (2, 3)
    # risk_on_growth has growth=0.8, inflation=0.0.
    assert m[0, 0] == 0.8
    assert m[0, 1] == 0.0


@pytest.mark.unit
def test_map_centroids_to_labels_identity() -> None:
    """Same centroids in same order -> labels unchanged, no drift."""
    centroids = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    prior_labels = ["a", "b", "c"]
    new_labels, summary = map_centroids_to_labels(
        centroids, centroids, prior_labels
    )
    assert new_labels == prior_labels
    assert summary["drift_warnings"] == []


@pytest.mark.unit
def test_map_centroids_to_labels_reorder() -> None:
    """When the new fit returns centroids in a different order, the
    Hungarian assignment recovers the prior labels."""
    prior = np.array([[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]])
    new = np.array([prior[2], prior[0], prior[1]])
    prior_labels = ["a", "b", "c"]
    new_labels, _ = map_centroids_to_labels(new, prior, prior_labels)
    # Row 0 of new = prior row 2 ("c"); row 1 = prior 0 ("a"); row 2 = prior 1 ("b").
    assert new_labels == ["c", "a", "b"]


@pytest.mark.unit
def test_map_centroids_to_labels_flags_drift() -> None:
    """A new centroid >2 std from the matched prior centroid triggers
    a drift warning."""
    prior = np.array([[0.0, 0.0]])
    new = np.array([[5.0, 5.0]])
    _labels, summary = map_centroids_to_labels(
        new, prior, ["a"], drift_warning_threshold=2.0
    )
    assert summary["drift_warnings"]
    assert summary["drift_warnings"][0]["matched_label"] == "a"


@pytest.mark.unit
def test_map_centroids_shape_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        map_centroids_to_labels(
            np.zeros((3, 2)),
            np.zeros((2, 2)),
            ["a", "b"],
        )
