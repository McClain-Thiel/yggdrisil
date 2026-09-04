from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from make24 import Combine, Make24

from tests.support import ScriptedPolicy
from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits
from yggdrisil.policy import Proposal
from yggdrisil.runner import Runner


class SlowPolicy:
    async def step(self, graph, status):
        await asyncio.sleep(1)
        return []


class FailingPolicy:
    async def step(self, graph, status):
        raise TimeoutError("policy failed")


@pytest.mark.asyncio
async def test_max_states_stops_before_exceeding(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    actions = [
        Combine("1", "3", "+"),
        Combine("1", "4", "+"),
        Combine("1", "6", "+"),
        Combine("3", "4", "+"),
        Combine("3", "6", "+"),
        Combine("4", "6", "+"),
    ]
    policy = ScriptedPolicy(
        [[Proposal(parent_id=start_id, action=action)] for action in actions]
    )
    result = await Runner(problem, policy, graph, RunLimits(max_states=3)).run()
    assert result.stop_reason == "max_states"
    assert result.unique_states == 3


@pytest.mark.asyncio
async def test_max_steps_counts_policy_calls(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    policy = ScriptedPolicy(
        [
            [Proposal(parent_id=start_id, action=Combine("1", "3", "+"))],
            [Proposal(parent_id=start_id, action=Combine("1", "4", "+"))],
            [Proposal(parent_id=start_id, action=Combine("1", "6", "+"))],
        ]
    )
    result = await Runner(problem, policy, graph, RunLimits(max_steps=2)).run()
    assert result.stop_reason == "max_steps"
    assert result.step == 2
    assert result.unique_states == 3


@pytest.mark.asyncio
async def test_max_wall_time_stops(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    result = await Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id="unused", action=Combine("1", "3", "+"))]]),
        graph,
        RunLimits(max_wall_time_s=0.0),
    ).run()
    assert result.stop_reason == "max_wall_time_s"
    assert result.unique_states == 1
    assert result.edges == 0


@pytest.mark.asyncio
async def test_max_wall_time_interrupts_policy_call(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    started = time.monotonic()
    result = await Runner(
        Make24(),
        SlowPolicy(),
        graph,
        RunLimits(max_wall_time_s=0.02),
    ).run()
    assert result.stop_reason == "max_wall_time_s"
    assert time.monotonic() - started < 0.5


@pytest.mark.asyncio
async def test_policy_timeout_error_is_not_mistaken_for_limit(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    with pytest.raises(TimeoutError, match="policy failed"):
        await Runner(
            Make24(),
            FailingPolicy(),
            graph,
            RunLimits(max_wall_time_s=1),
        ).run()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_states": -1},
        {"max_steps": -1},
        {"max_wall_time_s": -0.1},
        {"max_evaluation_cost": -0.1},
    ],
)
def test_negative_limits_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError, match="positive|non-negative"):
        RunLimits(**kwargs)


def test_zero_max_states_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_states must be positive"):
        RunLimits(max_states=0)


@pytest.mark.parametrize("value", [True, float("inf"), float("nan")])
def test_invalid_evaluation_cost_limits_are_rejected(value: float) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        RunLimits(max_evaluation_cost=value)
