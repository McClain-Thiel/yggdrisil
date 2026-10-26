import asyncio
from pathlib import Path

import pytest
from make24 import Combine, Make24

from tests.support import ScriptedPolicy
from yggdrisil import (
    MemoryConflictError,
    MemoryUpdate,
    RunLimits,
    Runner,
    SQLiteStateGraph,
)
from yggdrisil.agents import (
    ExplorationRequest,
    ExplorerResult,
    NavigationPlan,
    NavigatorExplorerPolicy,
)


def write_document(graph, decision_id, updates, *, namespace=None):
    return graph.add_decision(
        decision_id,
        run_id="run",
        policy="test",
        role="navigator",
        model=None,
        selected_state_ids=[],
        input_context=None,
        tool_calls=[],
        output=None,
        metadata={},
        created_step=1,
        memory_updates=updates,
        memory_namespace=namespace,
    )


def test_documents_are_revision_checked_atomic_and_idempotent(tmp_path: Path):
    path = tmp_path / "memory.sqlite"
    with SQLiteStateGraph(path) as graph:
        graph.save_run("run", step=0, status="running")
        updates = [MemoryUpdate("source", "original", metadata={"url": "source"})]
        write_document(graph, "first", updates)
        write_document(graph, "first", updates)
        assert graph.memory("run", "source").revision == 1
        with pytest.raises(MemoryConflictError, match="expected revision 0, found 1"):
            write_document(
                graph,
                "conflict",
                [
                    MemoryUpdate("new", "must roll back"),
                    MemoryUpdate("source", "stale overwrite"),
                ],
            )
        with pytest.raises(KeyError):
            graph.memory("run", "new")
        with pytest.raises(KeyError):
            graph.get_decision("conflict")
        write_document(graph, "second", [MemoryUpdate("source", "corrected", 1)])
        write_document(
            graph, "shared", [MemoryUpdate("source", "other")], namespace="explicit"
        )
    with SQLiteStateGraph(path) as reopened:
        assert reopened.memory("run", "source").content == "corrected"
        assert reopened.memory("run", "source").revision == 2
        assert reopened.memory("explicit", "source").content == "other"
        assert len(reopened.memories("run")) == 1
        assert reopened.readonly().memory("run", "source").decision_id == "second"
        assert (
            reopened._conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
            == 3
        )


@pytest.mark.parametrize("revision", [-1, True, 1.5])
def test_bad_revisions_fail_loudly(revision):
    with pytest.raises(ValueError):
        MemoryUpdate("key", "text", revision)


async def test_manager_and_workers_resume_with_separate_memory_and_outcomes(
    tmp_path: Path,
):
    problem = Make24()
    root_id = problem.state_key(problem.initial_state)
    path = tmp_path / "search.sqlite"
    observed = []

    class Manager:
        async def plan(self, context):
            observed.append(context)
            if not context.checkpoint:
                return NavigationPlan(
                    [ExplorationRequest(root_id, worker="research")],
                    checkpoint={"phase": "research"},
                    continue_on_empty=True,
                )
            assert context.checkpoint == {"phase": "research"}
            assert context.memory[0].content == "try addition"
            assert context.reports[0]["output"]["note"] == "found a useful source"
            return NavigationPlan(
                [
                    ExplorationRequest(
                        root_id, worker="transition", memory_keys=["evidence"]
                    )
                ],
                checkpoint={"phase": "apply"},
            )

    class Researcher:
        async def explore(self, context):
            assert context.checkpoint == {"phase": "research"}
            return ExplorerResult(
                [],
                note="found a useful source",
                memory_updates=[MemoryUpdate("evidence", "try addition")],
            )

    class TransitionWorker:
        async def explore(self, context):
            assert context.memory[0].content == "try addition"
            assert context.checkpoint == {"phase": "apply"}
            return ExplorerResult([Combine("1", "3", "+")], note="proposed addition")

    def policy():
        return NavigatorExplorerPolicy(
            Manager(),
            {"research": Researcher(), "transition": TransitionWorker()},
            memory_enabled=True,
        )

    with SQLiteStateGraph(path) as graph:
        await Runner(
            problem, policy(), graph, RunLimits(max_steps=1), run_id="run"
        ).run()
        assert len(graph) == 1
    with SQLiteStateGraph(path) as graph:
        await Runner(
            problem, policy(), graph, RunLimits(max_steps=2), run_id="run"
        ).run()
        assert len(graph) == 2
        decisions = graph.decisions("run")
        assert decisions[-2].output["checkpoint"] == {"phase": "apply"}
        context = policy()._navigator_context(
            graph.readonly(),
            Runner(
                problem, ScriptedPolicy([]), graph, RunLimits(), run_id="run"
            )._status(0),
        )
        assert context.reports[-1]["outcomes"][0]["outcome"] == "created"
        assert context.reports[-1]["outcomes"][0]["child_id"] is not None
    assert len(observed) == 2


