from __future__ import annotations

from pathlib import Path

import pytest
from make24 import Combine, Make24

from yggdrisil.exceptions import CycleError, UnknownStateError
from yggdrisil.graph import SQLiteStateGraph


def _apply(problem: Make24, *actions: Combine):
    state = problem.initial_state
    for action in actions:
        state = problem.apply(state, action)
    return state


def test_convergent_paths_share_a_node(tmp_path: Path) -> None:
    problem = Make24((1, 2, 3, 4))
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    start = problem.initial_state
    start_id = problem.state_key(start)
    graph.add_state(start_id, start, created_step=0)

    # 1+2 then 3+4  vs  3+4 then 1+2 — same remaining multiset.
    via_12 = [
        _apply(problem, Combine("1", "2", "+")),
        _apply(problem, Combine("1", "2", "+"), Combine("3", "4", "+")),
    ]
    via_34 = [
        _apply(problem, Combine("3", "4", "+")),
        _apply(problem, Combine("3", "4", "+"), Combine("1", "2", "+")),
    ]
    assert problem.state_key(via_12[-1]) == problem.state_key(via_34[-1])

    ids_12 = [problem.state_key(s) for s in via_12]
    ids_34 = [problem.state_key(s) for s in via_34]
    for state, state_id in zip(via_12 + via_34[:-1], ids_12 + ids_34[:-1], strict=True):
        graph.add_state(state_id, state)
    graph.add_edge(start_id, ids_12[0], Combine("1", "2", "+"))
    graph.add_edge(ids_12[0], ids_12[1], Combine("3", "4", "+"))
    graph.add_edge(start_id, ids_34[0], Combine("3", "4", "+"))
    graph.add_edge(ids_34[0], ids_12[1], Combine("1", "2", "+"))

    merged = ids_12[1]
    assert len(graph) == 4
    assert graph.edge_count() == 4
    assert {n.state_id for n in graph.parents(merged)} == {ids_12[0], ids_34[0]}


def test_duplicate_edge_is_idempotent(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    child = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    child_id = problem.state_key(child)
    graph.add_state(child_id, child)
    e1 = graph.add_edge(start_id, child_id, Combine("1", "3", "+"), metadata={"n": 1})
    e2 = graph.add_edge(start_id, child_id, Combine("1", "3", "+"), metadata={"n": 2})
    assert e1.edge_id == e2.edge_id
    assert graph.edge_count() == 1


def test_cycle_is_rejected(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    a = problem.initial_state
    b = problem.apply(a, Combine("1", "3", "+"))
    graph.add_state("a", a)
    graph.add_state("b", b)
    graph.add_edge("a", "b", Combine("1", "3", "+"))
    with pytest.raises(CycleError):
        graph.add_edge("b", "a", Combine("4", "6", "+"))
    with pytest.raises(CycleError):
        graph.add_edge("a", "a", Combine("1", "3", "+"))


def test_unknown_state_raises(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    with pytest.raises(UnknownStateError):
        graph.get_state("missing")
    graph.add_state("a", Make24().initial_state)
    with pytest.raises(UnknownStateError):
        graph.add_edge("a", "missing", Combine("1", "3", "+"))


def test_readonly_view_hides_mutators(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    graph.add_state("a", Make24().initial_state)
    view = graph.readonly()
    assert not hasattr(view, "add_state")
    assert not hasattr(view, "add_edge")
    assert view.get_state("a").state_id == "a"
    assert len(view) == 1


def test_persistence_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "graph.sqlite"
    problem = Make24()
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state, created_step=0)
    child = problem.apply(problem.initial_state, Combine("4", "6", "*"))
    child_id = problem.state_key(child)
    graph.add_state(child_id, child, metadata={"note": "24 early"})
    graph.add_edge(start_id, child_id, Combine("4", "6", "*"))
    graph.save_run("run_1", step=1, status="completed")
    graph.close()

    graph2 = SQLiteStateGraph(path)
    assert len(graph2) == 2
    assert graph2.edge_count() == 1
    loaded = graph2.get_state(child_id)
    assert loaded.state == child
    assert loaded.metadata["note"] == "24 early"
    assert graph2.latest_run() is not None
    assert graph2.latest_run().step == 1
    graph2.close()


def test_frontier_are_leaves(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    child = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    child_id = problem.state_key(child)
    graph.add_state(child_id, child)
    graph.add_edge(start_id, child_id, Combine("1", "3", "+"))
    assert {n.state_id for n in graph.frontier()} == {child_id}


def test_state_queries_can_be_bounded_and_ordered(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "graph.sqlite")
    problem = Make24()
    states = [
        problem.initial_state,
        problem.apply(problem.initial_state, Combine("1", "3", "+")),
        problem.apply(problem.initial_state, Combine("4", "6", "+")),
    ]
    for step, state in enumerate(states):
        graph.add_state(problem.state_key(state), state, created_step=step)

    assert [node.created_step for node in graph.states(limit=2)] == [0, 1]
    assert [node.created_step for node in graph.states(limit=2, newest=True)] == [2, 1]
