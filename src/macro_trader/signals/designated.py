"""Resolve the designated production-facing method for a signal component.

The dashboard heatmap and the public ``/api/v1/signals/heatmap`` endpoint
need to know "which method's values do I show for the trend column?"
That answer is not always the registry's PRODUCTION method:

- Trend has three SMA BASELINEs (short / medium / long) plus a
  ``trend.ensemble.v1`` BASELINE that the operator wants to feature.
  ``MethodRegistry.production_for`` would return *some* baseline but
  there's no defined tiebreaker among multiple BASELINEs.
- A future component may have a SHADOW we want to surface for inspection
  before promoting it.

Resolution order (returns the first match):

1. ``settings.signals.designated_per_component`` config override.
   Highest priority — operators can pin a method by id.
2. Registry PRODUCTION method for the component.
3. Registry BASELINE method for the component (any one if multiple).
4. The first non-DEPRECATED method registered for the component.

Returns ``None`` if the component has no registered methods at all.
This lets the heatmap silently skip components that are still being
built.
"""

from __future__ import annotations

from macro_trader.config import get_settings
from macro_trader.methods.base import Method
from macro_trader.methods.registry import (
    MethodNotFoundError,
    get_method,
    get_reference_method,
)


def resolve(component: str) -> Method[object, object] | None:
    """Return the method designated as production-facing for ``component``."""
    cfg = get_settings().signals.designated_per_component
    pinned_id = cfg.get(component)
    if pinned_id is not None:
        try:
            return get_method(pinned_id)
        except MethodNotFoundError:
            # Pinned method not registered — fall through to registry
            # resolution rather than 500ing the heatmap endpoint.
            pass
    return get_reference_method(component)


def resolve_id(component: str) -> str | None:
    """Convenience: just the method_id of the designated method, or None."""
    method = resolve(component)
    return method.metadata.method_id if method is not None else None
