from __future__ import annotations

from pathlib import Path

import pytest

from yggdrisil.exceptions import UnknownStateError
from make24 import Combine, Make24
from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits
from yggdrisil.policy import Proposal
from yggdrisil.runner import Runner
from tests.support import ScriptedPolicy


@pytest.mark.asyncio
async def test_runner_applies_proposals_and_stops_on_empty(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    policy = ScriptedPolicy(
        [
            [Proposal(parent_id=start_id, action=Combine("1", "3", "+"))],
            [Proposal(parent_id=start_id, action=Combine("4", "6", "+"))],
        ]
    )
    result = await Runner(problem, policy, graph, RunLimits(max_steps=10)).run()
    assert result.stop_reason == "no_proposals"
    assert result.unique_states == 3
    assert result.edges == 2
    assert result.step == 2


@pytest.mark.asyncio
async def test_invalid_parent_does_not_corrupt_graph(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    policy = ScriptedPolicy(
        [[Proposal(parent_id="missing", action=Combine("1", "3", "+"))]]
    )
    with pytest.raises(UnknownStateError):
        await Runner(problem, policy, graph, RunLimits(max_steps=5)).run()
    assert len(graph) == 1
    assert graph.edge_count() == 0


@pytest.mark.asyncio
async def test_invalid_action_does_not_corrupt_graph(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    child_id = problem.state_key(
        problem.apply(problem.initial_state, Combine("1", "3", "+"))
    )
    policy = ScriptedPolicy(
        [
            [Proposal(parent_id=start_id, action=Combine("1", "3", "+"))],
            [Proposal(parent_id=child_id, action=Combine("1", "3", "+"))],
        ]
    )
    with pytest.raises(ValueError, match="pool"):
        await Runner(problem, policy, graph, RunLimits(max_steps=5)).run()
    assert len(graph) == 2
    assert graph.edge_count() == 1


@pytest.mark.asyncio
async def test_parallel_proposals_in_one_step(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    policy = ScriptedPolicy(
        [
            [
                Proposal(parent_id=start_id, action=Combine("1", "3", "+")),
                Proposal(parent_id=start_id, action=Combine("4", "6", "+")),
            ]
        ]
    )
    result = await Runner(problem, policy, graph, RunLimits(max_steps=10)).run()
    assert result.step == 1
    assert result.unique_states == 3
    assert result.edges == 2


@pytest.mark.asyncio
async def test_resume_continues_step_count(tmp_path: Path) -> None:
    problem = Make24()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    policy = ScriptedPolicy(
        [[Proposal(parent_id=start_id, action=Combine("1", "3", "+"))]]
    )
    first = await Runner(
        problem, policy, graph, RunLimits(max_steps=10), run_id="run_a"
    ).run()
    assert first.step == 1
    graph.close()

    graph = SQLiteStateGraph(path)
    id0 = problem.state_key(problem.apply(problem.initial_state, Combine("1", "3", "+")))
    policy2 = ScriptedPolicy(
        [[Proposal(parent_id=id0, action=Combine("4", "6", "+"))]]
    )
    second = await Runner(
        problem, policy2, graph, RunLimits(max_steps=10), run_id="run_a"
    ).run()
    assert second.step == 2
    assert second.unique_states == 3
