"""Methods comparison framework.

Cross-cutting scaffold used by Stage 3 onward to compare baseline vs enhancement
methods (e.g. OLS vs Causal Forest, PCA vs DFM, ERC vs HRP) and gate promotion.

Public surface:
    - Method, MethodMetadata        — base class
    - MethodStatus                  — lifecycle enum
    - ComparisonResult, MethodComparator
    - PromotionCriteria, evaluate_promotion
    - registry helpers (register_method, get_method, ...)
"""

from macro_trader.methods.base import Method, MethodMetadata
from macro_trader.methods.comparator import ComparisonResult, MethodComparator
from macro_trader.methods.promotion import PromotionCriteria, evaluate_promotion
from macro_trader.methods.registry import (
    MethodRegistry,
    get_method,
    get_production,
    get_shadows,
    list_methods,
    register_method,
    set_status,
)
from macro_trader.methods.status import MethodStatus

__all__ = [
    "ComparisonResult",
    "Method",
    "MethodComparator",
    "MethodMetadata",
    "MethodRegistry",
    "MethodStatus",
    "PromotionCriteria",
    "evaluate_promotion",
    "get_method",
    "get_production",
    "get_shadows",
    "list_methods",
    "register_method",
    "set_status",
]
