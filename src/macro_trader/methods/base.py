"""Abstract `Method` base class.

Every algorithm that can be swapped in or out of a pipeline component (signal,
factor model, regime classifier, portfolio constructor, ...) implements this
interface. Subclasses bind concrete `InputT` and `OutputT` types so the
comparator framework can reason about agreement and stability over the same
input domain.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class MethodMetadata:
    """Static descriptive metadata for a method.

    Stored in `system.methods_registry.metadata_json` so the registry can be
    queried without loading method code.
    """

    method_id: str
    """Stable unique identifier, e.g. ``"factor_exposure.ols.v1"``."""

    component: str
    """The pipeline component the method belongs to, e.g.
    ``"macro_factor_exposure"`` or ``"regime_classifier"``. There may be many
    methods per component but at most one in PRODUCTION."""

    name: str
    """Human-readable name shown in dashboards."""

    version: str
    """Semantic version of the method implementation, e.g. ``"1.0.0"``."""

    description: str
    """One-paragraph description: what it does, key assumptions, limitations."""

    references: list[str] = field(default_factory=list)
    """Papers, blog posts, internal docs. URLs or citations."""


class Method[InputT, OutputT](ABC):
    """Abstract base for any swappable algorithm in the pipeline.

    Subclasses MUST set the class attribute :attr:`metadata` and implement
    :meth:`fit`, :meth:`predict`, :meth:`serialize`, and
    :meth:`deserialize`. The `fit` / `predict` split is intentionally
    permissive — for purely deterministic transforms ``fit`` can be a no-op.
    """

    metadata: MethodMetadata

    @abstractmethod
    def fit(self, data: InputT) -> None:
        """Train / calibrate on `data`. Mutates internal state."""
        raise NotImplementedError

    @abstractmethod
    def predict(self, data: InputT) -> OutputT:
        """Apply the (fitted) method and return the component's output."""
        raise NotImplementedError

    @abstractmethod
    def serialize(self) -> bytes:
        """Serialize fitted state to bytes for storage (e.g. pickle/joblib)."""
        raise NotImplementedError

    @classmethod
    @abstractmethod
    def deserialize(cls, blob: bytes) -> Method[InputT, OutputT]:
        """Reverse of :meth:`serialize`. Returns a fully-restored instance."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        m = self.metadata
        return f"<{type(self).__name__} {m.method_id} v{m.version}>"
