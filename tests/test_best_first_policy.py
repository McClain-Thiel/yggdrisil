from __future__ import annotations

from pathlib import Path

import pytest
from make24 import Combine, Make24

from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.policies import BestFirstPolicy


@pytest.mark.asyncio
async def test_best_first_expands_highest_scored_frontier(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    low = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    high = problem.apply(problem.initial_state, Combine("4", "6", "+"))
    low_id = problem.state_key(low)
    high_id = problem.state_key(high)
    graph.add_state(low_id, low)
    graph.add_state(high_id, high)

    seen = []

    def sample(state, rng):
        seen.append(state)
        return problem.legal_actions(state)[:1]

    policy = BestFirstPolicy(
        sample,
        lambda node, evaluations: -problem.distance(node.state),
        n_proposals=1,
        seed=0,
    )
    decisions = await policy.step(
        graph.readonly(),
        RunStatus(
            step=0,
            unique_states=2,
            edges=0,
            elapsed_s=0,
            limits=RunLimits(max_steps=1),
        ),
    )

    assert seen == [high]
    assert decisions[0].proposals[0].parent_id == high_id


@pytest.mark.asyncio
async def test_zero_proposals_returns_empty(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    policy = BestFirstPolicy(
        lambda state, rng: [state],
        lambda node, evaluations: 0.0,
        n_proposals=0,
    )
    decisions = await policy.step(
        graph.readonly(),
        RunStatus(0, 0, 0, 0, RunLimits(max_steps=1)),
    )
    assert decisions == []


def test_invalid_best_first_limits_are_rejected() -> None:
    with pytest.raises(ValueError, match="n_proposals"):
        BestFirstPolicy(
            lambda state, rng: [],
            lambda node, evaluations: 0.0,
            n_proposals=-1,
        )
    with pytest.raises(ValueError, match="frontier_limit"):
        BestFirstPolicy(
            lambda state, rng: [],
            lambda node, evaluations: 0.0,
            frontier_limit=0,
        )
