from __future__ import annotations

import json
from pathlib import Path

import pytest
from make24 import Combine, Make24

from tests.support import ScriptedPolicy
from yggdrisil.exceptions import GraphError, SerializationError, UnknownStateError
from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.limits import RunLimits
from yggdrisil.objective import Objective
from yggdrisil.policies import BestFirstPolicy
from yggdrisil.policy import Decision, Proposal
from yggdrisil.runner import Runner


class SimulatedCrash(BaseException):
    pass


class MustNotRun:
    async def step(self, graph, status):
        raise AssertionError("policy should not run while recovering a saved step")


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
async def test_repeated_proposals_share_edge_but_keep_decisions(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    proposal = Proposal(parent_id=start_id, action=Combine("1", "3", "+"))
    result = await Runner(
        problem,
        ScriptedPolicy([[proposal], [proposal]]),
        graph,
        RunLimits(max_steps=2),
        run_id="run_a",
    ).run()

    assert graph.edge_count() == 1
    assert len(graph.decisions(result.run_id)) == 2
    assert graph.decisions(result.run_id, limit=1, newest=True)[0].created_step == 2
    events = graph.proposal_events()
    assert len(events) == 2
    assert [event.outcome for event in events] == ["created", "reused"]
    assert events[0].edge_id == events[1].edge_id


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
    saved = graph.get_run("run_a")
    assert saved.status == "completed"
    assert saved.step == 1
    assert len(graph.decisions("run_a")) == 1
    assert graph.proposal_events(run_id="run_a")[0].outcome == "created"
    manifest = json.loads(path.with_suffix(".run.json").read_text())
    assert manifest["run_id"] == "run_a"
    assert manifest["step"] == 1
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
async def test_resume_finishes_pending_event_after_transition_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Make24()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    runner = Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id=start_id, action=Combine("1", "3", "+"))]]),
        graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    )
    finish_event = graph.finish_proposal_event

    def crash_before_event_finish(event_id, **kwargs):
        if kwargs["outcome"] in {"created", "reused"}:
            raise SimulatedCrash
        return finish_event(event_id, **kwargs)

    monkeypatch.setattr(graph, "finish_proposal_event", crash_before_event_finish)
    with pytest.raises(SimulatedCrash):
        await runner.run()

    assert graph.edge_count() == 1
    assert graph.proposal_events(run_id="run_a")[0].outcome == "pending"
    assert graph.get_run("run_a").step == 0
    assert graph.get_run("run_a").status == "running"
    graph.close()

    resumed_graph = SQLiteStateGraph(path)
    result = await Runner(
        problem,
        MustNotRun(),
        resumed_graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()

    assert result.step == 1
    assert result.stop_reason == "max_steps"
    event = resumed_graph.proposal_events(run_id="run_a")[0]
    assert event.outcome == "reused"
    assert event.edge_id == resumed_graph.edges()[0].edge_id


@pytest.mark.asyncio
async def test_resume_preserves_pending_proposal_metadata_and_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Make24()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)

    class OrderedPolicy:
        async def step(self, graph, status):
            return [
                Decision(
                    role="first",
                    proposals=[
                        Proposal(
                            parent_id=start_id,
                            action=Combine("1", "3", "+"),
                            metadata={"rank": 1},
                        )
                    ],
                ),
                Decision(
                    role="second",
                    proposals=[
                        Proposal(
                            parent_id=start_id,
                            action=Combine("4", "6", "+"),
                            metadata={"rank": 2},
                        )
                    ],
                ),
            ]

    runner = Runner(
        problem,
        OrderedPolicy(),
        graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    )

    def crash_before_transition(proposal, *, created_step=None):
        raise SimulatedCrash

    monkeypatch.setattr(runner, "_apply", crash_before_transition)
    with pytest.raises(SimulatedCrash):
        await runner.run()

    pending = graph.proposal_events(run_id="run_a")
    assert [event.sequence_index for event in pending] == [0, 1]
    assert [event.metadata for event in pending] == [{"rank": 1}, {"rank": 2}]
    graph.close()

    resumed_graph = SQLiteStateGraph(path)
    result = await Runner(
        problem,
        MustNotRun(),
        resumed_graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()

    assert result.step == 1
    assert {edge.metadata["rank"] for edge in resumed_graph.edges()} == {1, 2}


@pytest.mark.asyncio
async def test_resume_checkpoints_finished_unpersisted_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    problem = Make24()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    runner = Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id=start_id, action=Combine("1", "3", "+"))]]),
        graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    )
    persist = runner._persist
    running_saves = 0

    def crash_before_step_save(status, extra=None):
        nonlocal running_saves
        if status == "running":
            running_saves += 1
            if running_saves == 2:
                raise SimulatedCrash
        return persist(status, extra)

    monkeypatch.setattr(runner, "_persist", crash_before_step_save)
    with pytest.raises(SimulatedCrash):
        await runner.run()

    assert graph.proposal_events(run_id="run_a")[0].outcome == "created"
    assert graph.get_run("run_a").step == 0
    graph.close()

    resumed_graph = SQLiteStateGraph(path)
    result = await Runner(
        problem,
        MustNotRun(),
        resumed_graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()

    assert result.step == 1
    assert result.stop_reason == "max_steps"
    assert resumed_graph.edge_count() == 1


@pytest.mark.asyncio
async def test_resume_does_not_apply_pending_proposals_after_saved_goal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IntegerProblem:
        initial_state = 0

        def state_key(self, state: int) -> str:
            return str(state)

        def apply(self, state: int, action: int) -> int:
            return state + action

    problem = IntegerProblem()
    objective = Objective(score=float, goal_reached=lambda state: state == 1)
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    runner = Runner(
        problem,
        ScriptedPolicy(
            [
                [
                    Proposal(parent_id="0", action=1),
                    Proposal(parent_id="0", action=2),
                ]
            ]
        ),
        graph,
        RunLimits(max_steps=1),
        objective=objective,
        run_id="run_a",
    )
    finish_event = graph.finish_proposal_event

    def crash_while_skipping(event_id, **kwargs):
        if kwargs["outcome"] == "skipped_objective":
            raise SimulatedCrash
        return finish_event(event_id, **kwargs)

    monkeypatch.setattr(graph, "finish_proposal_event", crash_while_skipping)
    with pytest.raises(SimulatedCrash):
        await runner.run()

    assert graph.has_state("1")
    assert not graph.has_state("2")
    assert [event.outcome for event in graph.proposal_events(run_id="run_a")] == [
        "created",
        "pending",
    ]
    graph.close()

    resumed_graph = SQLiteStateGraph(path)
    result = await Runner(
        problem,
        MustNotRun(),
        resumed_graph,
        RunLimits(max_steps=1),
        objective=objective,
        run_id="run_a",
    ).run()

    assert result.stop_reason == "objective"
    assert result.step == 1
    assert not resumed_graph.has_state("2")
    assert [
        event.outcome for event in resumed_graph.proposal_events(run_id="run_a")
    ] == ["created", "skipped_objective"]


@pytest.mark.asyncio
async def test_resume_does_not_exceed_limit_while_finishing_saved_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class IntegerProblem:
        initial_state = 0

        def state_key(self, state: int) -> str:
            return str(state)

        def apply(self, state: int, action: int) -> int:
            return state + action

    problem = IntegerProblem()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    runner = Runner(
        problem,
        ScriptedPolicy(
            [
                [
                    Proposal(parent_id="0", action=1),
                    Proposal(parent_id="0", action=2),
                ]
            ]
        ),
        graph,
        RunLimits(max_states=2),
        run_id="run_a",
    )
    finish_event = graph.finish_proposal_event

    def crash_while_skipping(event_id, **kwargs):
        if kwargs["outcome"] == "skipped_max_states":
            raise SimulatedCrash
        return finish_event(event_id, **kwargs)

    monkeypatch.setattr(graph, "finish_proposal_event", crash_while_skipping)
    with pytest.raises(SimulatedCrash):
        await runner.run()
    assert [event.outcome for event in graph.proposal_events(run_id="run_a")] == [
        "created",
        "pending",
    ]
    graph.close()

    resumed_graph = SQLiteStateGraph(path)
    result = await Runner(
        problem,
        MustNotRun(),
        resumed_graph,
        RunLimits(max_states=2),
        run_id="run_a",
    ).run()

    assert result.stop_reason == "max_states"
    assert result.step == 1
    assert not resumed_graph.has_state("2")
    assert [
        event.outcome for event in resumed_graph.proposal_events(run_id="run_a")
    ] == ["created", "skipped_max_states"]


@pytest.mark.asyncio
async def test_resume_retries_identical_decision_after_failure(tmp_path: Path) -> None:
    class FlakyProblem:
        initial_state = 0

        def __init__(self) -> None:
            self._fail = True

        def state_key(self, state: int) -> str:
            return str(state)

        def apply(self, state: int, action: int) -> int:
            if self._fail:
                raise RuntimeError("transient failure")
            return state + action

    problem = FlakyProblem()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    proposal = Proposal(parent_id="0", action=1)
    with pytest.raises(RuntimeError, match="transient"):
        await Runner(
            problem,
            ScriptedPolicy([[proposal]]),
            graph,
            RunLimits(max_steps=1),
            run_id="run_a",
        ).run()
    assert graph.get_run("run_a").status == "failed"
    assert graph.proposal_events(run_id="run_a")[0].outcome == "failed"
    graph.close()

    problem._fail = False
    resumed_graph = SQLiteStateGraph(path)
    retry = Runner(
        problem,
        ScriptedPolicy([[proposal]]),
        resumed_graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    )

    def crash_before_retry_transition(proposal, *, created_step=None):
        raise SimulatedCrash

    retry._apply = crash_before_retry_transition
    with pytest.raises(SimulatedCrash):
        await retry.run()
    assert [
        event.outcome for event in resumed_graph.proposal_events(run_id="run_a")
    ] == ["failed", "pending"]
    resumed_graph.close()

    resumed_graph = SQLiteStateGraph(path)
    result = await Runner(
        problem,
        MustNotRun(),
        resumed_graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()

    assert result.step == 1
    assert resumed_graph.has_state("1")
    assert len(resumed_graph.decisions("run_a")) == 2
    assert [
        event.outcome for event in resumed_graph.proposal_events(run_id="run_a")
    ] == ["failed", "created"]


@pytest.mark.asyncio
async def test_resume_latest_run_preserves_metadata(tmp_path: Path) -> None:
    problem = Make24()
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    first = await Runner(
        problem,
        ScriptedPolicy([]),
        graph,
        RunLimits(max_steps=1),
        metadata={"experiment": "baseline"},
    ).run()
    graph.close()

    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    second = await Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id=start_id, action=Combine("1", "3", "+"))]]),
        graph,
        RunLimits(max_steps=1),
        metadata={"phase": "resume"},
    ).run()

    assert second.run_id == first.run_id
    metadata = graph.get_run(first.run_id).metadata
    assert metadata["experiment"] == "baseline"
    assert metadata["phase"] == "resume"


