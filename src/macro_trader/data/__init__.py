"""Data layer — ingestion, lineage, freshness, quality.

Public surface (Stage 2):
    - ``Ingester`` base class + ``IngestStats`` + ``LineageRecord``
    - ``lineage`` / ``freshness`` helpers
    - ``data.quality.methods``: ``ZScoreOutlier``, ``IsolationForestOutlier``
    - ``data.quality.comparator``: ``DataQualityComparator``
"""

from macro_trader.data.freshness import touch_freshness
from macro_trader.data.lineage import IngestStats, LineageRecord

__all__ = ["IngestStats", "LineageRecord", "touch_freshness"]
