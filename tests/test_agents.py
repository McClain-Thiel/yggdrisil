from __future__ import annotations

from pathlib import Path

import pytest
from make24 import Combine, Make24, tiny_policy

from yggdrisil.agents import (
    ExplorationRequest,
    ExplorerResult,
    NavigationPlan,
    NavigatorExplorerPolicy,
)
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
    policy = NavigatorExplorerPolicy(FixedNavigator(start_id), explorer, goal="make 24")
    status = RunStatus(
        step=0,
        unique_states=1,
        edges=0,
        elapsed_s=0.0,
        limits=RunLimits(max_steps=1),
    )
    proposals = await policy.step(graph.readonly(), status)
    assert {p.parent_id for p in proposals} == {start_id}
    assert [p.action for p in proposals] == [
        Combine("1", "3", "+"),
        Combine("4", "6", "*"),
    ]
    assert explorer.calls == 1
    assert proposals[0].metadata["trace"][0]["tool"] == "add"


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
    for edge in graph.edges():
        assert edge.metadata.get("note") == "try these"
        assert edge.metadata.get("trace")
    children = [n for n in graph.states() if n.state_id != start_id]
    assert all(n.state.trace for n in children)
    assert all(n.metadata.get("trace") for n in children)


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
    traced = [n for n in graph.states() if n.state.trace]
    assert traced, "explorer tool traces should land on node.state"
    tools = {step.get("tool") for n in traced for step in n.state.trace}
    assert tools & {"add", "subtract", "multiply", "divide"}
