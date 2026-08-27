from __future__ import annotations

import json
from pathlib import Path

from make24 import Combine, Make24, Pool

from yggdrisil.evaluation import EvaluationResult
from yggdrisil.graph import SQLiteStateGraph


def test_json_and_graphml_and_networkx(tmp_path: Path) -> None:
    problem = Make24()
    graph = SQLiteStateGraph(tmp_path / "g.sqlite")
    start_id = problem.state_key(problem.initial_state)
    graph.add_state(start_id, problem.initial_state)
    child = problem.apply(problem.initial_state, Combine("4", "6", "*"))
    child_id = problem.state_key(child)
    graph.add_state(child_id, child)
    edge = graph.add_edge(start_id, child_id, Combine("4", "6", "*"))
    graph.save_run("run-a", step=1, status="completed")
    graph.add_evaluation(
        child_id,
        evaluator_id="distance-v1",
        evaluator="distance",
        version="1",
        config_hash="config",
        result=EvaluationResult(metrics={"distance": 0.0}),
    )
    graph.add_decision(
        "decision-a",
        run_id="run-a",
        policy="test:Policy",
        role="policy",
        model=None,
        selected_state_ids=[start_id],
        input_context=None,
        tool_calls=[],
        output=None,
        metadata={},
        created_step=1,
    )
    graph.add_proposal_event(
        "event-a",
        decision_id="decision-a",
        run_id="run-a",
        parent_id=start_id,
        action=Combine("4", "6", "*"),
        metadata={"rank": 1},
        created_step=1,
        proposal_index=0,
        sequence_index=0,
    )
    graph.finish_proposal_event(
        "event-a",
        outcome="created",
        child_id=child_id,
        edge_id=edge.edge_id,
    )

    json_path = tmp_path / "graph.json"
    graphml_path = tmp_path / "graph.graphml"
    graph.export_json(json_path)
    graph.export_graphml(graphml_path)
    assert json_path.exists()
    assert graphml_path.exists()
    payload = json.loads(json_path.read_text())
    assert len(payload["evaluations"]) == 1
    assert len(payload["decisions"]) == 1
    assert payload["proposal_events"][0]["metadata"]

    nxg = graph.to_networkx()
    assert set(nxg.nodes) == {start_id, child_id}
    assert nxg.has_edge(start_id, child_id)
    assert isinstance(nxg.nodes[start_id]["state"], Pool)
    assert len(nxg.graph["decisions"]) == 1


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
