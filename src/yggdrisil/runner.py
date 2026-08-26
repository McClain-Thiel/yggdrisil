from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

from yggdrisil.exceptions import GraphError, SerializationError
from yggdrisil.graph.sqlite import SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.objective import Objective
from yggdrisil.policy import Policy, Proposal
from yggdrisil.problem import Problem
from yggdrisil.serialize import stable_hash
from yggdrisil.types import RunRecord, RunResult

State = TypeVar("State")
Action = TypeVar("Action")


def _utcnow_id() -> str:
    return datetime.now(UTC).strftime("run_%Y_%m_%d_%H%M%S_%f")


class Runner(Generic[State, Action]):
    """Ask a policy for proposals and materialize them on the graph."""

    def __init__(
        self,
        problem: Problem[State, Action],
        policy: Policy[Action],
        graph: SQLiteStateGraph[State, Action],
        limits: RunLimits,
        *,
        objective: Objective[State] | None = None,
        run_id: str | None = None,
        resume: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.problem = problem
        self.policy = policy
        self.graph = graph
        self.limits = limits
        self.objective = objective
        self.metadata = metadata or {}
        self.resume = resume
        self._requested_run_id = run_id
        self.run_id = run_id or _utcnow_id()
        self._step = 0
        self._best_state_id: str | None = None
        self._best_score: float | None = None
        self._problem_fingerprint: str | None = None

    async def run(self) -> RunResult:
        started = time.monotonic()
        self._problem_fingerprint = _fingerprint_problem(self.problem)
        restored = self._restore()
        self._check_problem_fingerprint(restored or self.graph.latest_run())
        self._ensure_initial_state()
        goal_found = self._prepare_objective_scores()
        self._persist("running")
        stop_reason = "completed"

        restored_stop = restored.metadata.get("stop_reason") if restored else None
        if goal_found or restored_stop == "objective":
            stop_reason = "objective"

        try:
            while stop_reason == "completed":
                elapsed = time.monotonic() - started
                hit = self.limits.reached(
                    unique_states=len(self.graph),
                    step=self._step,
                    elapsed_s=elapsed,
                )
                if hit:
                    stop_reason = hit
                    break

                proposals = await self._policy_step(elapsed)
                if proposals is None:
                    stop_reason = "max_wall_time_s"
                    break
                if not proposals:
                    stop_reason = "no_proposals"
                    break

                stopped = False
                for proposal in proposals:
                    elapsed = time.monotonic() - started
                    hit = self.limits.reached(
                        unique_states=len(self.graph),
                        step=self._step,
                        elapsed_s=elapsed,
                    )
                    if hit:
                        stop_reason = hit
                        stopped = True
                        break
                    if self._apply(proposal):
                        stop_reason = "objective"
                        stopped = True
                        break

                self._step += 1
                self._persist("running")
                if stopped:
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
            best_state_id=self._best_state_id,
            best_score=self._best_score,
        )
        self._persist("completed", extra={"stop_reason": stop_reason})
        self._write_manifest(result)
        return result

    async def _policy_step(self, elapsed_s: float) -> list[Proposal[Action]] | None:
        remaining = self._remaining_wall_time(elapsed_s)
        if remaining is None:
            return await self.policy.step(
                self.graph.readonly(),
                self._status(elapsed_s),
            )
        if remaining <= 0:
            return None
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                return await self.policy.step(
                    self.graph.readonly(),
                    self._status(elapsed_s),
                )
        except TimeoutError:
            if timeout.expired():
                return None
            raise

    def _remaining_wall_time(self, elapsed_s: float) -> float | None:
        if self.limits.max_wall_time_s is None:
            return None
        return self.limits.max_wall_time_s - elapsed_s

    def _restore(self) -> RunRecord | None:
        if not self.resume:
            return None
        record: RunRecord | None
        if self._requested_run_id is not None:
            try:
                record = self.graph.get_run(self._requested_run_id)
            except KeyError:
                return None
        else:
            record = self.graph.latest_run()
        if record is None:
            return None
        self.run_id = record.run_id
        self._step = record.step
        best_state_id = record.metadata.get("best_state_id")
        best_score = record.metadata.get("best_score")
        if isinstance(best_state_id, str):
            self._best_state_id = best_state_id
        if isinstance(best_score, (int, float)):
            self._best_score = float(best_score)
        return record

    def _check_problem_fingerprint(self, record: RunRecord | None) -> None:
        if record is None:
            return
        stored = record.config.get("problem_fingerprint")
        if stored is not None and stored != self._problem_fingerprint:
            raise GraphError(
                "graph was created for a different problem configuration; "
                "use a separate database"
            )

    def _ensure_initial_state(self) -> None:
        state = self.problem.initial_state
        validate = getattr(self.problem, "validate_state", None)
        if validate is not None:
            validate(state)
        state_id = self.problem.state_key(state)

        if len(self.graph) > 0 and not self.graph.has_state(state_id):
            raise GraphError(
                "graph does not contain this problem's initial state; "
                "use a separate database for each search problem"
            )

        metadata: dict[str, Any] = {"origin": "initial"}
        score = self._score(state)
        if score is not None:
            metadata["score"] = score
        node = self.graph.add_state(
            state_id,
            state,
            metadata=metadata,
            created_step=0,
        )
        if score is not None and "score" not in node.metadata:
            updated = dict(node.metadata)
            updated["score"] = score
            node = self.graph.update_state_metadata(state_id, updated)
        self._consider(node.state_id, score)

    def _apply(self, proposal: Proposal[Action]) -> bool:
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
        score = self._score(child)
        state_metadata = dict(proposal.metadata)
        if score is not None:
            state_metadata["score"] = score
        node, _, _ = self.graph.add_transition(
            parent_id=proposal.parent_id,
            child_id=child_id,
            child=child,
            action=proposal.action,
            state_metadata=state_metadata,
            edge_metadata=dict(proposal.metadata),
            created_step=self._step + 1,
        )
        self._consider(node.state_id, score)
        return bool(self.objective is not None and self.objective.goal_reached(child))

    def _prepare_objective_scores(self) -> bool:
        if self.objective is None:
            return False
        self._best_state_id = None
        self._best_score = None
        goal_found = False
        for node in self.graph.states():
            score = self._score(node.state)
            stored = node.metadata.get("score")
            if not isinstance(stored, (int, float)) or float(stored) != score:
                metadata = dict(node.metadata)
                metadata["score"] = score
                self.graph.update_state_metadata(node.state_id, metadata)
            self._consider(node.state_id, score)
            if self.objective.goal_reached(node.state):
                goal_found = True
        return goal_found

    def _score(self, state: State) -> float | None:
        if self.objective is None:
            return None
        return float(self.objective.score(state))

    def _consider(self, state_id: str, score: float | None) -> None:
        if score is None or self.objective is None:
            return
        if self.objective.better(score, self._best_score):
            self._best_state_id = state_id
            self._best_score = score

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
        if self._best_state_id is not None:
            metadata["best_state_id"] = self._best_state_id
            metadata["best_score"] = self._best_score
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
                "problem_fingerprint": self._problem_fingerprint,
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
            "best_state_id": result.best_state_id,
            "best_score": result.best_score,
            "limits": {
                "max_states": self.limits.max_states,
                "max_steps": self.limits.max_steps,
                "max_wall_time_s": self.limits.max_wall_time_s,
            },
        }
        path = Path(self.graph.path).with_suffix(".run.json")
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _fingerprint_problem(problem: Problem[Any, Any]) -> str:
    custom = getattr(problem, "problem_fingerprint", None)
    if callable(custom):
        config = custom()
    elif custom is not None:
        config = custom
    else:
        instance_vars = getattr(problem, "__dict__", {})
        config = {
            name: value
            for name, value in instance_vars.items()
            if not name.startswith("_")
            and name not in {"initial_state", "problem_fingerprint"}
        }
    payload = {
        "problem_type": f"{type(problem).__module__}:{type(problem).__qualname__}",
        "initial_state_id": problem.state_key(problem.initial_state),
        "config": config,
    }
    try:
        return stable_hash(payload)
    except SerializationError as exc:
        raise SerializationError(
            "problem configuration cannot be fingerprinted; define a "
            "problem_fingerprint attribute or method with serializable data"
        ) from exc
