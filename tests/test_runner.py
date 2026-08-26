from __future__ import annotations

from pathlib import Path

import pytest
from make24 import Combine, Make24

from tests.support import ScriptedPolicy
from yggdrisil.exceptions import GraphError, SerializationError, UnknownStateError
from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits
from yggdrisil.objective import Objective
from yggdrisil.policies import BestFirstPolicy
from yggdrisil.policy import Proposal
from yggdrisil.runner import Runner


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
    id0 = problem.state_key(
        problem.apply(problem.initial_state, Combine("1", "3", "+"))
    )
    policy2 = ScriptedPolicy([[Proposal(parent_id=id0, action=Combine("4", "6", "+"))]])
    second = await Runner(
        problem, policy2, graph, RunLimits(max_steps=10), run_id="run_a"
    ).run()
    assert second.step == 2
    assert second.unique_states == 3


@pytest.mark.asyncio
async def test_explicit_new_run_id_is_not_replaced_by_latest(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    await Runner(
        problem,
        ScriptedPolicy([]),
        graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()

    result = await Runner(
        problem,
        ScriptedPolicy([]),
        graph,
        RunLimits(max_steps=1),
        run_id="run_b",
    ).run()
    assert result.run_id == "run_b"
    assert graph.get_run("run_a").run_id == "run_a"
    assert graph.get_run("run_b").run_id == "run_b"


@pytest.mark.asyncio
async def test_reusing_graph_for_different_problem_is_rejected(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    await Runner(
        Make24(),
        ScriptedPolicy([]),
        graph,
        RunLimits(max_steps=1),
    ).run()
    with pytest.raises(GraphError, match="separate database"):
        await Runner(
            Make24((2, 3, 5, 7)),
            ScriptedPolicy([]),
            graph,
            RunLimits(max_steps=1),
            resume=False,
        ).run()


@pytest.mark.asyncio
async def test_problem_configuration_fingerprint_is_checked(tmp_path: Path) -> None:
    class ConfiguredProblem:
        initial_state = 0

        def __init__(self, increment: int) -> None:
            self.increment = increment

        def state_key(self, value: int) -> str:
            return str(value)

        def apply(self, value: int, action: int) -> int:
            return value + self.increment

    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    await Runner(
        ConfiguredProblem(1),
        ScriptedPolicy([]),
        graph,
        RunLimits(max_steps=1),
    ).run()
    with pytest.raises(GraphError, match="different problem configuration"):
        await Runner(
            ConfiguredProblem(2),
            ScriptedPolicy([]),
            graph,
            RunLimits(max_steps=1),
            resume=False,
        ).run()


@pytest.mark.asyncio
async def test_transition_serialization_failure_leaves_no_child(
    tmp_path: Path,
) -> None:
    class BadAction:
        pass

    class IntegerProblem:
        initial_state = 0

        def state_key(self, value: int) -> str:
            return str(value)

        def apply(self, value: int, action: BadAction) -> int:
            return value + 1

    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    policy = ScriptedPolicy([[Proposal(parent_id="0", action=BadAction())]])
    with pytest.raises(SerializationError, match="cannot serialize"):
        await Runner(
            IntegerProblem(),
            policy,
            graph,
            RunLimits(max_steps=2),
        ).run()
    assert len(graph) == 1
    assert graph.edge_count() == 0


@pytest.mark.asyncio
async def test_objective_tracks_best_and_stops_on_goal(tmp_path: Path) -> None:
    problem = Make24()
    actions = [
        Combine("3", "4", "/"),
        Combine("1", "3/4", "-"),
        Combine("6", "1/4", "/"),
    ]
    parents = []
    current = problem.initial_state
    for action in actions:
        parents.append(problem.state_key(current))
        current = problem.apply(current, action)
    policy = ScriptedPolicy(
        [
            [Proposal(parent_id=parent, action=action)]
            for parent, action in zip(parents, actions)
        ]
    )
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    result = await Runner(
        problem,
        policy,
        graph,
        RunLimits(max_steps=10),
        objective=Objective(
            score=lambda state: -problem.distance(state),
            goal_reached=problem.solved,
        ),
    ).run()

    assert result.stop_reason == "objective"
    assert result.best_state_id == problem.state_key(current)
    assert result.best_score == 0.0
    assert (
        graph.get_run(result.run_id).metadata["best_state_id"] == result.best_state_id
    )

    class MustNotRun:
        async def step(self, graph, status):
            raise AssertionError("completed objective should not call the policy")

    resumed = await Runner(
        problem,
        MustNotRun(),
        graph,
        RunLimits(max_steps=10),
        objective=Objective(
            score=lambda state: -problem.distance(state),
            goal_reached=problem.solved,
        ),
        run_id=result.run_id,
    ).run()
    assert resumed.stop_reason == "objective"


@pytest.mark.asyncio
async def test_objective_scores_are_backfilled_for_existing_graph(
    tmp_path: Path,
) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    first = await Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id=start_id, action=Combine("1", "3", "+"))]]),
        graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()
    assert first.stop_reason == "max_steps"
    assert all("score" not in node.metadata for node in graph.states())

    resumed = await Runner(
        problem,
        BestFirstPolicy(
            lambda state, rng: problem.legal_actions(state)[:1],
            n_proposals=1,
        ),
        graph,
        RunLimits(max_steps=2),
        objective=Objective(score=lambda state: -problem.distance(state)),
        run_id="run_a",
    ).run()
    assert resumed.stop_reason == "max_steps"
    assert all("score" in node.metadata for node in graph.states())
