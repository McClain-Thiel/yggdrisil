from __future__ import annotations

import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

import pytest
from make24 import Combine, Make24

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


def test_compact_updates_keep_details_on_demand(tmp_path: Path) -> None:
    import json

    path = tmp_path / "compact.sqlite"
    graph = SQLiteStateGraph(path)
    graph.add_state("root", {"genes": frozenset(f"g{i}" for i in range(5_000))})
    graph.save_run(
        "run",
        step=0,
        status="completed",
        config={"large": "x" * 20_000},
        metadata={"stop_reason": "max_states", "large": "x" * 20_000},
    )
    graph.add_evaluation(
        "root",
        evaluator_id="checks-v1",
        evaluator="checks",
        version="1",
        config_hash="config",
        result=EvaluationResult(
            metrics={"feasible": False, "score": 0.0},
            metadata={"evidence": "x" * 30_000},
        ),
    )
    graph.add_decision(
        "decision",
        run_id="run",
        policy="test",
        role="explorer",
        model="test",
        selected_state_ids=["root"],
        input_context="x" * 40_000,
        tool_calls=[{"tool": "inspect", "response": "x" * 30_000}],
        output={"rationale": "Recorded rationale. " * 100},
        metadata={},
        created_step=1,
    )
    graph.add_proposal_event(
        "proposal",
        decision_id="decision",
        run_id="run",
        parent_id="root",
        action={"genes": list(range(1000))},
        metadata={"evidence": "x" * 10_000},
        created_step=1,
        proposal_index=0,
        sequence_index=0,
    )
    reader = GraphReader(path)
    raw = reader.updates(state_after=0, edge_after=0)
    compact = reader.updates(state_after=0, edge_after=0, compact=True)
    assert len(json.dumps(compact)) < len(json.dumps(raw)) / 20
    assert compact["states"][0]["fields"] == {"genes": {"count": 5000}}
    assert "state" not in compact["states"][0]
    assert compact["evaluations"][0]["metrics"] == {"feasible": False, "score": 0.0}
    assert "metadata" not in compact["evaluations"][0]
    assert "input_context" not in compact["decisions"][0]
    assert compact["decisions"][0]["rationale"].endswith("…")
    assert compact["run"]["metadata"] == {"stop_reason": "max_states"}
    assert reader.detail("state", "root") == raw["states"][0]
    assert reader.detail("decision", "decision") == raw["decisions"][0]
    assert reader.detail("proposal", "proposal") == raw["proposal_events"][0]
    evaluation_id = raw["evaluations"][0]["evaluation_id"]
    assert reader.detail("evaluation", evaluation_id) == raw["evaluations"][0]
    with pytest.raises(ValueError, match="unknown detail kind"):
        reader.detail("states; DROP TABLE states", "root")
    with pytest.raises(KeyError, match="not found"):
        reader.detail("state", "missing")

    graph.finish_proposal_event("proposal", outcome="failed", error="Invalid action")
    next_payload = reader.updates(
        state_after=compact["state_cursor"],
        edge_after=compact["edge_cursor"],
        evaluation_after=compact["evaluation_cursor"],
        decision_after=compact["decision_cursor"],
        compact=True,
    )
    assert next_payload["states"] == []
    assert next_payload["proposal_events"][0]["outcome"] == "failed"
    assert next_payload["proposal_events"][0]["error"] == "Invalid action"


def test_viewer_search_matches_values_and_literal_wildcards(tmp_path: Path) -> None:
    graph = SQLiteStateGraph(tmp_path / "search.sqlite")
    graph.add_state("root", {"value": "needle"}, metadata={"note": "100%_literal"})
    graph.add_state("other", {"value": "another"}, metadata={"note": "100anything"})
    reader = GraphReader(tmp_path / "search.sqlite")
    for query in ["root", "needle", "100%_"]:
        assert [row["state_id"] for row in reader.search(query)["states"]] == ["root"]
    assert reader.search("' OR 1=1 --")["states"] == []
    assert reader.search("")["states"] == []
    assert reader.search("absent")["states"] == []
    assert reader.search("o", limit=1)["has_more"] is True
    with pytest.raises(ValueError, match="500"):
        reader.search("x" * 501)


