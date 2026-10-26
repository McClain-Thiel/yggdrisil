from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from yggdrisil.graph.base import ReadOnlyStateGraph
from yggdrisil.limits import RunStatus
from yggdrisil.memory import MemoryConflictError, MemoryEntry, MemoryUpdate
from yggdrisil.policy import Decision, PolicyStepError, Proposal
from yggdrisil.serialize import dumps
from yggdrisil.types import EvaluationRecord, StateNode

State = TypeVar("State")
Action = TypeVar("Action")


@dataclass(frozen=True)
class ExplorationRequest:
    state_id: str
    guidance: str | None = None
    worker: str = "explorer"
    memory_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NavigationPlan:
    requests: list[ExplorationRequest]
    checkpoint: dict[str, Any] | None = None
    memory_updates: list[MemoryUpdate] = field(default_factory=list)
    continue_on_empty: bool = False


@dataclass(frozen=True)
class ExplorerResult(Generic[Action]):
    actions: list[Action]
    note: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    memory_updates: list[MemoryUpdate] = field(default_factory=list)


@dataclass(frozen=True)
class NavigatorContext:
    goal: str | None
    status: RunStatus
    unique_states: int
    edges: int
    frontier_ids: list[str]
    recent: list[dict[str, Any]]
    summaries: dict[str, str]
    checkpoint: dict[str, Any] = field(default_factory=dict)
    memory: list[MemoryEntry] = field(default_factory=list)
    memory_keys: list[str] = field(default_factory=list)
    reports: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ExplorerContext(Generic[State]):
    goal: str | None
    state_id: str
    state: State
    lineage: list[StateNode[State]]
    guidance: str | None
    evaluations: list[EvaluationRecord] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    checkpoint: dict[str, Any] = field(default_factory=dict)
    memory: list[MemoryEntry] = field(default_factory=list)
    memory_keys: list[str] = field(default_factory=list)


class Navigator(Protocol):
    async def plan(self, context: NavigatorContext) -> NavigationPlan: ...


