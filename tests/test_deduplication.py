from __future__ import annotations

from pathlib import Path

from make24 import Combine, Make24

from yggdrisil.graph import SQLiteStateGraph


def test_repeated_proposal_does_not_duplicate_nodes(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    child = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    child_id = problem.state_key(child)
    graph.add_state(child_id, child)
    graph.add_edge(start_id, child_id, Combine("1", "3", "+"))
    graph.add_state(child_id, child)
    graph.add_edge(start_id, child_id, Combine("1", "3", "+"))
    assert len(graph) == 2
    assert graph.edge_count() == 1


def test_second_parent_adds_an_incoming_edge(tmp_path: Path) -> None:
    problem = Make24((1, 2, 3, 4))
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start = problem.initial_state
    start_id = problem.state_key(start)
    graph.add_state(start_id, start)

    via_12 = problem.apply(start, Combine("1", "2", "+"))
    via_34 = problem.apply(start, Combine("3", "4", "+"))
    merged = problem.apply(via_12, Combine("3", "4", "+"))
    assert problem.state_key(merged) == problem.state_key(
        problem.apply(via_34, Combine("1", "2", "+"))
    )

    id12 = problem.state_key(via_12)
    id34 = problem.state_key(via_34)
    merged_id = problem.state_key(merged)
    graph.add_state(id12, via_12)
    graph.add_state(id34, via_34)
    graph.add_state(merged_id, merged)
    graph.add_edge(start_id, id12, Combine("1", "2", "+"))
    graph.add_edge(id12, merged_id, Combine("3", "4", "+"))
    graph.add_edge(start_id, id34, Combine("3", "4", "+"))
    graph.add_edge(id34, merged_id, Combine("1", "2", "+"))
    assert len(graph.parents(merged_id)) == 2
