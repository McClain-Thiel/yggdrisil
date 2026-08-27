from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from make24 import Combine, Make24, Pool

from yggdrisil.evaluation import EvaluationResult
from yggdrisil.graph import SQLiteStateGraph
from yggdrisil.viewer import GraphReader


def test_graph_reader_returns_raw_incremental_rows(tmp_path: Path) -> None:
    path = tmp_path / "g.sqlite"
    problem = Make24()
    graph = SQLiteStateGraph(path)
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    graph.save_run("run_a", step=0, status="running")
    graph.add_evaluation(
        start_id,
        evaluator_id="distance-v1",
        evaluator="distance",
        version="1",
        config_hash="config",
        result=EvaluationResult(metrics={"distance": 18.0}),
    )
    graph.add_decision(
        "decision-a",
        run_id="run_a",
        policy="test:Policy",
        role="explorer",
        model="test-model",
        selected_state_ids=[start_id],
        input_context="try addition",
        tool_calls=[{"tool": "add"}],
        output={"actions": ["add"]},
        metadata={"note": "candidate"},
        created_step=1,
    )
    graph.add_proposal_event(
        "event-a",
        decision_id="decision-a",
        run_id="run_a",
        parent_id=start_id,
        action=Combine("1", "3", "+"),
        metadata={"source": "policy"},
        created_step=1,
        proposal_index=0,
        sequence_index=0,
    )

    reader = GraphReader(path)
    first = reader.updates(state_after=0, edge_after=0)
    assert first["counts"] == {"states": 1, "edges": 0}
    assert first["states"][0]["state_id"] == start_id
    assert isinstance(first["states"][0]["state"], dict)
    assert first["run"]["run_id"] == "run_a"
    assert first["evaluations"][0]["metrics"]
    assert first["decisions"][0]["tool_calls"]
    assert first["proposal_events"][0]["outcome"] == "pending"
    assert first["proposal_events"][0]["metadata"]

    child = problem.apply(problem.initial_state, Combine("1", "3", "+"))
    child_id = problem.state_key(child)
    graph.add_transition(
        parent_id=start_id,
        child_id=child_id,
        child=child,
        action=Combine("1", "3", "+"),
        created_step=1,
    )
    edge = graph.edges()[0]
    graph.finish_proposal_event(
        "event-a",
        outcome="created",
        child_id=child_id,
        edge_id=edge.edge_id,
    )
    second = reader.updates(
        state_after=first["state_cursor"],
        edge_after=first["edge_cursor"],
    )
    assert [node["state_id"] for node in second["states"]] == [child_id]
    assert len(second["edges"]) == 1
    assert second["proposal_events"][0]["outcome"] == "created"


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
