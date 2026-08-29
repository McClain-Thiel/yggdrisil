from __future__ import annotations

import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
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


def test_viewer_preserves_manual_zoom_and_fits_wide_graph() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the viewer interaction regression")

    script = Path(str(files("yggdrisil.web").joinpath("viewer.js")))
    probe = r"""
const fs = require("node:fs");
const vm = require("node:vm");

function fakeElement() {
  return {
    attributes: {},
    checked: false,
    classList: { add() {}, remove() {}, toggle() {} },
    dataset: {},
    hidden: false,
    textContent: "",
    title: "",
    value: "",
    addEventListener() {},
    append() {},
    closest() { return null; },
    getBoundingClientRect() {
      return { width: 1000, height: 600, left: 0, top: 0 };
    },
    replaceChildren() {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
}

const elements = new Map();
const document = {
  createElement: fakeElement,
  createElementNS: fakeElement,
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, fakeElement());
    return elements.get(id);
  },
  querySelectorAll() { return []; },
};
const payload = {
  counts: { states: 1, edges: 0 },
  decisions: [],
  decision_cursor: 0,
  edges: [],
  edge_cursor: 0,
  evaluations: [{
    evaluation_id: "evaluation-1",
    evaluator: "test",
    evaluator_id: "test-v1",
    metadata: {},
    metrics: {},
    state_id: "root",
    version: "1",
  }],
  evaluation_cursor: 1,
  graph: "test.sqlite",
  pending: false,
  proposal_events: [],
  run: null,
  states: [],
  state_cursor: 1,
};
const context = {
  console,
  document,
  fetch: async () => ({ ok: true, json: async () => payload }),
  URLSearchParams,
  window: { addEventListener() {}, setTimeout() {} },
};
vm.createContext(context);
const source = fs.readFileSync(process.argv[1], "utf8");
vm.runInContext(source.replace(/\npoll\(\);\s*$/, "\n"), context);

(async () => {
  vm.runInContext(`
    state.nodes.set("root", {
      created_step: 0,
      metadata: {},
      state: { value: 1 },
      state_id: "root",
    });
    fitGraph();
    zoomAround(1.18, 500, 300);
  `, context);
  const manualZoom = vm.runInContext("state.zoom", context);
  await vm.runInContext("poll()", context);
  const polledZoom = vm.runInContext("state.zoom", context);
  if (polledZoom !== manualZoom) {
    throw new Error(`poll reset manual zoom ${manualZoom} -> ${polledZoom}`);
  }
  vm.runInContext(`
    state.nodes.clear();
    for (let step = 0; step <= 104; step += 1) {
      state.nodes.set("node-" + step, {
        created_step: step,
        metadata: {},
        state: { value: step },
        state_id: "node-" + step,
      });
    }
    fitGraph();
  `, context);
  const wideGraphZoom = vm.runInContext("state.zoom", context);
  if (wideGraphZoom >= 0.2) {
    throw new Error(`wide graph fit remained cropped at zoom ${wideGraphZoom}`);
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    completed = subprocess.run(
        [node, "-e", probe, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


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
