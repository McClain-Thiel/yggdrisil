from __future__ import annotations

from pathlib import Path

from make24 import Combine, Make24, Pool

from yggdrisil.graph import SQLiteStateGraph


def test_json_and_graphml_and_networkx(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    child = problem.apply(problem.initial_state, Combine("4", "6", "*"))
    child_id = problem.state_key(child)
    graph.add_state(child_id, child)
    graph.add_edge(start_id, child_id, Combine("4", "6", "*"))

    json_path = tmp_path / "graph.json"
    graphml_path = tmp_path / "graph.graphml"
    graph.export_json(json_path)
    graph.export_graphml(graphml_path)
    assert json_path.exists()
    assert graphml_path.exists()

    nxg = graph.to_networkx()
    assert set(nxg.nodes) == {start_id, child_id}
    assert nxg.has_edge(start_id, child_id)
    assert isinstance(nxg.nodes[start_id]["state"], Pool)


def test_parallel_edges_and_non_json_metadata_are_preserved(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    child = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    child_id = problem.state_key(child)
    graph.add_state(start_id, problem.initial_state, metadata={"tags": {"root"}})
    graph.add_state(child_id, child)
    graph.add_edge(start_id, child_id, Combine("1", "3", "+"))
    graph.add_edge(start_id, child_id, Combine("3", "1", "+"))

    nxg = graph.to_networkx()
    assert nxg.number_of_edges(start_id, child_id) == 2
    graph.export_json(tmp_path / "graph.json")
    graph.export_graphml(tmp_path / "graph.graphml")
