from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

from yggdrisil.graph.sqlite import SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.policy import Policy, Proposal
from yggdrisil.problem import Problem
from yggdrisil.types import RunResult

State = TypeVar("State")
Action = TypeVar("Action")


def _utcnow_id() -> str:
    return datetime.now(timezone.utc).strftime("run_%Y_%m_%d_%H%M%S")


class Runner(Generic[State, Action]):
    """Ask a policy for proposals and materialize them on the graph."""

    def __init__(
        self,
        problem: Problem[State, Action],
        policy: Policy[Action],
        graph: SQLiteStateGraph[State, Action],
        limits: RunLimits,
        *,
        run_id: str | None = None,
        resume: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.problem = problem
        self.policy = policy
        self.graph = graph
        self.limits = limits
        self.metadata = metadata or {}
        self.resume = resume
        self.run_id = run_id or _utcnow_id()
        self._step = 0

    async def run(self) -> RunResult:
        started = time.monotonic()
        self._restore()
        self._ensure_initial_state()
        self._persist("running")
        stop_reason = "completed"
        try:
            while True:
                elapsed = time.monotonic() - started
                hit = self.limits.reached(
                    unique_states=len(self.graph),
                    step=self._step,
                    elapsed_s=elapsed,
                )
                if hit:
                    stop_reason = hit
                    break
                proposals = await self.policy.step(
                    self.graph.readonly(),
                    self._status(elapsed),
                )
                if not proposals:
                    stop_reason = "no_proposals"
                    break
                limited = False
                for proposal in proposals:
                    elapsed = time.monotonic() - started
                    hit = self.limits.reached(
                        unique_states=len(self.graph),
                        step=self._step,
                        elapsed_s=elapsed,
                    )
                    if hit:
                        stop_reason = hit
                        limited = True
                        break
                    self._apply(proposal)
                self._step += 1
                self._persist("running")
                if limited:
                    break
        except Exception:
            self._persist("failed")
            raise
        elapsed_s = time.monotonic() - started
        result = RunResult(
            run_id=self.run_id,
            status="completed",
            stop_reason=stop_reason,
            step=self._step,
            unique_states=len(self.graph),
            edges=self.graph.edge_count(),
            elapsed_s=elapsed_s,
            limits=self.limits,
        )
        self._persist("completed", extra={"stop_reason": stop_reason})
        self._write_manifest(result)
        return result

    def _restore(self) -> None:
        if not self.resume:
            return
        record = self.graph.latest_run()
        if record is None:
            return
        self.run_id = record.run_id
        self._step = record.step

    def _ensure_initial_state(self) -> None:
        if len(self.graph) > 0:
            return
        state = self.problem.initial_state
        validate = getattr(self.problem, "validate_state", None)
        if validate is not None:
            validate(state)
        self.graph.add_state(
            self.problem.state_key(state),
            state,
            metadata={"origin": "initial"},
            created_step=0,
        )

    def _apply(self, proposal: Proposal[Action]) -> None:
        parent = self.graph.get_state(proposal.parent_id)
        validate_action = getattr(self.problem, "validate_action", None)
        if validate_action is not None:
            validate_action(parent.state, proposal.action)
        child = self.problem.apply(parent.state, proposal.action)
        validate_state = getattr(self.problem, "validate_state", None)
        if validate_state is not None:
            validate_state(child)
        decorate = getattr(self.problem, "decorate", None)
        if decorate is not None:
            child = decorate(child, dict(proposal.metadata))
        child_id = self.problem.state_key(child)
        existed = self.graph.has_state(child_id)
        self.graph.add_state(
            child_id,
            child,
            metadata=None if existed else dict(proposal.metadata),
            created_step=self._step + 1,
        )
        self.graph.add_edge(
            parent_id=proposal.parent_id,
            child_id=child_id,
            action=proposal.action,
            metadata=dict(proposal.metadata),
            created_step=self._step + 1,
        )

    def _status(self, elapsed_s: float) -> RunStatus:
        return RunStatus(
            step=self._step,
            unique_states=len(self.graph),
            edges=self.graph.edge_count(),
            elapsed_s=elapsed_s,
            limits=self.limits,
        )

    def _persist(self, status: str, extra: dict[str, Any] | None = None) -> None:
        metadata = dict(self.metadata)
        if extra:
            metadata.update(extra)
        self.graph.save_run(
            self.run_id,
            step=self._step,
            status=status,
            config={
                "max_states": self.limits.max_states,
                "max_steps": self.limits.max_steps,
                "max_wall_time_s": self.limits.max_wall_time_s,
            },
            metadata=metadata,
        )

    def _write_manifest(self, result: RunResult) -> None:
        if str(self.graph.path) == ":memory:":
            return
        payload = {
            "run_id": result.run_id,
            "status": result.status,
            "stop_reason": result.stop_reason,
            "step": result.step,
            "unique_states": result.unique_states,
            "edges": result.edges,
            "elapsed_s": result.elapsed_s,
            "limits": {
                "max_states": self.limits.max_states,
                "max_steps": self.limits.max_steps,
                "max_wall_time_s": self.limits.max_wall_time_s,
            },
        }
        path = Path(self.graph.path).with_name("run.json")
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
