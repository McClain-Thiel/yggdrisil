"use strict";

const NS = "http://www.w3.org/2000/svg";
const MIN_ZOOM = 0.01;
const MAX_ZOOM = 3.5;
const NODE_WIDTH = 184;
const NODE_HEIGHT = 70;
const state = {
  nodes: new Map(), edges: new Map(), evaluations: new Map(), decisions: new Map(),
  proposalEvents: new Map(), parents: new Map(), children: new Map(),
  evaluationsByNode: new Map(), decisionsByNode: new Map(), decisionsByEdge: new Map(),
  details: new Map(), stateCursor: 0, edgeCursor: 0, evaluationCursor: 0,
  decisionCursor: 0, run: null, selected: null, selectedKind: null,
  view: "focus", tab: "summary", searchMatches: null, searchVersion: 0,
  panX: 30, panY: 80, zoom: 1, drag: null, autoFit: true,
  polling: false, pollTimer: null, searchTimer: null, positions: new Map(),
  graphRevision: 0, renderedSignature: null,
};
const el = Object.fromEntries([
  "graph", "graph-shell", "viewport", "bands", "edges", "nodes", "empty-state",
  "empty-title", "empty-description", "run-id", "run-description", "run-error",
  "connection", "connection-label", "graph-path", "search", "search-results",
  "frontier-only", "selection-kind", "selection-title", "selection-subtitle",
  "evaluation-count", "decision-count", "inspector-body", "panel-summary",
  "panel-evaluations", "panel-decisions", "zoom-in", "zoom-out", "fit",
  "focus", "lineage", "overview", "view-title", "view-description", "refresh",
].map((id) => [id, document.getElementById(id)]));

document.querySelectorAll(".tab").forEach((button, index, tabs) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
  button.addEventListener("keydown", (event) => {
    const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!offset && !["Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
      : (index + offset + tabs.length) % tabs.length;
    tabs[next].focus();
    activateTab(tabs[next].dataset.tab);
  });
});
el.search.addEventListener("input", () => {
  window.clearTimeout(state.searchTimer);
  const version = ++state.searchVersion;
  if (!el.search.value.trim()) {
    state.searchMatches = null;
    el["search-results"].hidden = true;
    renderGraph();
    fitGraph();
    return;
  }
  el["search-results"].hidden = false;
  el["search-results"].replaceChildren(element("p", "Searching…", "search-status"));
  state.searchTimer = window.setTimeout(() => searchStates(version), 180);
});
el.search.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    clearSearch();
    renderGraph();
    fitGraph();
  }
});
el["frontier-only"].addEventListener("change", () => { renderGraph(); fitGraph(); });
for (const view of ["focus", "lineage", "overview"]) {
  el[view].addEventListener("click", () => {
    state.view = view;
    clearSearch();
    renderGraph();
    fitGraph();
  });
}
el["zoom-in"].addEventListener("click", () => zoomAt(1.18));
el["zoom-out"].addEventListener("click", () => zoomAt(1 / 1.18));
el.fit.addEventListener("click", fitGraph);
el.refresh.addEventListener("click", () => {
  window.clearTimeout(state.pollTimer);
  poll();
});
el.graph.addEventListener("wheel", (event) => {
  event.preventDefault();
  const bounds = el.graph.getBoundingClientRect();
  zoomAround(event.deltaY < 0 ? 1.12 : 1 / 1.12, event.clientX - bounds.left, event.clientY - bounds.top);
}, { passive: false });
el.graph.addEventListener("pointerdown", (event) => {
  if (event.target.closest(".node, .edge")) return;
  state.drag = { x: event.clientX, y: event.clientY, panX: state.panX, panY: state.panY };
  el.graph.setPointerCapture(event.pointerId);
  el.graph.classList.add("dragging");
});
el.graph.addEventListener("pointermove", (event) => {
  if (!state.drag) return;
  state.panX = state.drag.panX + event.clientX - state.drag.x;
  state.panY = state.drag.panY + event.clientY - state.drag.y;
  state.autoFit = false;
  applyTransform();
});
function endDrag(event) {
  if (state.drag && el.graph.hasPointerCapture(event.pointerId)) el.graph.releasePointerCapture(event.pointerId);
  state.drag = null;
  el.graph.classList.remove("dragging");
}
el.graph.addEventListener("pointerup", endDrag);
el.graph.addEventListener("pointercancel", endDrag);
window.addEventListener("resize", () => { if (state.autoFit) fitGraph(); });