class ExplorationRequestSelector(Protocol[State, Action]):
    """Select exploration requests directly from durable search state."""

    def select(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> list[ExplorationRequest]: ...


class Explorer(Protocol[State, Action]):
    async def explore(
        self, context: ExplorerContext[State]
    ) -> ExplorerResult[Action]: ...


class NavigatorExplorerPolicy(Generic[State, Action]):
    """Two-role agent policy. Agents are ephemeral; the graph is persistent.

    Explorer failures are fail-fast by default. Set
    ``tolerate_explorer_failures`` to persist failed explorer decisions while
    still returning successful sibling proposals from the same selection.
    """

    def __init__(
        self,
        navigator: Navigator | None,
        explorer: Explorer[State, Action] | Mapping[str, Explorer[State, Action]],
        *,
        goal: str | None = None,
        max_requests: int = 4,
        lineage_depth: int = 8,
        recent: int = 12,
        max_frontier: int = 100,
        request_selector: ExplorationRequestSelector[State, Action] | None = None,
        tolerate_explorer_failures: bool = False,
        memory_enabled: bool = False,
        memory_namespace: str | None = None,
        max_memory_entries: int = 20,
        max_memory_chars: int = 16000,
        commit_memory_before_exploration: bool = False,
    ) -> None:
        if navigator is None and request_selector is None:
            raise ValueError("navigator is required when request_selector is not set")
        self.navigator = navigator
        self.explorer = explorer
        self.goal = goal
        self.max_requests = max_requests
        self.lineage_depth = lineage_depth
        self.recent = recent
        self.max_frontier = max_frontier
        self.request_selector = request_selector
        self.tolerate_explorer_failures = tolerate_explorer_failures
        if max_memory_entries < 1 or max_memory_chars < 1:
            raise ValueError("memory context limits must be positive")
        if memory_namespace is not None and not memory_namespace.strip():
            raise ValueError("memory namespace must be nonempty")
        self.memory_enabled = memory_enabled
        if commit_memory_before_exploration and not memory_enabled:
            raise ValueError("committing manager memory first requires memory_enabled")
        self.commit_memory_before_exploration = commit_memory_before_exploration
        self.memory_namespace = memory_namespace
        self.max_memory_entries = max_memory_entries
        self.max_memory_chars = max_memory_chars
        self._interrupted_decisions: list[Decision[Action]] = []

    def drain_interrupted_decisions(self) -> list[Decision[Action]]:
        """Return and clear decisions staged before policy-task cancellation."""
        decisions = self._interrupted_decisions
        self._interrupted_decisions = []
        return decisions

    async def step(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> list[Decision[Action]]:
        self._interrupted_decisions = []
        if self.memory_enabled and status.run_id is None:
            raise ValueError("persistent orchestration requires a run_id")
        navigator_context = self._navigator_context(graph, status)
        checkpoint = deepcopy(navigator_context.checkpoint)
        memory_updates: list[MemoryUpdate] = []
        continue_on_empty = False
        latest = (
            graph.decisions(status.run_id, limit=1, newest=True)
            if self.commit_memory_before_exploration
            else []
        )
        pending = (
            latest[0]
            if latest and latest[0].metadata.get("_yggdrisil_pending_exploration")
            else None
        )
        if pending is not None:
            requests = [
                ExplorationRequest(**item) for item in pending.output["requests"]
            ]
            selection_component: object = None
            input_context: Any = {"manager_decision_id": pending.decision_id}
            request_source = "pending"
        elif self.request_selector is None:
            if self.navigator is None:  # guarded in __init__; narrows the type
                raise RuntimeError("navigator is not configured")
            selection_component = self.navigator
            input_context = _input_context(
                self.navigator,
                navigator_context,
                format_navigator_prompt,
            )
            request_source = "navigator"
            try:
                plan = await self.navigator.plan(navigator_context)
            except asyncio.CancelledError as exc:
                self._interrupted_decisions = [
                    _failed_decision(
                        role="navigator",
                        component=selection_component,
                        input_context=input_context,
                        request_source=request_source,
                        exc=exc,
                    )
                ]
                raise
            except Exception as exc:
                decision = _failed_decision(
                    role="navigator",
                    component=selection_component,
                    input_context=input_context,
                    request_source=request_source,
                    exc=exc,
                )
                raise PolicyStepError(
                    "navigator failed",
                    decisions=[decision],
                    cause=exc,
                ) from exc
            requests = plan.requests[: self.max_requests]
            if not self.memory_enabled and (
                plan.checkpoint is not None or plan.memory_updates
            ):
                raise ValueError(
                    "enable memory to persist manager state or shared documents"
                )
            if plan.checkpoint is not None:
                checkpoint = deepcopy(plan.checkpoint)
            memory_updates = plan.memory_updates
            continue_on_empty = plan.continue_on_empty
        else:
            selection_component = self.request_selector
            input_context = _selector_input_context(status)
            request_source = "selector"
            try:
                selected = self.request_selector.select(graph, status)
            except Exception as exc:
                decision = _failed_decision(
                    role="navigator",
                    component=selection_component,
                    input_context=input_context,
                    request_source=request_source,
                    exc=exc,
                )
                raise PolicyStepError(
                    "exploration request selector failed",
                    decisions=[decision],
                    cause=exc,
                ) from exc
            requests = selected[: self.max_requests]
        defer_exploration = bool(
            self.commit_memory_before_exploration and memory_updates and requests
        )
        decisions: list[Decision[Action]] = [
            Decision(
                role="navigator",
                selected_state_ids=[request.state_id for request in requests],
                model=_model_name(selection_component),
                input_context=input_context,
                output={
                    "requests": [
                        {
                            "state_id": request.state_id,
                            "guidance": request.guidance,
                            **(
                                {
                                    "worker": request.worker,
                                    "memory_keys": request.memory_keys,
                                }
                                if self.memory_enabled
                                else {}
                            ),
                        }
                        for request in requests
                    ],
                    **({"checkpoint": checkpoint} if self.memory_enabled else {}),
                },
                tool_calls=list(getattr(selection_component, "last_trace", ()) or ()),
                metadata={
                    "request_source": request_source,
                    **(
                        {"_yggdrisil_pending_exploration": True}
                        if defer_exploration
                        else {}
                    ),
                },
                continue_on_empty=continue_on_empty
                or defer_exploration
                or (bool(requests) and self.request_selector is not None),
                memory_updates=memory_updates,
                memory_namespace=self.memory_namespace,
            )
        ]
        if not requests or defer_exploration:
            return decisions
        contexts = [self._explorer_context(graph, request) for request in requests]
        workers = [self._worker(request.worker) for request in requests]
        if self.memory_enabled:
            namespace = self.memory_namespace or status.run_id
            assert namespace is not None
            visible_documents = {
                entry.key: entry for entry in graph.memories(namespace)
            }
            revisions = {
                key: entry.revision for key, entry in visible_documents.items()
            }
            for update in memory_updates:
                revision = revisions.get(update.key, 0)
                if update.expected_revision != revision:
                    raise MemoryConflictError(
                        f"stale manager memory update: {update.key}"
                    )
                revisions[update.key] = revision + 1
            for request, context in zip(requests, contexts, strict=True):
                context.checkpoint.update(deepcopy(checkpoint))
                worker_documents = (
                    [visible_documents[key] for key in request.memory_keys]
                    if request.memory_keys
                    else list(visible_documents.values())
                )
                context.memory.extend(deepcopy(self._bounded_memory(worker_documents)))
                context.memory_keys.extend(visible_documents)
        explorer_inputs = [
            _input_context(worker, context, format_explorer_prompt)
            for worker, context in zip(workers, contexts, strict=True)
        ]
        try:
            results = await asyncio.gather(
                *[
                    worker.explore(ctx)
                    for worker, ctx in zip(workers, contexts, strict=True)
                ],
                return_exceptions=True,
            )
        except asyncio.CancelledError as exc:
            self._interrupted_decisions = decisions + [
                _failed_decision(
                    role="explorer",
                    component=worker,
                    input_context=input_context,
                    request_source=None,
                    exc=exc,
                    selected_state_ids=[request.state_id],
                )
                for request, input_context, worker in zip(
                    requests,
                    explorer_inputs,
                    workers,
                    strict=True,
                )
            ]
            raise
        failures: list[Exception] = []
        for request, input_context, result, worker in zip(
            requests,
            explorer_inputs,
            results,
            workers,
            strict=True,
        ):
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result
                failures.append(result)
                decisions.append(
                    _failed_decision(
                        role="explorer",
                        component=worker,
                        input_context=input_context,
                        request_source=None,
                        exc=result,
                        selected_state_ids=[request.state_id],
                    )
                )
                continue
            proposals = [
                Proposal(parent_id=request.state_id, action=action)
                for action in result.actions
            ]
            metadata = {"note": result.note} if result.note else {}
            if self.memory_enabled:
                metadata["worker"] = request.worker
            elif result.memory_updates:
                raise ValueError("enable memory to persist worker documents")
            decisions.append(
                Decision(
                    role="explorer",
                    proposals=proposals,
                    selected_state_ids=[request.state_id],
                    model=_model_name(worker),
                    input_context=input_context,
                    tool_calls=list(result.trace),
                    output={"actions": list(result.actions), "note": result.note},
                    metadata=metadata,
                    memory_updates=result.memory_updates,
                    memory_namespace=self.memory_namespace,
                )
            )
        if failures and not self.tolerate_explorer_failures:
            raise PolicyStepError(
                "one or more explorers failed",
                decisions=decisions,
                cause=failures[0],
            ) from failures[0]
        return decisions

    def _worker(self, name: str) -> Explorer[State, Action]:
        if isinstance(self.explorer, Mapping):
            try:
                return self.explorer[name]
            except KeyError as exc:
                raise ValueError(f"unknown worker: {name}") from exc
        if name != "explorer":
            raise ValueError(f"unknown worker: {name}")
        return self.explorer

    def _bounded_memory(self, entries: list[MemoryEntry]) -> list[MemoryEntry]:
        selected: list[MemoryEntry] = []
        remaining = self.max_memory_chars
        for entry in entries:
            size = len(dumps(entry))
            if size <= remaining and len(selected) < self.max_memory_entries:
                selected.append(entry)
                remaining -= size
        return selected

    def _navigator_context(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> NavigatorContext:
        recent_nodes = graph.states(limit=self.recent, newest=True)
        summaries: dict[str, str] = {}
        recent_decisions = graph.decisions(
            run_id=status.run_id,
            limit=self.recent,
            newest=True,
        )
        for decision in reversed(recent_decisions):
            note = decision.metadata.get("note")
            if isinstance(note, str) and note:
                for state_id in decision.selected_state_ids:
                    summaries[state_id] = note
        checkpoint: dict[str, Any] = {}
        documents: list[MemoryEntry] = []
        reports: list[dict[str, Any]] = []
        if self.memory_enabled:
            assert status.run_id is not None
            documents = graph.memories(self.memory_namespace or status.run_id)
            for decision in graph.decisions(run_id=status.run_id, newest=True):
                if decision.role == "navigator" and isinstance(decision.output, dict):
                    if "checkpoint" in decision.output:
                        checkpoint = deepcopy(decision.output["checkpoint"])
                        break
            for decision in reversed(recent_decisions):
                if decision.role != "explorer":
                    continue
                outcomes = []
                for event in graph.proposal_events(decision_id=decision.decision_id):
                    outcomes.append(
                        {
                            "action": event.action,
                            "outcome": event.outcome,
                            "child_id": event.child_id,
                            "error": event.error,
                            "evaluations": [
                                {
                                    "evaluator": record.evaluator,
                                    "metrics": record.metrics,
                                }
                                for record in graph.evaluations(event.child_id)
                            ]
                            if event.child_id
                            else [],
                        }
                    )
                reports.append(
                    {
                        "decision_id": decision.decision_id,
                        "worker": decision.metadata.get("worker", "explorer"),
                        "state_ids": decision.selected_state_ids,
                        "output": decision.output,
                        "error": decision.metadata.get("error"),
                        "outcomes": outcomes,
                    }
                )
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
                    "evaluations": [
                        {
                            "evaluator": record.evaluator,
                            "version": record.version,
                            "metrics": record.metrics,
                        }
                        for record in graph.evaluations(n.state_id)
                    ],
                }
                for n in recent_nodes
            ],
            summaries=summaries,
            checkpoint=checkpoint,
            memory=deepcopy(self._bounded_memory(documents)),
            memory_keys=[entry.key for entry in documents],
            reports=reports,
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
            evaluations=graph.evaluations(node.state_id),
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
        lines.append("    evaluations=" + dumps(item["evaluations"]))
    if context.summaries:
        lines.append("NOTES:")
        for state_id, note in context.summaries.items():
            lines.append(f"  - {state_id}: {note}")
    if context.checkpoint or context.memory_keys or context.reports:
        lines.extend(
            [
                "MANAGER_CHECKPOINT: " + dumps(context.checkpoint),
                "SHARED_MEMORY_KEYS: " + dumps(context.memory_keys),
                "SHARED_MEMORY_CONTEXT: " + dumps(context.memory),
                "WORKER_REPORTS: " + dumps(context.reports),
            ]
        )
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
            f"EVALUATIONS: {dumps([{'evaluator': record.evaluator, 'version': record.version, 'metrics': record.metrics} for record in context.evaluations])}",
            f"NAVIGATOR_GUIDANCE: {context.guidance or '(none)'}",
            f"SHORT_LINEAGE: {dumps(lineage)}",
            *(
                [
                    "MANAGER_CHECKPOINT: " + dumps(context.checkpoint),
                    "SHARED_MEMORY_KEYS: " + dumps(context.memory_keys),
                    "SHARED_MEMORY_CONTEXT: " + dumps(context.memory),
                ]
                if context.checkpoint or context.memory_keys
                else []
            ),
            "Propose only direct children of the current state.",
        ]
    )


