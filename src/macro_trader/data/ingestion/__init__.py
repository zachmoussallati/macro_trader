"""Per-source ingestion modules.

Each ingester subclasses :class:`Ingester` and implements ``fetch``,
``transform``, and ``persist``. The base class handles lineage, freshness,
heartbeat, and structured logging.
"""

from macro_trader.data.ingestion.base import Ingester, RawData

__all__ = ["Ingester", "RawData"]