async function requestJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || ("HTTP " + response.status));
  return payload;
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  el.refresh.disabled = true;
  let delay = 1000;
  try {
    const query = new URLSearchParams({
      compact: "1", state_after: String(state.stateCursor), edge_after: String(state.edgeCursor),
      evaluation_after: String(state.evaluationCursor), decision_after: String(state.decisionCursor),
    });
    const payload = await requestJson("/api/updates?" + query);
    let changed = false;
    for (const [key, map] of [
      ["states", state.nodes], ["edges", state.edges], ["evaluations", state.evaluations],
      ["decisions", state.decisions], ["proposal_events", state.proposalEvents],
    ]) {
      const idKey = { states: "state_id", edges: "edge_id", evaluations: "evaluation_id",
        decisions: "decision_id", proposal_events: "event_id" }[key];
      for (const row of payload[key]) {
        if (JSON.stringify(map.get(row[idKey])) !== JSON.stringify(row)) {
          changed = true;
          if (key === "proposal_events") state.details.delete("proposal:" + row.event_id);
        }
        map.set(row[idKey], row);
      }
    }
    state.stateCursor = payload.state_cursor;
    state.edgeCursor = payload.edge_cursor;
    state.evaluationCursor = payload.evaluation_cursor;
    state.decisionCursor = payload.decision_cursor;
    state.run = payload.run;
    el["run-id"].textContent = payload.run?.run_id || "Saved search";
    el["run-id"].title = payload.run?.run_id || "";
    const stop = payload.run?.metadata?.stop_reason;
    el["run-description"].textContent = payload.counts.states + " states · " + payload.counts.edges
      + " transitions · step " + (payload.run?.step ?? "—") + (stop ? " · stopped: " + humanize(stop) : "");
    el["graph-path"].textContent = payload.graph;
    el["graph-path"].title = payload.graph;
    el["run-error"].hidden = true;
    if (changed) { rebuildIndexes(); state.graphRevision++; }
    if (!state.selected && state.nodes.size && !payload.pending) {
      const best = state.nodes.get(currentBestId());
      const latest = [...state.nodes.values()].sort((a, b) => b.cursor - a.cursor)[0];
      state.selected = (best || latest).state_id;
      state.selectedKind = "node";
      changed = true;
    }
    if (changed || !state.nodes.size) {
      renderGraph();
      renderInspector();
      if (state.autoFit && state.nodes.size) fitGraph();
    }
    const terminal = ["completed", "failed", "cancelled", "stopped"].includes(payload.run?.status);
    setConnection("live", payload.pending ? "loading" : (payload.run?.status || "watching"));
    if (terminal && !payload.pending) delay = null;
    else if (payload.pending) delay = 10;
  } catch (error) {
    setConnection("error", "read failed");
    el["run-error"].textContent = "Could not read the run: " + error.message + ". Retrying; you can also press Refresh.";
    el["run-error"].hidden = false;
    el["empty-title"].textContent = "The run could not be loaded.";
    el["empty-description"].textContent = "See the error above. Press Refresh to retry.";
    delay = 3000;
  } finally {
    state.polling = false;
    el.refresh.disabled = false;
    if (delay !== null) state.pollTimer = window.setTimeout(poll, delay);
  }
}

function addIndex(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}
function rebuildIndexes() {
  for (const map of [state.parents, state.children, state.evaluationsByNode,
    state.decisionsByNode, state.decisionsByEdge]) map.clear();
  state.edges.forEach((edge) => {
    addIndex(state.parents, edge.child_id, edge);
    addIndex(state.children, edge.parent_id, edge);
  });
  state.evaluations.forEach((evaluation) => addIndex(state.evaluationsByNode, evaluation.state_id, evaluation));
  const nodeDecisions = new Map();
  const edgeDecisions = new Map();
  function link(map, key, id) {
    if (!key) return;
    if (!map.has(key)) map.set(key, new Set());
    map.get(key).add(id);
  }
  state.decisions.forEach((decision) => {
    for (const id of decision.selected_state_ids) link(nodeDecisions, id, decision.decision_id);
  });
  state.proposalEvents.forEach((event) => {
    link(nodeDecisions, event.parent_id, event.decision_id);
    link(nodeDecisions, event.child_id, event.decision_id);
    link(edgeDecisions, event.edge_id, event.decision_id);
  });
  for (const [source, target] of [[nodeDecisions, state.decisionsByNode], [edgeDecisions, state.decisionsByEdge]]) {
    source.forEach((ids, key) => target.set(key, [...ids].map((id) => state.decisions.get(id))
      .filter(Boolean).sort((a, b) => a.created_step - b.created_step)));
  }
}

