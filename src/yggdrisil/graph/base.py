from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from yggdrisil.types import Edge, StateNode

State = TypeVar("State")
Action = TypeVar("Action")


class ReadOnlyStateGraph(Protocol[State, Action]):
    def get_state(self, state_id: str) -> StateNode[State]: ...

    def has_state(self, state_id: str) -> bool: ...

    def parents(self, state_id: str) -> list[StateNode[State]]: ...

    def children(self, state_id: str) -> list[StateNode[State]]: ...

    def ancestors(self, state_id: str) -> list[StateNode[State]]: ...

    def descendants(self, state_id: str) -> list[StateNode[State]]: ...

    def frontier(self) -> list[StateNode[State]]: ...

    def states(self) -> list[StateNode[State]]: ...

    def edges(self) -> list[Edge[Action]]: ...

    def __len__(self) -> int: ...

    def edge_count(self) -> int: ...


class StateGraph(ABC, Generic[State, Action]):
    @abstractmethod
    def add_state(
        self,
        state_id: str,
        state: State,
        metadata: dict[str, Any] | None = None,
        *,
        created_step: int = 0,
    ) -> StateNode[State]: ...

    @abstractmethod
    def add_edge(
        self,
        parent_id: str,
        child_id: str,
        action: Action,
        metadata: dict[str, Any] | None = None,
        *,
        created_step: int = 0,
    ) -> Edge[Action]: ...

    @abstractmethod
    def get_state(self, state_id: str) -> StateNode[State]: ...

    @abstractmethod
    def has_state(self, state_id: str) -> bool: ...

    @abstractmethod
    def parents(self, state_id: str) -> list[StateNode[State]]: ...

    @abstractmethod
    def children(self, state_id: str) -> list[StateNode[State]]: ...

    @abstractmethod
    def ancestors(self, state_id: str) -> list[StateNode[State]]: ...

    @abstractmethod
    def descendants(self, state_id: str) -> list[StateNode[State]]: ...

    @abstractmethod
    def frontier(self) -> list[StateNode[State]]: ...

    @abstractmethod
    def states(self) -> list[StateNode[State]]: ...

    @abstractmethod
    def edges(self) -> list[Edge[Action]]: ...

    @abstractmethod
    def __len__(self) -> int: ...

    @abstractmethod
    def edge_count(self) -> int: ...

    def readonly(self) -> ReadOnlyStateGraph[State, Action]:
        return ReadOnlyGraph(self)

    def to_networkx(self):
        from yggdrisil.graph.export import to_networkx

        return to_networkx(self)

    def export_json(self, path: str | Path) -> None:
        from yggdrisil.graph.export import export_json

        export_json(self, path)

    def export_graphml(self, path: str | Path) -> None:
        from yggdrisil.graph.export import export_graphml

        export_graphml(self, path)


class ReadOnlyGraph(Generic[State, Action]):
    """Explicit read-only view: mutators are simply not present."""

    __slots__ = ("_graph",)

    def __init__(self, graph: StateGraph[State, Action]) -> None:
        object.__setattr__(self, "_graph", graph)

    def get_state(self, state_id: str) -> StateNode[State]:
        return self._graph.get_state(state_id)

    def has_state(self, state_id: str) -> bool:
        return self._graph.has_state(state_id)

    def parents(self, state_id: str) -> list[StateNode[State]]:
        return self._graph.parents(state_id)

    def children(self, state_id: str) -> list[StateNode[State]]:
        return self._graph.children(state_id)

    def ancestors(self, state_id: str) -> list[StateNode[State]]:
        return self._graph.ancestors(state_id)

    def descendants(self, state_id: str) -> list[StateNode[State]]:
        return self._graph.descendants(state_id)

    def frontier(self) -> list[StateNode[State]]:
        return self._graph.frontier()

    def states(self) -> list[StateNode[State]]:
        return self._graph.states()

    def edges(self) -> list[Edge[Action]]:
        return self._graph.edges()

    def __len__(self) -> int:
        return len(self._graph)

    def edge_count(self) -> int:
        return self._graph.edge_count()
