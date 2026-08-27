from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

from yggdrisil.evaluation import EvaluatorSuite
from yggdrisil.exceptions import GraphError, SerializationError
from yggdrisil.graph.sqlite import SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.objective import Objective
from yggdrisil.policy import Decision, Policy, Proposal
from yggdrisil.problem import Problem
from yggdrisil.serialize import stable_hash
from yggdrisil.types import RunRecord, RunResult

State = TypeVar("State")
Action = TypeVar("Action")


def _utcnow_id() -> str:
    return datetime.now(UTC).strftime("run_%Y_%m_%d_%H%M%S_%f")


class Runner(Generic[State, Action]):
    """Evaluate states, persist decisions, and materialize proposed transitions."""

    def __init__(
        self,
        problem: Problem[State, Action],
        policy: Policy[Action],
        graph: SQLiteStateGraph[State, Action],
        limits: RunLimits,
        *,
        objective: Objective[State] | None = None,
        evaluators: EvaluatorSuite[State] | None = None,
        run_id: str | None = None,
        resume: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.problem = problem
        self.policy = policy
        self.graph = graph
        self.limits = limits
        self.objective = objective
        self.evaluators = evaluators
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
        self._check_new_run_id()
        restored = self._restore()
        self._check_problem_fingerprint(restored or self.graph.latest_run())
        self._ensure_initial_state()
        goal_found = self._prepare_objective_scores()
        self._persist("running")
        stop_reason = "completed"

        try:
            evaluations_complete = await self._evaluate_existing_states(started)
            if not evaluations_complete:
                stop_reason = "max_wall_time_s"
            if restored is not None and stop_reason == "completed":
                recovered_stop = await self._recover_uncheckpointed_step(
                    goal_found=goal_found,
                    started=started,
                )
                if recovered_stop is not None:
                    stop_reason = recovered_stop
            if stop_reason == "completed" and goal_found:
                stop_reason = "objective"

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

                decisions = await self._policy_step(elapsed)
                if decisions is None:
                    stop_reason = "max_wall_time_s"
                    break
                proposal_events = self._record_decisions(decisions)
                if not proposal_events:
                    stop_reason = "no_proposals"
                    break

                stopped = False
                for index, (proposal, event_id) in enumerate(proposal_events):
                    elapsed = time.monotonic() - started
                    hit = self.limits.reached(
                        unique_states=len(self.graph),
                        step=self._step,
                        elapsed_s=elapsed,
                    )
                    if hit:
                        stop_reason = hit
                        self._skip_events(proposal_events[index:], f"skipped_{hit}")
                        stopped = True
                        break
                    try:
                        goal, child_id, edge_id, edge_created = self._apply(proposal)
                    except Exception as exc:
                        self.graph.finish_proposal_event(
                            event_id,
                            outcome="failed",
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        self._skip_events(
                            proposal_events[index + 1 :],
                            "skipped_failure",
                        )
                        raise
                    self.graph.finish_proposal_event(
                        event_id,
                        outcome="created" if edge_created else "reused",
                        child_id=child_id,
                        edge_id=edge_id,
                    )
                    try:
                        evaluations_complete = await self._evaluate_state(
                            child_id,
                            started,
                        )
                    except Exception:
                        self._skip_events(
                            proposal_events[index + 1 :],
                            "skipped_failure",
                        )
                        raise
                    if not evaluations_complete:
                        stop_reason = "max_wall_time_s"
                        self._skip_events(
                            proposal_events[index + 1 :],
                            "skipped_max_wall_time_s",
                        )
                        stopped = True
                        break
                    if goal:
                        stop_reason = "objective"
                        self._skip_events(
                            proposal_events[index + 1 :],
                            "skipped_objective",
                        )
                        stopped = True
                        break

                self._step += 1
                self._persist("running")
                if stopped:
                    break
        except Exception as exc:
            self._fail_pending_events(exc)
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

    async def _policy_step(self, elapsed_s: float) -> list[Decision[Action]] | None:
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

    def _check_new_run_id(self) -> None:
        if self.resume:
            return
        try:
            self.graph.get_run(self.run_id)
        except KeyError:
            return
        raise GraphError(
            f"run {self.run_id!r} already exists; resume it or choose a new run_id"
        )

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
        stored_metadata = dict(record.metadata)
        for key in ("best_state_id", "best_score", "stop_reason"):
            stored_metadata.pop(key, None)
        stored_metadata.update(self.metadata)
        self.metadata = stored_metadata
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
        node = self.graph.add_state(
            state_id,
            state,
            metadata=metadata,
            created_step=0,
        )
        self._consider(node.state_id, score)

    def _record_decisions(
        self,
        decisions: list[Decision[Action]],
    ) -> list[tuple[Proposal[Action], str]]:
        policy_name = f"{type(self.policy).__module__}:{type(self.policy).__qualname__}"
        recorded: list[tuple[Proposal[Action], str]] = []
        created_step = self._step + 1
        decision_offset, sequence_index = self.graph.provenance_counts(
            self.run_id,
            created_step,
        )
        for local_index, decision in enumerate(decisions):
            decision_index = decision_offset + local_index
            payload = {
                "run_id": self.run_id,
                "created_step": created_step,
                "decision_index": decision_index,
                "policy": policy_name,
                "role": decision.role,
                "model": decision.model,
                "selected_state_ids": decision.selected_state_ids,
                "input_context": decision.input_context,
                "tool_calls": decision.tool_calls,
                "output": decision.output,
                "metadata": decision.metadata,
                "proposals": [
                    {
                        "parent_id": proposal.parent_id,
                        "action": proposal.action,
                        "metadata": proposal.metadata,
                    }
                    for proposal in decision.proposals
                ],
            }
            decision_id = stable_hash(payload)
            self.graph.add_decision(
                decision_id,
                run_id=self.run_id,
                policy=policy_name,
                role=decision.role,
                model=decision.model,
                selected_state_ids=list(decision.selected_state_ids),
                input_context=decision.input_context,
                tool_calls=list(decision.tool_calls),
                output=decision.output,
                metadata=dict(decision.metadata),
                created_step=created_step,
            )
            for proposal_index, proposal in enumerate(decision.proposals):
                event_id = stable_hash(
                    {
                        "decision_id": decision_id,
                        "proposal_index": proposal_index,
                    }
                )
                event = self.graph.add_proposal_event(
                    event_id,
                    decision_id=decision_id,
                    run_id=self.run_id,
                    parent_id=proposal.parent_id,
                    action=proposal.action,
                    metadata=dict(proposal.metadata),
                    created_step=created_step,
                    proposal_index=proposal_index,
                    sequence_index=sequence_index,
                )
                sequence_index += 1
                if event.outcome == "pending":
                    recorded.append((proposal, event_id))
        return recorded

    async def _recover_uncheckpointed_step(
        self,
        *,
        goal_found: bool,
        started: float,
    ) -> str | None:
        events = [
            event
            for event in self.graph.proposal_events(run_id=self.run_id)
            if event.created_step > self._step
        ]
        if not events:
            return None

        created_step = min(event.created_step for event in events)
        events = [event for event in events if event.created_step == created_step]
        last_failure = max(
            (
                event.sequence_index
                for event in events
                if event.outcome in {"failed", "skipped_failure"}
            ),
            default=-1,
        )
        if last_failure >= 0:
            events = [event for event in events if event.sequence_index > last_failure]
        if not events:
            return None

        skip_outcome: str | None = None
        if goal_found:
            skip_outcome = "skipped_objective"
        else:
            skip_outcome = next(
                (
                    event.outcome
                    for event in events
                    if event.outcome.startswith("skipped_")
                ),
                None,
            )
        if skip_outcome is not None:
            for event in events:
                if event.outcome == "pending":
                    self.graph.finish_proposal_event(
                        event.event_id,
                        outcome=skip_outcome,
                    )
            self._step = created_step
            self._persist("running")
            return "objective" if skip_outcome == "skipped_objective" else None

        stop_reason: str | None = None
        materialized = False
        for index, event in enumerate(events):
            if event.outcome != "pending":
                if event.outcome in {"created", "reused"}:
                    materialized = True
                continue
            if materialized:
                hit = self.limits.reached(
                    unique_states=len(self.graph),
                    step=self._step,
                    elapsed_s=0.0,
                )
                if hit is not None:
                    for remaining in events[index:]:
                        if remaining.outcome == "pending":
                            self.graph.finish_proposal_event(
                                remaining.event_id,
                                outcome=f"skipped_{hit}",
                            )
                    break
            proposal = Proposal(
                parent_id=event.parent_id,
                action=event.action,
                metadata=dict(event.metadata),
            )
            try:
                goal, child_id, edge_id, edge_created = self._apply(
                    proposal,
                    created_step=created_step,
                )
            except Exception as exc:
                self.graph.finish_proposal_event(
                    event.event_id,
                    outcome="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                for remaining in events[index + 1 :]:
                    if remaining.outcome == "pending":
                        self.graph.finish_proposal_event(
                            remaining.event_id,
                            outcome="skipped_failure",
                        )
                raise
            self.graph.finish_proposal_event(
                event.event_id,
                outcome="created" if edge_created else "reused",
                child_id=child_id,
                edge_id=edge_id,
            )
            materialized = True
            try:
                evaluations_complete = await self._evaluate_state(
                    child_id,
                    started,
                )
            except Exception:
                for remaining in events[index + 1 :]:
                    if remaining.outcome == "pending":
                        self.graph.finish_proposal_event(
                            remaining.event_id,
                            outcome="skipped_failure",
                        )
                raise
            if not evaluations_complete:
                stop_reason = "max_wall_time_s"
                for remaining in events[index + 1 :]:
                    if remaining.outcome == "pending":
                        self.graph.finish_proposal_event(
                            remaining.event_id,
                            outcome="skipped_max_wall_time_s",
                        )
                break
            if goal:
                stop_reason = "objective"
                for remaining in events[index + 1 :]:
                    if remaining.outcome == "pending":
                        self.graph.finish_proposal_event(
                            remaining.event_id,
                            outcome="skipped_objective",
                        )
                break

        self._step = created_step
        self._persist("running")
        return stop_reason

    async def _evaluate_existing_states(self, started: float) -> bool:
        if self.evaluators is None:
            return True
        for node in self.graph.states():
            if not await self._evaluate_state(node.state_id, started):
                return False
        return True

    async def _evaluate_state(self, state_id: str, started: float) -> bool:
        if self.evaluators is None:
            return True
        remaining = self._remaining_wall_time(time.monotonic() - started)
        if remaining is None:
            await self.evaluators.evaluate_cached(self.graph, state_id)
            return True
        if remaining <= 0:
            return False
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                await self.evaluators.evaluate_cached(self.graph, state_id)
        except TimeoutError:
            if timeout.expired():
                return False
            raise
        return True

    def _skip_events(
        self,
        proposal_events: list[tuple[Proposal[Action], str]],
        outcome: str,
    ) -> None:
        for _, event_id in proposal_events:
            self.graph.finish_proposal_event(event_id, outcome=outcome)

    def _fail_pending_events(self, exc: Exception) -> None:
        error = f"batch aborted: {type(exc).__name__}: {exc}"
        for event in self.graph.proposal_events(run_id=self.run_id):
            if event.created_step > self._step and event.outcome == "pending":
                self.graph.finish_proposal_event(
                    event.event_id,
                    outcome="skipped_failure",
                    error=error,
                )

    def _apply(
        self,
        proposal: Proposal[Action],
        *,
        created_step: int | None = None,
    ) -> tuple[bool, str, str, bool]:
        parent = self.graph.get_state(proposal.parent_id)
        validate_action = getattr(self.problem, "validate_action", None)
        if validate_action is not None:
            validate_action(parent.state, proposal.action)
        child = self.problem.apply(parent.state, proposal.action)
        validate_state = getattr(self.problem, "validate_state", None)
        if validate_state is not None:
            validate_state(child)
        child_id = self.problem.state_key(child)
        score = self._score(child)
        transition_step = self._step + 1 if created_step is None else created_step
        node, edge, _, edge_created = self.graph.add_transition(
            parent_id=proposal.parent_id,
            child_id=child_id,
            child=child,
            action=proposal.action,
            edge_metadata=dict(proposal.metadata),
            created_step=transition_step,
        )
        self._consider(node.state_id, score)
        goal = bool(self.objective is not None and self.objective.goal_reached(child))
        return goal, node.state_id, edge.edge_id, edge_created

    def _prepare_objective_scores(self) -> bool:
        if self.objective is None:
            return False
        self._best_state_id = None
        self._best_score = None
        goal_found = False
        for node in self.graph.states():
            score = self._score(node.state)
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