function selectedNodeId() {
  return state.selectedKind === "edge" ? state.edges.get(state.selected)?.child_id : state.selected;
}
function currentBestId() { return state.run?.metadata?.best_state_id || null; }
function visibleNodeIds() {
  const selected = selectedNodeId();
  let visible = new Set(state.nodes.keys());
  if (state.searchMatches !== null) {
    visible = new Set(state.searchMatches);
  } else if (selected && state.view !== "overview") {
    visible = new Set([selected]);
    let frontier = [selected];
    const rounds = state.view === "lineage" ? state.nodes.size : 2;
    for (let step = 0; step < rounds && frontier.length; step++) {
      const next = [];
      for (const id of frontier) {
        const edges = state.view === "lineage" ? (state.parents.get(id) || [])
          : [...(state.parents.get(id) || []), ...(state.children.get(id) || [])];
        for (const edge of edges) {
          const neighbor = edge.child_id === id ? edge.parent_id : edge.child_id;
          if (!visible.has(neighbor)) { visible.add(neighbor); next.push(neighbor); }
        }
      }
      frontier = next;
    }
  }
  if (el["frontier-only"].checked) {
    visible = new Set([...visible].filter((id) => !state.children.get(id)?.length));
  }
  return visible;
}

function layoutNodes(visible = visibleNodeIds()) {
  const byStep = new Map();
  for (const id of visible) {
    const node = state.nodes.get(id);
    if (!node) continue;
    addIndex(byStep, node.created_step ?? 0, node);
  }
  const positions = new Map();
  [...byStep.keys()].sort((a, b) => a - b).forEach((step, column) => {
    const nodes = byStep.get(step).sort((a, b) => a.cursor - b.cursor);
    nodes.forEach((node, row) => positions.set(node.state_id, {
      x: column * 238, y: row * 102, column, step,
    }));
  });
  return positions;
}
function selectedLineage() {
  const result = new Set();
  const stack = [selectedNodeId()];
  const visited = new Set(stack);
  while (stack.length) {
    for (const edge of state.parents.get(stack.pop()) || []) {
      result.add(edge.edge_id);
      if (!visited.has(edge.parent_id)) { visited.add(edge.parent_id); stack.push(edge.parent_id); }
    }
  }
  return result;
}
function renderGraph() {
  const visible = visibleNodeIds();
  const positions = layoutNodes(visible);
  state.positions = positions;
  const lineage = selectedLineage();
  const signature = state.graphRevision + ":" + [...visible].join("|");
  if (signature !== state.renderedSignature) {
    state.renderedSignature = signature;
    const bands = new Map();
    positions.forEach((position) => bands.set(position.column, position.step));
    el.bands.replaceChildren(...[...bands].map(([column, step]) => {
      const text = svg("text", { x: column * 238, y: -18, class: "step-label" });
      text.textContent = "Step " + step;
      return text;
    }));
    const edges = [];
    state.edges.forEach((edge) => {
      const from = positions.get(edge.parent_id);
      const to = positions.get(edge.child_id);
      if (!from || !to) return;
      const group = svg("g", { class: "edge", role: "button", tabindex: "0",
        "aria-label": "Inspect transition " + edge.edge_id });
      group.dataset.edgeId = edge.edge_id;
      group.classList.toggle("selected", state.selectedKind === "edge" && state.selected === edge.edge_id);
      group.classList.toggle("lineage", lineage.has(edge.edge_id));
      const x1 = from.x + NODE_WIDTH, y1 = from.y + NODE_HEIGHT / 2;
      const x2 = to.x - 5, y2 = to.y + NODE_HEIGHT / 2;
      const bend = Math.max(32, (x2 - x1) * 0.45);
      const path = "M" + x1 + "," + y1 + " C" + (x1 + bend) + "," + y1 + " "
        + (x2 - bend) + "," + y2 + " " + x2 + "," + y2;
      group.append(svg("path", { class: "edge-path", d: path }), svg("path", { class: "edge-hit", d: path }));
      bindSelect(group, "edge", edge.edge_id);
      edges.push(group);
    });
    el.edges.replaceChildren(...edges);
    el.nodes.replaceChildren(...[...positions].map(([id, position]) => {
      const node = state.nodes.get(id);
      const group = svg("g", { class: "node", role: "button", tabindex: "0",
        transform: "translate(" + position.x + " " + position.y + ")",
        "aria-label": "Inspect state " + id + ": " + node.summary });
      group.dataset.stateId = id;
      group.classList.toggle("frontier", !state.children.get(id)?.length);
      group.classList.toggle("selected", selectedNodeId() === id);
      group.classList.toggle("best", currentBestId() === id);
      const title = svg("title", {});
      title.textContent = node.summary + "\n" + id;
      group.append(title, svg("rect", { width: NODE_WIDTH, height: NODE_HEIGHT, rx: 6 }));
      group.append(svgText(13, 26, trimText(humanize(node.summary).replace(/ items/g, ""), 24), "node-summary"));
      group.append(svgText(13, 48, shortId(id) + (currentBestId() === id ? " · best" : ""), "node-id"));
      bindSelect(group, "node", id);
      return group;
    }));
  } else {
    for (const group of el.nodes.children) {
      group.classList.toggle("selected", group.dataset.stateId === selectedNodeId());
      group.classList.toggle("best", group.dataset.stateId === currentBestId());
    }
    for (const group of el.edges.children) {
      group.classList.toggle("selected", state.selectedKind === "edge" && group.dataset.edgeId === state.selected);
      group.classList.toggle("lineage", lineage.has(group.dataset.edgeId));
    }
  }
  for (const view of ["focus", "lineage", "overview"]) {
    el[view].setAttribute("aria-pressed", String(state.view === view && state.searchMatches === null));
  }
  el["view-title"].textContent = state.searchMatches !== null ? "Search matches"
    : { focus: "Around this state", lineage: "Path to this state", overview: "Entire search" }[state.view];
  el["view-description"].textContent = visible.size + " of " + state.nodes.size + " states"
    + (state.view === "focus" && state.searchMatches === null ? " · up to two transitions away" : "");
  el["empty-state"].classList.toggle("hidden", positions.size > 0);
  el["empty-title"].textContent = state.nodes.size ? "No states in this view." : "Waiting for the first state.";
  el["empty-description"].textContent = state.nodes.size
    ? "Clear the search or turn off Frontier only to see more states." : "The view updates while the search runs.";
  applyTransform();
}
function bindSelect(target, kind, id) {
  target.addEventListener("click", (event) => { event.stopPropagation(); select(kind, id); });
  target.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault(); event.stopPropagation(); select(kind, id);
      const container = kind === "edge" ? el.edges : el.nodes;
      const key = kind === "edge" ? "edgeId" : "stateId";
      [...container.children].find((item) => item.dataset[key] === id)?.focus();
    }
  });
}
function select(kind, id) {
  state.selectedKind = kind;
  state.selected = id;
  const nodeId = selectedNodeId();
  if (el["frontier-only"].checked && state.children.get(nodeId)?.length) el["frontier-only"].checked = false;
  if (state.searchMatches !== null && !state.searchMatches.has(nodeId)) {
    clearSearch();
    state.view = "focus";
  }
  state.tab = "summary";
  el["inspector-body"].scrollTop = 0;
  renderGraph();
  renderInspector();
  if (state.view === "focus" && state.searchMatches === null) fitGraph();
}
function fitGraph() {
  if (!state.nodes.size) return;
  const positions = layoutNodes();
  if (!positions.size) return;
  let maxX = NODE_WIDTH, maxY = NODE_HEIGHT;
  positions.forEach((p) => { maxX = Math.max(maxX, p.x + NODE_WIDTH); maxY = Math.max(maxY, p.y + NODE_HEIGHT); });
  const bounds = el["graph-shell"].getBoundingClientRect();
  state.zoom = clamp(Math.min((bounds.width - 64) / maxX, (bounds.height - 160) / maxY), MIN_ZOOM, 1.25);
  state.panX = (bounds.width - maxX * state.zoom) / 2;
  state.panY = Math.max(100, (bounds.height - maxY * state.zoom) / 2);
  state.autoFit = true;
  applyTransform();
}
function zoomAt(factor) {
  const bounds = el.graph.getBoundingClientRect();
  zoomAround(factor, bounds.width / 2, bounds.height / 2);
}
function zoomAround(factor, x, y) {
  const next = clamp(state.zoom * factor, MIN_ZOOM, MAX_ZOOM);
  if (next === state.zoom) return;
  const ratio = next / state.zoom;
  state.panX = x - (x - state.panX) * ratio;
  state.panY = y - (y - state.panY) * ratio;
  state.zoom = next;
  state.autoFit = false;
  applyTransform();
}
function applyTransform() {
  el.viewport.setAttribute("transform", "translate(" + state.panX + " " + state.panY + ") scale(" + state.zoom + ")");
}
function clearSearch() {
  ++state.searchVersion;
  window.clearTimeout(state.searchTimer);
  el.search.value = "";
  el["search-results"].hidden = true;
  state.searchMatches = null;
}
async function searchStates(version) {
  const query = el.search.value.trim();
  try {
    const result = await requestJson("/api/search?" + new URLSearchParams({ q: query }));
    if (version !== state.searchVersion) return;
    state.searchMatches = new Set(result.states.map((node) => node.state_id));
    const rows = result.states.map((node) => {
      state.nodes.set(node.state_id, node);
      const button = element("button", "", "search-result");
      button.type = "button";
      button.append(element("strong", humanize(node.summary)), element("span", "Step " + node.created_step + " · " + shortId(node.state_id)));
      button.addEventListener("click", () => {
        clearSearch();
        el["frontier-only"].checked = false;
        state.view = "focus";
        select("node", node.state_id);
      });
      return button;
    });
    const message = result.states.length ? result.states.length + (result.has_more ? "+ matches. Refine your search." : result.states.length === 1 ? " match" : " matches")
      : "No matching states. Try an ID, value, or note.";
    el["search-results"].replaceChildren(element("p", message, "search-status"), ...rows);
    renderGraph();
    fitGraph();
  } catch (error) {
    if (version !== state.searchVersion) return;
    el["search-results"].replaceChildren(element("p", "Search failed: " + error.message, "inline-error"));
  }
}