async def test_parallel_workers_receive_isolated_checkpoints(tmp_path: Path):
    problem = Make24()
    root = problem.state_key(problem.initial_state)

    class Manager:
        async def plan(self, context):
            if not context.checkpoint:
                return NavigationPlan(
                    [],
                    checkpoint={"nested": {"value": 1}},
                    memory_updates=[MemoryUpdate("oversized", "x" * 1000)],
                    continue_on_empty=True,
                )
            return NavigationPlan(
                [ExplorationRequest(root), ExplorationRequest(root)],
                checkpoint={"nested": {"value": 1}},
            )

    class Worker:
        async def explore(self, context):
            assert context.checkpoint["nested"]["value"] == 1
            context.checkpoint["nested"]["value"] = 2
            assert context.memory == []
            assert context.memory_keys == ["oversized"]
            return ExplorerResult([])

    with SQLiteStateGraph(tmp_path / "parallel.sqlite") as graph:
        policy = NavigatorExplorerPolicy(
            Manager(),
            Worker(),
            memory_enabled=True,
            max_memory_chars=100,
        )
        await Runner(problem, policy, graph, RunLimits(max_steps=2), run_id="run").run()
        assert graph.decisions("run")[0].output["checkpoint"] == {
            "nested": {"value": 1}
        }


async def test_timeout_preserves_manager_checkpoint_and_failure_report(tmp_path: Path):
    problem = Make24()
    root = problem.state_key(problem.initial_state)

    class Manager:
        async def plan(self, context):
            return NavigationPlan(
                [ExplorationRequest(root)], checkpoint={"active": root}
            )

    class SlowWorker:
        async def explore(self, context):
            await asyncio.sleep(5)
            return ExplorerResult([])

    with SQLiteStateGraph(tmp_path / "timeout.sqlite") as graph:
        policy = NavigatorExplorerPolicy(Manager(), SlowWorker(), memory_enabled=True)
        result = await Runner(
            problem, policy, graph, RunLimits(max_wall_time_s=0.03), run_id="run"
        ).run()
        assert result.stop_reason == "max_wall_time_s"
        restored = NavigatorExplorerPolicy(Manager(), SlowWorker(), memory_enabled=True)
        context = restored._navigator_context(graph.readonly(), policy_status("run"))
        assert context.checkpoint == {"active": root}
        assert context.reports[-1]["error"]


def policy_status(run_id):
    from yggdrisil import RunStatus

    return RunStatus(0, 1, 0, 0, RunLimits(), run_id=run_id)


async def test_different_run_has_no_inherited_checkpoint_or_documents(tmp_path: Path):
    problem = Make24()

    class Manager:
        async def plan(self, context):
            assert context.checkpoint == {}
            assert context.memory == []
            return NavigationPlan([])

    with SQLiteStateGraph(tmp_path / "isolation.sqlite") as graph:
        graph.save_run("run", step=0, status="completed")
        write_document(graph, "previous", [MemoryUpdate("secret", "old run")])
        policy = NavigatorExplorerPolicy(Manager(), object(), memory_enabled=True)
        await Runner(
            problem, policy, graph, RunLimits(max_steps=1), run_id="new", resume=False
        ).run()
