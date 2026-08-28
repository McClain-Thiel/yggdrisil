from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from make24 import Combine, Make24, tiny_policy

from yggdrisil.agents import (
    ExplorationRequest,
    ExplorerResult,
    NavigationPlan,
    NavigatorExplorerPolicy,
)
from yggdrisil.agents.pydantic_ai import _trace_from_run
from yggdrisil.evaluation import EvaluationResult, EvaluatorSuite
from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.runner import Runner


class FixedNavigator:
    def __init__(self, state_id: str) -> None:
        self.state_id = state_id

    async def plan(self, context) -> NavigationPlan:
        return NavigationPlan(
            requests=[
                ExplorationRequest(state_id=self.state_id, guidance="try addition")
            ]
        )


class FixedExplorer:
    def __init__(self, actions: list[Combine]) -> None:
        self.actions = actions
        self.calls = 0

    async def explore(self, context) -> ExplorerResult[Combine]:
        self.calls += 1
        assert context.guidance == "try addition"
        return ExplorerResult(
            actions=list(self.actions),
            note="try these",
            trace=[{"tool": "add", "a": "1", "b": "3", "ok": True}],
        )


class PoolViabilityEvaluator:
    name = "pool_viability"
    version = "1"
    config = None

    async def evaluate(self, state) -> EvaluationResult:
        return EvaluationResult(metrics={"viable": len(state.values) == 4})


class PersistedViableSelector:
    def __init__(self, root_id: str, *, stop_after: int = 3) -> None:
        self.root_id = root_id
        self.stop_after = stop_after
        self.seen_attempts: list[int] = []

    def select(self, graph, status) -> list[ExplorationRequest]:
        assert status.run_id is not None
        attempts = [
            event
            for event in graph.proposal_events(
                run_id=status.run_id,
                state_id=self.root_id,
            )
            if event.parent_id == self.root_id
        ]
        self.seen_attempts.append(len(attempts))
        viability = {
            record.metrics.get("viable") for record in graph.evaluations(self.root_id)
        }
        assert viability == {True}
        if len(attempts) >= self.stop_after:
            return []
        return [
            ExplorationRequest(
                state_id=self.root_id,
                guidance=f"prior_attempts={len(attempts)}",
            )
        ]


class RecoveryExplorer:
    async def explore(self, context) -> ExplorerResult[Combine]:
        if context.guidance == "prior_attempts=0":
            actions = [Combine("1", "3", "+"), Combine("4", "6", "+")]
        else:
            actions = [Combine("1", "4", "+")]
        return ExplorerResult(actions=actions, note=context.guidance)


class FailingExplorer:
    async def explore(self, context) -> ExplorerResult[Combine]:
        raise RuntimeError("provider unavailable")


class SleepingExplorer:
    async def explore(self, context) -> ExplorerResult[Combine]:
        await asyncio.sleep(1)
        return ExplorerResult(actions=[])


class TwoCandidateSelector:
    def __init__(self, first_id: str, second_id: str) -> None:
        self.candidates = [first_id, second_id]

    def select(self, graph, status) -> list[ExplorationRequest]:
        assert status.run_id is not None
        attempts = [
            decision
            for decision in graph.decisions(run_id=status.run_id)
            if decision.role == "explorer"
        ]
        if len(attempts) >= len(self.candidates):
            return []
        return [
            ExplorationRequest(
                state_id=self.candidates[len(attempts)],
                guidance=f"candidate={len(attempts)}",
            )
        ]


class EmptyThenActionExplorer:
    async def explore(self, context) -> ExplorerResult[Combine]:
        if context.guidance == "candidate=0":
            return ExplorerResult(actions=[], note="no action for first candidate")
        return ExplorerResult(
            actions=[Combine("4", "6", "+")],
            note="second candidate has an action",
        )


