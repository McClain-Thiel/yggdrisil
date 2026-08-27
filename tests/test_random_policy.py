from __future__ import annotations

from pathlib import Path

import pytest
from make24 import Make24

from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.policies import RandomPolicy
from yggdrisil.runner import Runner


@pytest.mark.asyncio
async def test_random_policy_is_deterministic_with_seed(tmp_path: Path) -> None:
    problem = Make24()
    g1 = SQLiteStateGraph(tmp_path / "a.sqlite")
    g2 = SQLiteStateGraph(tmp_path / "b.sqlite")
    r1 = await Runner(
        problem,
        RandomPolicy(problem.sample_actions, n_proposals=2, seed=7),
        g1,
        RunLimits(max_states=12),
    ).run()
    r2 = await Runner(
        problem,
        RandomPolicy(problem.sample_actions, n_proposals=2, seed=7),
        g2,
        RunLimits(max_states=12),
    ).run()
    assert r1.unique_states == r2.unique_states
    assert {n.state_id for n in g1.states()} == {n.state_id for n in g2.states()}
    assert {(e.parent_id, e.child_id, e.action) for e in g1.edges()} == {
        (e.parent_id, e.child_id, e.action) for e in g2.edges()
    }


@pytest.mark.asyncio
async def test_random_policy_cannot_mutate_graph(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    graph.add_state(problem.state_key(problem.initial_state), problem.initial_state)
    policy = RandomPolicy(problem.sample_actions, seed=1)
    view = graph.readonly()
    status = RunStatus(
        step=0,
        unique_states=1,
        edges=0,
        elapsed_s=0.0,
        limits=RunLimits(max_steps=1),
    )
    await policy.step(view, status)
    assert not hasattr(view, "add_state")
    assert len(graph) == 1


@pytest.mark.asyncio
async def test_seeded_random_policy_matches_uninterrupted_run_after_resume(
    tmp_path: Path,
) -> None:
    problem = Make24()
    uninterrupted = SQLiteStateGraph(tmp_path / "uninterrupted.sqlite")
    await Runner(
        problem,
        RandomPolicy(problem.sample_actions, n_proposals=2, seed=11),
        uninterrupted,
        RunLimits(max_steps=4),
        run_id="full",
    ).run()

    path = tmp_path / "resumed.sqlite"
    resumed = SQLiteStateGraph(path)
    await Runner(
        problem,
        RandomPolicy(problem.sample_actions, n_proposals=2, seed=11),
        resumed,
        RunLimits(max_steps=2),
        run_id="resume",
    ).run()
    resumed.close()

    resumed = SQLiteStateGraph(path)
    await Runner(
        problem,
        RandomPolicy(problem.sample_actions, n_proposals=2, seed=11),
        resumed,
        RunLimits(max_steps=4),
        run_id="resume",
    ).run()

    assert {node.state_id for node in resumed.states()} == {
        node.state_id for node in uninterrupted.states()
    }
    assert {
        (edge.parent_id, edge.child_id, edge.action) for edge in resumed.edges()
    } == {
        (edge.parent_id, edge.child_id, edge.action) for edge in uninterrupted.edges()
    }


def test_random_policy_rejects_negative_proposal_count() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        RandomPolicy(lambda state, rng: [], n_proposals=-1)