def _model_name(component: object) -> str | None:
    model = getattr(component, "model", None)
    return model if isinstance(model, str) else None


def _input_context(
    component: object,
    context: Any,
    fallback: Any,
) -> Any:
    formatter = getattr(component, "format_prompt", None)
    if callable(formatter):
        return formatter(context)
    return fallback(context)


def _selector_input_context(status: RunStatus) -> dict[str, Any]:
    """Small durable snapshot for deterministic request selection."""
    return {
        "run_id": status.run_id,
        "step": status.step,
        "unique_states": status.unique_states,
        "edges": status.edges,
        "elapsed_s": status.elapsed_s,
    }


def _failed_decision(
    *,
    role: str,
    component: object,
    input_context: Any,
    request_source: str | None,
    exc: BaseException,
    selected_state_ids: list[str] | None = None,
) -> Decision[Any]:
    detail = str(exc) or "policy step interrupted"
    error = f"{type(exc).__name__}: {detail}"
    metadata: dict[str, Any] = {
        "attempt_status": "failed",
        "error_type": type(exc).__name__,
        "error": detail,
    }
    if request_source is not None:
        metadata["request_source"] = request_source
    return Decision(
        role=role,
        selected_state_ids=selected_state_ids or [],
        model=_model_name(component),
        input_context=input_context,
        tool_calls=list(getattr(component, "last_trace", ()) or ()),
        output={"error": error},
        metadata=metadata,
    )
