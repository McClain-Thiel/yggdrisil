from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from yggdrisil.graph.base import ReadOnlyStateGraph
from yggdrisil.limits import RunStatus

Action = TypeVar("Action")


@dataclass(frozen=True)
class Proposal(Generic[Action]):
    parent_id: str
    action: Action
    metadata: dict[str, Any] = field(default_factory=dict)


class Policy(Protocol[Action]):
    """Decides where to search. Must not mutate the graph."""

    async def step(
        self,
        graph: ReadOnlyStateGraph,
        status: RunStatus,
    ) -> list[Proposal[Action]]: ...
