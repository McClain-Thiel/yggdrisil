from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from make24 import Combine, Make24, Pool

from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.viewer import GraphReader


def test_graph_reader_returns_raw_incremental_rows(tmp_path: Path) -> None:
    path = tmp_path / "g.sqlite"
    problem = Make24()
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(
        start_id, problem.initial_state, metadata={"trace": [{"tool": "seed"}]}
    )
    graph.save_run("run_a", step=0, status="running")

    reader = GraphReader(path)
    first = reader.updates(state_after=0, edge_after=0)
    assert first["counts"] == {"states": 1, "edges": 0}
    assert first["states"][0]["state_id"] == start_id
    assert isinstance(first["states"][0]["state"], dict)
    assert first["run"]["run_id"] == "run_a"

    child = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    child_id = problem.state_key(child)
    graph.add_transition(
        parent_id=start_id,
        child_id=child_id,
        child=child,
        action=Combine("1", "3", "+"),
        edge_metadata={"trace": [{"tool": "add"}]},
        created_step=1,
    )
    second = reader.updates(
        state_after=first["state_cursor"],
        edge_after=first["edge_cursor"],
    )
    assert [node["state_id"] for node in second["states"]] == [child_id]
    assert len(second["edges"]) == 1


def test_viewer_assets_are_packaged() -> None:
    web = files("yggdrisil.web")
    assert web.joinpath("index.html").is_file()
    assert web.joinpath("viewer.css").is_file()
    assert web.joinpath("viewer.js").is_file()


def test_graph_reader_pages_initial_graph(tmp_path: Path) -> None:
    path = tmp_path / "g.sqlite"
    graph = SQLiteStateGraph(path)
    for step, value in enumerate(("1", "2", "3")):
        graph.add_state(value, Pool((value,)), created_step=step)

    reader = GraphReader(path, batch_size=2)
    first = reader.updates(state_after=0, edge_after=0)
    assert len(first["states"]) == 2
    assert first["pending"] is True
    second = reader.updates(
        state_after=first["state_cursor"],
        edge_after=first["edge_cursor"],
    )
    assert len(second["states"]) == 1
    assert second["pending"] is False
