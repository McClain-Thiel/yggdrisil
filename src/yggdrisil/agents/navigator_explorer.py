from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from yggdrisil.graph.base import ReadOnlyStateGraph
from yggdrisil.limits import RunStatus
from yggdrisil.policy import Proposal
from yggdrisil.serialize import dumps
from yggdrisil.types import StateNode

State = TypeVar("State")
Action = TypeVar("Action")


@dataclass(frozen=True)
class ExplorationRequest:
    state_id: str
    guidance: str | None = None


@dataclass(frozen=True)
class NavigationPlan:
    requests: list[ExplorationRequest]


@dataclass(frozen=True)
class ExplorerResult(Generic[Action]):
    actions: list[Action]
    note: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class NavigatorContext:
    goal: str | None
    status: RunStatus
    unique_states: int
    edges: int
    frontier_ids: list[str]
    recent: list[dict[str, Any]]
    summaries: dict[str, str]


@dataclass(frozen=True)
class ExplorerContext(Generic[State]):
    goal: str | None
    state_id: str
    state: State
    lineage: list[StateNode[State]]
    guidance: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


class Navigator(Protocol):
    async def plan(self, context: NavigatorContext) -> NavigationPlan: ...


class Explorer(Protocol[State, Action]):
    async def explore(
        self, context: ExplorerContext[State]
    ) -> ExplorerResult[Action]: ...


class NavigatorExplorerPolicy(Generic[State, Action]):
    """Two-role agent policy. Agents are ephemeral; the graph is persistent."""

    def __init__(
        self,
        navigator: Navigator,
        explorer: Explorer[State, Action],
        *,
        goal: str | None = None,
        max_requests: int = 4,
        lineage_depth: int = 8,
        recent: int = 12,
        max_frontier: int = 100,
    ) -> None:
        self.navigator = navigator
        self.explorer = explorer
        self.goal = goal
        self.max_requests = max_requests
        self.lineage_depth = lineage_depth
        self.recent = recent
        self.max_frontier = max_frontier

    async def step(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> list[Proposal[Action]]:
        plan = await self.navigator.plan(self._navigator_context(graph, status))
        requests = plan.requests[: self.max_requests]
        if not requests:
            return []
        contexts = [self._explorer_context(graph, request) for request in requests]
        results = await asyncio.gather(
            *[self.explorer.explore(ctx) for ctx in contexts]
        )
        proposals: list[Proposal[Action]] = []
        for request, result in zip(requests, results, strict=True):
            for action in result.actions:
                metadata: dict[str, Any] = {"created_by": "explorer"}
                if result.note:
                    metadata["note"] = result.note
                if result.trace:
                    metadata["trace"] = list(result.trace)
                proposals.append(
                    Proposal(
                        parent_id=request.state_id,
                        action=action,
                        metadata=metadata,
                    )
                )
        return proposals

    def _navigator_context(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> NavigatorContext:
        recent_nodes = graph.states(limit=self.recent, newest=True)
        summaries: dict[str, str] = {}
        for node in recent_nodes:
            note = node.metadata.get("note") or node.metadata.get("agent_note")
            if isinstance(note, str) and note:
                summaries[node.state_id] = note
        return NavigatorContext(
            goal=self.goal,
            status=status,
            unique_states=len(graph),
            edges=graph.edge_count(),
            frontier_ids=[n.state_id for n in graph.frontier(limit=self.max_frontier)],
            recent=[
                {
                    "state_id": n.state_id,
                    "created_step": n.created_step,
                    "metadata": n.metadata,
                }
                for n in recent_nodes
            ],
            summaries=summaries,
        )

    def _explorer_context(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        request: ExplorationRequest,
    ) -> ExplorerContext[State]:
        node = graph.get_state(request.state_id)
        lineage = _lineage(graph, request.state_id, self.lineage_depth)
        return ExplorerContext(
            goal=self.goal,
            state_id=node.state_id,
            state=node.state,
            lineage=lineage,
            guidance=request.guidance,
            metadata=dict(node.metadata),
        )


def _lineage(
    graph: ReadOnlyStateGraph[State, Action],
    state_id: str,
    depth: int,
) -> list[StateNode[State]]:
    """A single path of parents up toward a root, nearest first."""
    path: list[StateNode[State]] = []
    current = state_id
    seen: set[str] = set()
    for _ in range(depth):
        parents = graph.parents(current)
        if not parents:
            break
        parent = parents[0]
        if parent.state_id in seen:
            break
        path.append(parent)
        seen.add(parent.state_id)
        current = parent.state_id
    return path


def format_navigator_prompt(context: NavigatorContext) -> str:
    lines = [
        f"GOAL: {context.goal or '(unspecified)'}",
        f"STEP: {context.status.step}",
        f"UNIQUE_STATES: {context.unique_states}",
        f"EDGES: {context.edges}",
        f"ELAPSED_S: {context.status.elapsed_s:.2f}",
        f"LIMITS: max_states={context.status.limits.max_states} "
        f"max_steps={context.status.limits.max_steps} "
        f"max_wall_time_s={context.status.limits.max_wall_time_s}",
        "FRONTIER: " + (", ".join(context.frontier_ids) or "(empty)"),
        "RECENT:",
    ]
    for item in context.recent:
        lines.append(f"  - {item['state_id']} step={item['created_step']}")
    if context.summaries:
        lines.append("NOTES:")
        for state_id, note in context.summaries.items():
            lines.append(f"  - {state_id}: {note}")
    lines.append("Select existing state_ids to explore next.")
    return "\n".join(lines)


def format_explorer_prompt(context: ExplorerContext[Any]) -> str:
    lineage = [
        {"state_id": n.state_id, "created_step": n.created_step}
        for n in context.lineage
    ]
    return "\n".join(
        [
            f"GOAL: {context.goal or '(unspecified)'}",
            f"CURRENT_STATE_ID: {context.state_id}",
            f"CURRENT_STATE: {dumps(context.state)}",
            f"NAVIGATOR_GUIDANCE: {context.guidance or '(none)'}",
            f"SHORT_LINEAGE: {dumps(lineage)}",
            "Propose only direct children of the current state.",
        ]
    )