@pytest.mark.asyncio
async def test_explorer_only_proposes_direct_children(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    explorer = FixedExplorer([Combine("1", "3", "+"), Combine("4", "6", "*")])
    navigator = FixedNavigator(start_id)
    navigator.last_trace = [{"role": "usage", "input_tokens": 12}]
    policy = NavigatorExplorerPolicy(navigator, explorer, goal="make 24")
    status = RunStatus(
        step=0,
        unique_states=1,
        edges=0,
        elapsed_s=0.0,
        limits=RunLimits(max_steps=1),
    )
    decisions = await policy.step(graph.readonly(), status)
    assert [decision.role for decision in decisions] == ["navigator", "explorer"]
    assert decisions[0].tool_calls == [{"role": "usage", "input_tokens": 12}]
    proposals = decisions[1].proposals
    assert {proposal.parent_id for proposal in proposals} == {start_id}
    assert [proposal.action for proposal in proposals] == [
        Combine("1", "3", "+"),
        Combine("4", "6", "*"),
    ]
    assert explorer.calls == 1
    assert decisions[1].tool_calls[0]["tool"] == "add"


def test_pydantic_ai_trace_includes_usage_without_messages() -> None:
    class Usage:
        requests = 2
        tool_calls = 3
        input_tokens = 120
        output_tokens = 40
        cache_read_tokens = 10
        cache_write_tokens = 5
        cost = 0.0012

    class Result:
        def all_messages(self):
            return []

        def usage(self):
            return Usage()

    assert _trace_from_run(Result()) == [
        {
            "role": "usage",
            "requests": 2,
            "tool_calls": 3,
            "input_tokens": 120,
            "output_tokens": 40,
            "cache_read_tokens": 10,
            "cache_write_tokens": 5,
            "cost_usd": "0.0012",
        }
    ]


@pytest.mark.asyncio
async def test_agent_policy_with_runner(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    explorer = FixedExplorer([Combine("1", "3", "+"), Combine("4", "6", "+")])
    policy = NavigatorExplorerPolicy(FixedNavigator(start_id), explorer)
    result = await Runner(problem, policy, graph, RunLimits(max_steps=1)).run()
    assert result.unique_states == 3
    assert result.edges == 2
    decisions = graph.decisions(result.run_id)
    explorer_decisions = [d for d in decisions if d.role == "explorer"]
    assert explorer_decisions[0].metadata["note"] == "try these"
    assert explorer_decisions[0].tool_calls[0]["tool"] == "add"
    events = graph.proposal_events(decision_id=explorer_decisions[0].decision_id)
    assert len(events) == 2
    assert {event.outcome for event in events} == {"created"}
    assert all(event.edge_id for event in events)


@pytest.mark.asyncio
async def test_llm_navigator_empty_explorer_remains_terminal(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "llm-empty.sqlite")
    root_id = problem.state_key(problem.initial_state)
    explorer = FixedExplorer([])

    result = await Runner(
        problem,
        NavigatorExplorerPolicy(FixedNavigator(root_id), explorer),
        graph,
        RunLimits(max_steps=3),
        run_id="llm-empty",
        resume=False,
    ).run()

    assert result.stop_reason == "no_proposals"
    assert explorer.calls == 1


@pytest.mark.asyncio
async def test_selector_reopens_viable_non_leaf_from_persisted_run_history(
    tmp_path: Path,
) -> None:
    problem = Make24()
    path = tmp_path / "recoverable.sqlite"
    graph = SQLiteStateGraph(path)
    root_id = problem.state_key(problem.initial_state)
    evaluators = EvaluatorSuite([PoolViabilityEvaluator()])

    first_selector = PersistedViableSelector(root_id)
    first = await Runner(
        problem,
        NavigatorExplorerPolicy(
            None,
            RecoveryExplorer(),
            max_requests=1,
            request_selector=first_selector,
        ),
        graph,
        RunLimits(max_steps=1),
        evaluators=evaluators,
        run_id="recoverable",
        resume=False,
    ).run()

    assert first_selector.seen_attempts == [0]
    assert len(graph.children(root_id)) == 2
    assert all(
        graph.evaluations(child.state_id)[0].metrics["viable"] is False
        for child in graph.children(root_id)
    )

    graph.save_run("foreign", step=0, status="completed")
    graph.add_decision(
        "foreign-decision",
        run_id="foreign",
        policy="test",
        role="explorer",
        model=None,
        selected_state_ids=[root_id],
        input_context=None,
        tool_calls=[],
        output=None,
        metadata={},
        created_step=1,
    )
    graph.add_proposal_event(
        "foreign-event",
        decision_id="foreign-decision",
        run_id="foreign",
        parent_id=root_id,
        action=Combine("3", "4", "+"),
        metadata={},
        created_step=1,
        proposal_index=0,
        sequence_index=0,
    )
    graph.finish_proposal_event("foreign-event", outcome="skipped_foreign")

    resumed_selector = PersistedViableSelector(root_id)
    resumed = await Runner(
        problem,
        NavigatorExplorerPolicy(
            None,
            RecoveryExplorer(),
            max_requests=1,
            request_selector=resumed_selector,
        ),
        graph,
        RunLimits(max_steps=2),
        evaluators=evaluators,
        run_id=first.run_id,
    ).run()

    assert resumed.stop_reason == "max_steps"
    assert resumed_selector.seen_attempts == [2]
    assert len(graph.children(root_id)) == 3
    current_events = graph.proposal_events(run_id=first.run_id)
    assert len(current_events) == 3
    second_selection = next(
        decision
        for decision in graph.decisions(first.run_id)
        if decision.created_step == 2 and decision.role == "navigator"
    )
    assert second_selection.selected_state_ids == [root_id]
    assert second_selection.metadata["request_source"] == "selector"
    assert second_selection.input_context["run_id"] == first.run_id

    exhausted_selector = PersistedViableSelector(root_id)
    exhausted = await Runner(
        problem,
        NavigatorExplorerPolicy(
            None,
            RecoveryExplorer(),
            max_requests=1,
            request_selector=exhausted_selector,
        ),
        graph,
        RunLimits(max_steps=3),
        evaluators=evaluators,
        run_id=first.run_id,
    ).run()

    assert exhausted.stop_reason == "no_proposals"
    assert exhausted_selector.seen_attempts == [3]
    empty_selection = graph.decisions(first.run_id, limit=1, newest=True)[0]
    assert empty_selection.role == "navigator"
    assert empty_selection.selected_state_ids == []
    assert empty_selection.output == {"requests": []}

    must_not_select = PersistedViableSelector(root_id)
    repeated = await Runner(
        problem,
        NavigatorExplorerPolicy(
            None,
            RecoveryExplorer(),
            max_requests=1,
            request_selector=must_not_select,
        ),
        graph,
        RunLimits(max_steps=3),
        evaluators=evaluators,
        run_id=first.run_id,
    ).run()
    assert repeated.stop_reason == "max_steps"
    assert must_not_select.seen_attempts == []
    assert len(graph.decisions(first.run_id)) == 5


@pytest.mark.asyncio
async def test_explorer_failure_persists_selection_and_failed_attempt(
    tmp_path: Path,
) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "failed-explorer.sqlite")
    root_id = problem.state_key(problem.initial_state)
    selector = PersistedViableSelector(root_id)
    policy = NavigatorExplorerPolicy(
        None,
        FailingExplorer(),
        request_selector=selector,
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await Runner(
            problem,
            policy,
            graph,
            RunLimits(max_steps=1),
            evaluators=EvaluatorSuite([PoolViabilityEvaluator()]),
            run_id="failed-explorer",
            resume=False,
        ).run()

    decisions = graph.decisions("failed-explorer")
    assert [decision.role for decision in decisions] == ["navigator", "explorer"]
    assert decisions[0].selected_state_ids == [root_id]
    assert decisions[1].selected_state_ids == [root_id]
    assert decisions[1].metadata == {
        "attempt_status": "failed",
        "error": "provider unavailable",
        "error_type": "RuntimeError",
    }
    assert decisions[1].output == {"error": "RuntimeError: provider unavailable"}
    assert graph.proposal_events(run_id="failed-explorer") == []
    assert graph.get_run("failed-explorer").status == "failed"


@pytest.mark.asyncio
async def test_empty_selected_attempt_continues_to_another_candidate(
    tmp_path: Path,
) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "empty-selected.sqlite")
    first_id = problem.state_key(problem.initial_state)
    graph.add_state(first_id, problem.initial_state)
    second = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    second_id = problem.state_key(second)
    graph.add_state(second_id, second)
    selector = TwoCandidateSelector(first_id, second_id)

    result = await Runner(
        problem,
        NavigatorExplorerPolicy(
            None,
            EmptyThenActionExplorer(),
            max_requests=1,
            request_selector=selector,
        ),
        graph,
        RunLimits(max_steps=3),
        run_id="empty-selected",
        resume=False,
    ).run()

    assert result.stop_reason == "no_proposals"
    assert result.step == 3
    decisions = graph.decisions(result.run_id)
    assert [decision.role for decision in decisions] == [
        "navigator",
        "explorer",
        "navigator",
        "explorer",
        "navigator",
    ]
    assert decisions[0].selected_state_ids == [first_id]
    assert decisions[1].output == {
        "actions": [],
        "note": "no action for first candidate",
    }
    assert decisions[2].selected_state_ids == [second_id]
    assert decisions[-1].selected_state_ids == []
    assert decisions[0].metadata["_yggdrisil_continue_on_empty"] is True
    assert "_yggdrisil_continue_on_empty" not in decisions[-1].metadata
    assert len(graph.proposal_events(run_id=result.run_id)) == 1


@pytest.mark.asyncio
async def test_wall_timeout_persists_selection_and_interrupted_explorer(
    tmp_path: Path,
) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "timed-out-explorer.sqlite")
    root_id = problem.state_key(problem.initial_state)
    selector = PersistedViableSelector(root_id)

    result = await Runner(
        problem,
        NavigatorExplorerPolicy(
            None,
            SleepingExplorer(),
            request_selector=selector,
        ),
        graph,
        RunLimits(max_wall_time_s=0.01),
        evaluators=EvaluatorSuite([PoolViabilityEvaluator()]),
        run_id="timed-out-explorer",
        resume=False,
    ).run()

    assert result.stop_reason == "max_wall_time_s"
    assert result.step == 1
    decisions = graph.decisions(result.run_id)
    assert [decision.role for decision in decisions] == ["navigator", "explorer"]
    assert decisions[0].selected_state_ids == [root_id]
    assert decisions[1].selected_state_ids == [root_id]
    assert decisions[1].metadata["attempt_status"] == "failed"
    assert decisions[1].metadata["error_type"] == "CancelledError"
    assert decisions[1].metadata["error"] == "policy step interrupted"
    assert graph.proposal_events(run_id=result.run_id) == []
    assert graph.get_run(result.run_id).status == "completed"


@pytest.mark.asyncio
async def test_tiny_lm_plays_with_tools_and_can_hit_24(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    result = await Runner(
        problem,
        tiny_policy(seed=0, problem=problem),
        graph,
        RunLimits(max_states=40, max_steps=20),
    ).run()
    assert result.unique_states > 1
    for node in graph.states():
        problem.validate_state(node.state)
    for edge in graph.edges():
        parent = graph.get_state(edge.parent_id)
        problem.validate_action(parent.state, edge.action)
    hits = [n for n in graph.states() if problem.solved(n.state)]
    assert hits, "tiny LM should reach 24 in a short search"
    explorer_decisions = [
        decision
        for decision in graph.decisions(result.run_id)
        if decision.role == "explorer"
    ]
    assert explorer_decisions, "explorer decisions should be persisted"
    tools = {
        step.get("tool")
        for decision in explorer_decisions
        for step in decision.tool_calls
    }
    assert tools & {"add", "subtract", "multiply", "divide"}