@pytest.mark.asyncio
async def test_resume_false_rejects_existing_run_id(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    await Runner(
        problem,
        ScriptedPolicy([]),
        graph,
        RunLimits(max_steps=1),
        run_id="run_a",
    ).run()

    with pytest.raises(GraphError, match="already exists"):
        await Runner(
            problem,
            ScriptedPolicy([]),
            graph,
            RunLimits(max_steps=1),
            run_id="run_a",
            resume=False,
        ).run()


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
async def test_decision_recording_failure_skips_already_pending_events(
    tmp_path: Path,
) -> None:
    class BadAction:
        pass

    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)

    class PartiallySerializablePolicy:
        async def step(self, graph, status):
            return [
                Decision(
                    role="valid",
                    proposals=[
                        Proposal(
                            parent_id=start_id,
                            action=Combine("1", "3", "+"),
                        )
                    ],
                ),
                Decision(
                    role="invalid",
                    proposals=[Proposal(parent_id=start_id, action=BadAction())],
                ),
            ]

    with pytest.raises(SerializationError, match="cannot serialize"):
        await Runner(
            problem,
            PartiallySerializablePolicy(),
            graph,
            RunLimits(max_steps=1),
        ).run()

    events = graph.proposal_events()
    assert len(events) == 1
    assert events[0].outcome == "skipped_failure"
    assert "SerializationError" in (events[0].error or "")
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
            for parent, action in zip(parents, actions, strict=True)
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
async def test_resume_recomputes_a_changed_objective(tmp_path: Path) -> None:
    class IntegerProblem:
        initial_state = 0

        def state_key(self, state: int) -> str:
            return str(state)

        def apply(self, state: int, action: int) -> int:
            return state + action

    problem = IntegerProblem()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    first = await Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id="0", action=1)]]),
        graph,
        RunLimits(max_steps=2),
        objective=Objective(score=float, goal_reached=lambda state: state == 1),
        run_id="run_a",
    ).run()
    assert first.stop_reason == "objective"

    resumed = await Runner(
        problem,
        ScriptedPolicy([[Proposal(parent_id="1", action=1)]]),
        graph,
        RunLimits(max_steps=2),
        objective=Objective(score=float, goal_reached=lambda state: state == 2),
        run_id="run_a",
    ).run()

    assert resumed.stop_reason == "objective"
    assert resumed.best_state_id == "2"


@pytest.mark.asyncio
async def test_objective_scores_stay_out_of_state_metadata(
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
            lambda node, evaluations: -problem.distance(node.state),
            n_proposals=1,
        ),
        graph,
        RunLimits(max_steps=2),
        objective=Objective(score=lambda state: -problem.distance(state)),
        run_id="run_a",
    ).run()
    assert resumed.stop_reason == "max_steps"
    assert all("score" not in node.metadata for node in graph.states())