function activateTab(name) {
  state.tab = name;
  el["inspector-body"].scrollTop = 0;
  renderInspector();
}
function renderInspector() {
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.tab === state.tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  for (const name of ["summary", "evaluations", "decisions"]) {
    el["panel-" + name].hidden = name !== state.tab;
    el["panel-" + name].classList.toggle("active", name === state.tab);
  }
  if (!state.selected) return;
  const node = state.nodes.get(selectedNodeId());
  const edge = state.selectedKind === "edge" ? state.edges.get(state.selected) : null;
  if (!node) return;
  const evaluations = state.evaluationsByNode.get(node.state_id) || [];
  const decisions = (edge ? state.decisionsByEdge.get(edge.edge_id) : state.decisionsByNode.get(node.state_id)) || [];
  el["selection-kind"].textContent = edge ? "Transition · step " + edge.created_step
    : "Step " + node.created_step + (node.state_id === currentBestId() ? " · recorded best" : "");
  el["selection-title"].textContent = humanize(edge ? edge.summary : node.summary).replace(/: (\d[\d,]*) items/, ": $1");
  el["selection-subtitle"].textContent = edge ? shortId(edge.parent_id) + " → " + shortId(edge.child_id)
    : (!state.children.get(node.state_id)?.length ? "Frontier state" : "Expanded state") + " · " + shortId(node.state_id);
  el["selection-subtitle"].title = edge ? edge.edge_id : node.state_id;
  el["evaluation-count"].textContent = String(evaluations.length);
  el["decision-count"].textContent = String(decisions.length);
  const panel = el["panel-" + state.tab];
  panel.replaceChildren();
  if (state.tab === "summary") renderSummary(panel, node, edge, evaluations);
  if (state.tab === "evaluations") renderEvaluations(panel, node, evaluations, edge);
  if (state.tab === "decisions") renderDecisions(panel, decisions);
}
function parentFor(node, edge) {
  if (edge) return edge.parent_id;
  const parents = state.parents.get(node.state_id) || [];
  return parents.length === 1 ? parents[0].parent_id : null;
}
function numericDelta(value, parent) {
  if (typeof value !== "number" || typeof parent !== "number" || !Number.isFinite(value) || !Number.isFinite(parent)) return "";
  const delta = value - parent;
  return delta === 0 ? "unchanged" : ((delta > 0 ? "+" : "−") + formatNumber(Math.abs(delta)));
}
function metricsForParent(parentId, evaluation) {
  return (state.evaluationsByNode.get(parentId) || []).find((row) => row.evaluator_id === evaluation.evaluator_id)?.metrics || {};
}
function metricRows(evaluations, parentId) {
  return evaluations.flatMap((evaluation) => Object.entries(evaluation.metrics).map(([key, value]) => ({
    key, value, evaluation, parent: metricsForParent(parentId, evaluation)[key],
  })));
}
function renderSummary(panel, node, edge, evaluations) {
  const parentId = parentFor(node, edge);
  const rows = metricRows(evaluations, parentId);
  const booleans = rows.filter((row) => typeof row.value === "boolean");
  if (booleans.length) {
    const checks = element("div", "", "checks");
    for (const row of booleans) {
      const check = element("div", "", "check " + (row.value ? "positive" : "negative"));
      const previous = typeof row.parent === "boolean" && row.parent !== row.value
        ? " · was " + formatMetric(row.parent) : "";
      check.append(element("span", humanize(row.evaluation.evaluator) + ": " + humanize(row.key)),
        element("strong", formatMetric(row.value) + previous));
      checks.append(check);
    }
    panel.append(checks);
  }
  const scalars = rows.filter((row) => typeof row.value === "number" || typeof row.value === "string");
  const highlights = scalars.filter((row) => row.evaluation === evaluations[0]).slice(0, 4);
  if (highlights.length) {
    const grid = element("div", "", "metric-grid");
    for (const row of highlights) {
      const cell = element("div", "", "metric");
      cell.append(element("span", humanize(row.key), "metric-label"), element("strong", formatMetric(row.value)));
      const delta = numericDelta(row.value, row.parent);
      if (delta) cell.append(element("small", delta + " from parent"));
      grid.append(cell);
    }
    panel.append(grid);
  } else if (!evaluations.length) {
    panel.append(element("p", "No evaluation results recorded for this state.", "muted"));
    const fields = element("dl", "", "facts");
    for (const [name, value] of Object.entries(node.fields || {})) {
      const item = element("div");
      item.append(element("dt", humanize(name)), element("dd", formatMetric(value)));
      fields.append(item);
    }
    panel.append(fields);
  }
  if (scalars.length > highlights.length) {
    panel.append(actionButton("View all " + rows.length + " metrics", () => activateTab("evaluations"), "inline-button"));
  }
  if (!parentId && (state.parents.get(node.state_id) || []).length > 1) {
    panel.append(element("p", "Multiple parents. Select a transition to compare its results.", "muted"));
  }
  panel.append(element("h2", edge ? "This transition" : "How this state was reached"));
  const incoming = edge ? [edge] : (state.parents.get(node.state_id) || []);
  if (!incoming.length) panel.append(element("p", "Initial state. No incoming transition.", "muted"));
  for (const transition of incoming) {
    const card = element("article", "", "transition-card");
    card.append(element("strong", humanize(transition.summary)));
    if (transition.rationale) card.append(element("p", transition.rationale, "rationale"));
    const nav = element("div", "", "node-navigation");
    nav.append(actionButton("Parent · step " + (state.nodes.get(transition.parent_id)?.created_step ?? "—"),
      () => select("node", transition.parent_id)));
    if (edge) nav.append(actionButton("Child · step " + node.created_step, () => select("node", node.state_id)));
    else nav.append(actionButton("Inspect transition", () => select("edge", transition.edge_id)));
    card.append(nav);
    panel.append(card);
  }
  const outgoing = state.children.get(node.state_id) || [];
  if (!edge && outgoing.length) {
    panel.append(element("h2", "Next states"));
    for (const transition of outgoing) {
      const child = state.nodes.get(transition.child_id);
      if (child) panel.append(actionButton("Step " + child.created_step + " · " + humanize(child.summary),
        () => select("node", child.state_id), "child-link"));
    }
  }
  const kind = edge ? "edge" : "state";
  const selected = edge || node;
  panel.append(lazyDetails("State data & provenance", async () => {
    const detail = await loadDetail(kind, edge ? edge.edge_id : node.state_id);
    const container = element("div");
    container.append(jsonDisclosure(edge ? "Full action" : "Full state", edge ? detail.action : detail.state),
      jsonDisclosure("Metadata", detail.metadata), jsonDisclosure("Identifiers & timestamps", {
        id: edge ? edge.edge_id : node.state_id, created_step: selected.created_step,
        created_at: selected.created_at,
      }));
    return container;
  }));
}
function renderEvaluations(panel, node, evaluations, edge) {
  if (!evaluations.length) { panel.append(element("p", "No evaluations for this state.", "muted")); return; }
  const parentId = parentFor(node, edge);
  panel.append(element("p", parentId ? "Values for this state, with changes from its parent." : "Recorded values for this state.", "muted"));
  for (const evaluation of evaluations) {
    const card = element("article", "", "evaluation-card");
    const header = element("header");
    header.append(element("h2", humanize(evaluation.evaluator)), element("span", "v" + evaluation.version, "muted"));
    card.append(header);
    const table = element("dl", "", "metric-list");
    const parentMetrics = metricsForParent(parentId, evaluation);
    for (const [key, value] of Object.entries(evaluation.metrics)) {
      const row = element("div");
      const detail = element("dd", formatMetric(value));
      if (typeof value === "boolean") detail.className = value ? "positive-text" : "negative-text";
      const delta = numericDelta(value, parentMetrics[key]);
      if (delta) detail.append(element("small", delta));
      if (typeof value === "boolean" && typeof parentMetrics[key] === "boolean" && value !== parentMetrics[key]) {
        detail.append(element("small", "was " + formatMetric(parentMetrics[key])));
      }
      row.append(element("dt", humanize(key)), detail);
      table.append(row);
    }
    card.append(table, lazyDetails("Evidence & provenance", async () => {
      const detail = await loadDetail("evaluation", evaluation.evaluation_id);
      const container = element("div");
      container.append(jsonDisclosure("Evidence and metadata", detail.metadata), jsonDisclosure("Full evaluation record", detail));
      return container;
    }));
    panel.append(card);
  }
}
function renderDecisions(panel, decisions) {
  if (!decisions.length) { panel.append(element("p", "No decisions linked to this selection.", "muted")); return; }
  for (const decision of decisions) {
    const card = element("article", "", "decision-card");
    const header = element("header");
    header.append(element("h2", humanize(decision.role)), element("span", "Step " + decision.created_step, "muted"));
    card.append(header);
    const events = [...state.proposalEvents.values()].filter((event) => event.decision_id === decision.decision_id);
    if (!events.length) card.append(element("p", "No proposals recorded for this decision.", "muted"));
    for (const event of events) {
      const proposal = element("div", "", "proposal");
      const outcome = element("span", humanize(event.outcome), "outcome");
      if (event.error || ["failed", "error", "invalid"].includes(event.outcome)) outcome.classList.add("negative-text");
      proposal.append(element("strong", humanize(event.summary)), outcome);
      if (event.rationale) proposal.append(element("p", event.rationale, "rationale"));
      if (event.error) proposal.append(element("p", event.error, "inline-error"));
      if (event.edge_id && state.edges.has(event.edge_id)) {
        proposal.append(actionButton("Inspect transition", () => select("edge", event.edge_id), "inline-button"));
      }
      proposal.append(lazyDetails("Full proposal", async () => {
        const detail = await loadDetail("proposal", event.event_id);
        return element("pre", formatTraceValue(detail));
      }));
      card.append(proposal);
    }
    if (decision.rationale) card.append(element("p", decision.rationale, "rationale"));
    if (!decision.rationale && !events.some((event) => event.rationale)) {
      card.append(element("p", "No short rationale recorded. The full response is available below.", "muted"));
    }
    card.append(lazyDetails("Full trace · " + decision.trace_event_count + " events", async () => {
      const detail = await loadDetail("decision", decision.decision_id);
      const container = element("div");
      container.append(element("p", detail.model || "No model recorded", "muted"),
        jsonDisclosure("Model response", detail.output));
      const calls = displayValue(detail.tool_calls);
      calls.forEach((call, index) => {
        const name = call.tool || call.name || call.function?.name || humanize(call.role || "Trace event");
        container.append(jsonDisclosure((index + 1) + ". " + name, call));
      });
      container.append(jsonDisclosure("Model input", detail.input_context, formatTraceInput),
        jsonDisclosure("Context & metadata", { policy: detail.policy, selected_state_ids: detail.selected_state_ids,
          metadata: detail.metadata }));
      return container;
    }));
    panel.append(card);
  }
}
async function loadDetail(kind, id) {
  const key = kind + ":" + id;
  if (!state.details.has(key)) {
    const promise = requestJson("/api/detail?" + new URLSearchParams({ kind, id }));
    state.details.set(key, promise);
    try { await promise; }
    catch (error) { state.details.delete(key); throw error; }
  }
  return state.details.get(key);
}
function lazyDetails(label, load) {
  const details = element("details", "", "disclosure");
  const summary = element("summary", label);
  const body = element("div", "", "disclosure-body");
  details.append(summary, body);
  let loaded = false, loading = false;
  async function populate() {
    if (loaded || loading) return;
    loading = true;
    body.replaceChildren(element("p", "Loading details…", "muted"));
    try {
      const content = await load();
      body.replaceChildren(content);
      loaded = true;
    } catch (error) {
      body.replaceChildren(element("p", "Could not load details: " + error.message, "inline-error"),
        actionButton("Retry", populate));
    } finally { loading = false; }
  }
  details.addEventListener("toggle", () => { if (details.open) populate(); });
  return details;
}
function jsonDisclosure(label, value, formatter = formatTraceValue) {
  return lazyDetails(label, async () => element("pre", formatter(value)));
}
function actionButton(label, action, className = "") {
  const button = element("button", label, className);
  button.type = "button";
  button.addEventListener("click", action);
  return button;
}
function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
function svg(tag, attributes) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}
function svgText(x, y, text, className) {
  const node = svg("text", { x, y, class: className });
  node.textContent = text;
  return node;
}
function setConnection(kind, label) {
  el.connection.className = "connection " + kind;
  el["connection-label"].textContent = label;
}
function humanize(value) {
  const text = String(value).replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}
