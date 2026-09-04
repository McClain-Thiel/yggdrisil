from __future__ import annotations

from pathlib import Path

import pytest
from make24 import Combine, Make24, tiny_policy

from yggdrisil.agents import (
    ExplorationRequest,
    ExplorerContext,
    ExplorerResult,
    NavigationPlan,
    NavigatorExplorerPolicy,
)
from yggdrisil.agents.pydantic_ai import PydanticAIExplorer, _trace_from_run
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
async def test_pydantic_ai_explorer_preserves_custom_trace_and_usage() -> None:
    class Usage:
        requests = 1
        cost = 0.001

    class Result:
        output = ExplorerResult(
            actions=["action"],
            trace=[{"role": "tool_call", "tool": "custom"}],
        )

        def all_messages(self):
            return []

        def usage(self):
            return Usage()

    class Agent:
        async def run(self, prompt: str) -> Result:
            assert prompt
            return Result()

    explorer = PydanticAIExplorer[str, str](Agent())
    result = await explorer.explore(
        ExplorerContext(
            goal=None,
            state_id="state",
            state="state",
            lineage=[],
            guidance=None,
        )
    )

    assert result.trace == [
        {"role": "tool_call", "tool": "custom"},
        {
            "role": "usage",
            "requests": 1,
            "tool_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": "0.001",
        },
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