def test_viewer_http_routes(tmp_path: Path) -> None:
    import json
    import threading
    from urllib.error import HTTPError
    from urllib.request import urlopen

    from yggdrisil.viewer import ViewerServer

    graph = SQLiteStateGraph(tmp_path / "http.sqlite")
    graph.add_state("root", {"value": 1})
    server = ViewerServer(("127.0.0.1", 0), GraphReader(tmp_path / "http.sqlite"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/updates?compact=1") as response:
            payload = json.load(response)
            assert payload["states"][0]["summary"] == "value: 1"
        with urlopen(base + "/api/detail?kind=state&id=root") as response:
            assert json.load(response)["state_id"] == "root"
        with urlopen(base + "/api/search?q=root") as response:
            assert len(json.load(response)["states"]) == 1
        with urlopen(base + "/") as response:
            assert b"Summary" in response.read()
            assert "default-src 'self'" in response.headers["Content-Security-Policy"]
        for url, status in [
            ("/api/detail?kind=state&id=missing", 404),
            ("/api/detail?kind=unknown&id=root", 400),
            ("/api/search?q=" + "x" * 501, 400),
        ]:
            with pytest.raises(HTTPError) as caught:
                urlopen(base + url)
            assert caught.value.code == status
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_viewer_navigation_loading_and_comparisons() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the viewer interaction regression")
    script = Path(str(files("yggdrisil.web").joinpath("viewer.js")))
    probe = r"""
const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
function fakeElement() {
  return {
    attributes: {}, checked: false, dataset: {}, hidden: false, textContent: "",
    value: "", children: [], classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getBoundingClientRect() { return { width: 1000, height: 600, left: 0, top: 0 }; },
  };
}
const elements = new Map();
const document = {
  createElement: fakeElement, createElementNS: fakeElement,
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, fakeElement());
    return elements.get(id);
  },
  querySelectorAll() { return []; },
};
let timers = [], requests = [], failDetail = false;
const payload = {
  counts: { states: 1, edges: 0 }, states: [], edges: [], evaluations: [], decisions: [],
  proposal_events: [], state_cursor: 1, edge_cursor: 0, evaluation_cursor: 0,
  decision_cursor: 0, pending: false, graph: "test.sqlite",
  run: { run_id: "run", status: "completed", step: 0, metadata: {} },
};
const context = {
  console, document, URLSearchParams,
  fetch: async (url) => {
    requests.push(url);
    if (url.startsWith("/api/detail")) {
      return { ok: !failDetail, json: async () => failDetail ? { error: "Missing detail" } : { state: {} } };
    }
    return { ok: true, json: async () => payload };
  },
  window: { addEventListener() {}, clearTimeout() {}, setTimeout(fn, delay) { timers.push(delay); } },
};
vm.createContext(context);
const source = fs.readFileSync(process.argv[1], "utf8");
vm.runInContext(source.replace(/\npoll\(\);\s*$/, "\n"), context);
const run = (code) => vm.runInContext(code, context);
const evaluate = (code) => JSON.parse(JSON.stringify(run(code)));
(async () => {
  run(`
    for (let step = 0; step <= 104; step++) {
      const id = "node-" + step;
      state.nodes.set(id, { cursor: step + 1, created_step: step, state_id: id,
        summary: "value: " + step, fields: { value: step } });
      if (step) state.edges.set("edge-" + step, { edge_id: "edge-" + step,
        parent_id: "node-" + (step - 1), child_id: id, summary: "add: 1" });
    }
    state.selected = "node-104"; state.selectedKind = "node";
    rebuildIndexes();
  `);
  assert.deepEqual(evaluate("[...visibleNodeIds()].sort()"), ["node-102", "node-103", "node-104"]);
  run("fitGraph()");
  assert(run("state.zoom") > 1, "neighborhood must start at a readable scale");
  run('state.view = "overview"; fitGraph()');
  assert(run("state.zoom") < 0.1, "overview should include a long graph");
  run('el["frontier-only"].checked = true; fitGraph()');
  assert.equal(run("visibleNodeIds().size"), 1);
  assert(run("state.zoom") > 1, "Fit must use only filtered states");
  run('select("node", "node-103")');
  assert.equal(run('el["frontier-only"].checked'), false, "parent navigation must reveal the selected state");
  run('select("node", "node-104")');
  run('el["frontier-only"].checked = false; state.searchMatches = new Set(["node-50"]); fitGraph()');
  assert.equal(run("layoutNodes().size"), 1);
  assert(run("state.zoom") > 1, "search match must be readable");
  run('state.searchMatches = null; state.view = "lineage"');
  assert.equal(run("visibleNodeIds().size"), 105);
  assert.equal(run("selectedLineage().size"), 104);
  run('state.view = "focus"; state.tab = "decisions"; select("edge", "edge-104")');
  assert.equal(run("state.tab"), "summary", "edge navigation must show the transition");
  assert.equal(run("parentFor(state.nodes.get('node-104'), state.edges.get('edge-104'))"), "node-103");
  run('state.edges.set("merge", { edge_id: "merge", parent_id: "node-102", child_id: "node-104" }); rebuildIndexes()');
  assert.equal(run('parentFor(state.nodes.get("node-104"), null)'), null,
    "a merged state must not choose an arbitrary parent for comparisons");
  run('state.edges.delete("merge"); rebuildIndexes()');
  assert.equal(run("numericDelta(2343, 2318)"), "+25");
  assert.equal(run("numericDelta(false, 1)"), "", "booleans are not numeric deltas");
  assert.equal(run("numericDelta(1, undefined)"), "", "missing results are not zero");
  assert.equal(run("formatMetric(false)"), "No");
  assert.equal(run("formatMetric(0)"), "0");

  run('zoomAround(1.18, 500, 300)');
  const zoom = run("state.zoom");
  await run("poll()");
  assert.equal(run("state.zoom"), zoom, "poll must preserve manual zoom");
  assert.equal(timers.length, 0, "a completed run must stop polling after loading");
  payload.pending = true;
  await run("poll()");
  assert.equal(timers.pop(), 10, "completed runs must still drain every page");
  payload.pending = false; payload.run.status = "running";
  await run("poll()");
  assert.equal(timers.pop(), 1000, "live runs must keep polling");
  assert.equal(requests.filter((url) => url.startsWith("/api/detail")).length, 0,
    "selection and summary rendering must not load raw details");
  await run('loadDetail("state", "node-104")');
  await run('loadDetail("state", "node-104")');
  assert.equal(requests.filter((url) => url.startsWith("/api/detail")).length, 1,
    "reopening details should use the cache");
  failDetail = true;
  await assert.rejects(run('loadDetail("state", "missing")'), /Missing detail/);
  assert.equal(run('state.details.has("state:missing")'), false, "errors must remain retryable");
  const formatted = run('formatTraceInput(\'CANDIDATE_PREVIEW: {"candidates":[{"gene_id":"g1"}]}\')');
  assert(formatted.includes('CANDIDATE_PREVIEW:\n{\n  "candidates": ['));
  assert.equal(run('formatTraceValue("{not JSON}")'), "{not JSON}", "retain original model prose");
})().catch((error) => { console.error(error); process.exitCode = 1; });
"""
    completed = subprocess.run(
        [node, "-e", probe, str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