function trimText(value, length) { return value.length > length ? value.slice(0, length - 1) + "…" : value; }
function shortId(id) { return id ? trimText(id, 11) : "—"; }
function clamp(value, low, high) { return Math.min(high, Math.max(low, value)); }
function formatNumber(value) {
  return Number.isInteger(value) ? value.toLocaleString("en-US") : value.toLocaleString("en-US", { maximumSignificantDigits: 5 });
}
function formatMetric(value) {
  if (value == null) return "Not recorded";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "object") return value.count !== undefined ? value.count.toLocaleString("en-US") + " items" : JSON.stringify(value);
  return String(value);
}
function displayValue(value) {
  if (Array.isArray(value)) return value.map(displayValue);
  if (!value || typeof value !== "object") return value;
  const tag = value.__yggdrisil__;
  if (tag === "dict") {
    const entries = value.items.map(([key, item]) => [displayValue(key), displayValue(item)]);
    if (entries.every(([key]) => ["string", "number", "boolean"].includes(typeof key))) return Object.fromEntries(entries);
    return { entries };
  }
  if (tag === "tuple") return value.items.map(displayValue);
  if (["set", "frozenset"].includes(tag)) return { [tag]: value.items.map(displayValue) };
  if (tag === "object") return Object.fromEntries([["__type__", value.qualname],
    ...value.fields.map(([key, item]) => [key, displayValue(item)])]);
  if (tag === "datetime") return value.iso;
  if (tag === "float") return value.value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, displayValue(item)]));
}
function parseJsonText(value) {
  const text = value.trim();
  if (!((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]")))) return null;
  try { return JSON.parse(text); }
  catch (error) {
    // A model response may contain prose that resembles JSON. Preserve it verbatim.
    if (error instanceof SyntaxError) return null;
    throw error;
  }
}
function expandJsonStrings(value) {
  if (typeof value === "string") {
    const parsed = parseJsonText(value);
    return parsed === null ? value : expandJsonStrings(displayValue(parsed));
  }
  if (Array.isArray(value)) return value.map(expandJsonStrings);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, expandJsonStrings(item)]));
}
function formatTraceValue(value) {
  const expanded = expandJsonStrings(displayValue(value));
  return typeof expanded === "string" ? expanded : JSON.stringify(expanded, null, 2);
}
function formatTraceInput(value) {
  if (typeof value !== "string") return formatTraceValue(value);
  const whole = parseJsonText(value);
  if (whole !== null) return formatTraceValue(whole);
  return value.split("\n").map((line) => {
    const separator = line.indexOf(":");
    if (separator < 0) return line;
    const embedded = parseJsonText(line.slice(separator + 1));
    return embedded === null ? line : line.slice(0, separator + 1) + "\n" + formatTraceValue(embedded);
  }).join("\n");
}

poll();
