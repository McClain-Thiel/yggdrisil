"use strict";

const NS = "http://www.w3.org/2000/svg";
const state = {
  nodes: new Map(),
  edges: new Map(),
  stateCursor: 0,
  edgeCursor: 0,
  run: null,
  selected: null,
  selectedKind: null,
  panX: 30,
  panY: 38,
  zoom: 1,
  drag: null,
  fitted: false,
};

const el = Object.fromEntries(
  [
    "graph", "graph-shell", "viewport", "bands", "edges", "nodes",
    "empty-state", "run-id", "run-step", "state-count", "edge-count",
    "connection", "connection-label", "graph-path", "search", "frontier-only",
    "selection-kind", "selection-title", "selection-subtitle", "selection-facts",
    "state-value", "state-metadata", "trace-list", "link-list", "trace-count",
    "link-count", "zoom-in", "zoom-out", "fit",
  ].map((id) => [id, document.getElementById(id)])
);

document.querySelectorAll(".tab").forEach((button) => {
  button.addEventListener("click", () => activateTab(button.dataset.tab));
});
el.search.addEventListener("input", renderGraph);
el["frontier-only"].addEventListener("change", renderGraph);
el["zoom-in"].addEventListener("click", () => zoomAt(1.18));
el["zoom-out"].addEventListener("click", () => zoomAt(1 / 1.18));
el.fit.addEventListener("click", fitGraph);

el.graph.addEventListener("wheel", (event) => {
  event.preventDefault();
  const bounds = el.graph.getBoundingClientRect();
  const x = event.clientX - bounds.left;
  const y = event.clientY - bounds.top;
  zoomAround(event.deltaY < 0 ? 1.12 : 1 / 1.12, x, y);
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
  state.fitted = false;
  applyTransform();
});

el.graph.addEventListener("pointerup", (event) => {
  if (state.drag) el.graph.releasePointerCapture(event.pointerId);
  state.drag = null;
  el.graph.classList.remove("dragging");
});

window.addEventListener("resize", () => {
  if (state.nodes.size && state.fitted) fitGraph();
});

function activateTab(name) {
  document.querySelectorAll(".tab").forEach((button) => {
    const active = button.dataset.tab === name;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    const active = panel.id === `panel-${name}`;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
}

async function poll() {
  let delayMs = 1000;
  try {
    const query = new URLSearchParams({
      state_after: String(state.stateCursor),
      edge_after: String(state.edgeCursor),
    });
    const response = await fetch(`/api/updates?${query}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    payload.states.forEach((node) => {
      node.metadata = displayValue(node.metadata);
      state.nodes.set(node.state_id, node);
    });
    payload.edges.forEach((edge) => {
      edge.metadata = displayValue(edge.metadata);
      state.edges.set(edge.edge_id, edge);
    });
    if (payload.run) {
      payload.run.config = displayValue(payload.run.config);
      payload.run.metadata = displayValue(payload.run.metadata);
    }
    state.stateCursor = payload.state_cursor;
    state.edgeCursor = payload.edge_cursor;
    updateStatus(payload);
    if (payload.states.length || payload.edges.length || !state.fitted) {
      renderGraph();
      renderInspector();
      if (!state.fitted && state.nodes.size) fitGraph();
    }
    if (payload.pending) delayMs = 10;
    setConnection("live", payload.run?.status || "watching");
  } catch (error) {
    setConnection("error", "read failed");
    el["connection-label"].title = String(error);
  } finally {
    window.setTimeout(poll, delayMs);
  }
}

function updateStatus(payload) {
  state.run = payload.run;
  el["state-count"].textContent = String(payload.counts.states);
  el["edge-count"].textContent = String(payload.counts.edges);
  el["run-id"].textContent = payload.run?.run_id || "—";
  el["run-id"].title = payload.run?.run_id || "";
  el["run-step"].textContent = payload.run ? String(payload.run.step) : "—";
  el["graph-path"].textContent = payload.graph;
  el["graph-path"].title = payload.graph;
  el["empty-state"].classList.toggle("hidden", payload.counts.states > 0);
}

function setConnection(kind, label) {
  el.connection.className = `connection ${kind}`;
  el["connection-label"].textContent = label;
}

function graphData() {
  const outgoing = new Map([...state.nodes.keys()].map((id) => [id, 0]));
  state.edges.forEach((edge) => outgoing.set(edge.parent_id, (outgoing.get(edge.parent_id) || 0) + 1));
  const query = el.search.value.trim().toLowerCase();
  const frontierOnly = el["frontier-only"].checked;
  const visible = new Set();
  state.nodes.forEach((node, id) => {
    const haystack = [
      id,
      summarize(node.state, 10_000),
      safeJson(displayValue(node.state)),
      safeJson(displayValue(node.metadata)),
    ].join(" ").toLowerCase();
    if ((!query || haystack.includes(query)) && (!frontierOnly || outgoing.get(id) === 0)) visible.add(id);
  });
  return { outgoing, visible };
}

function renderGraph() {
  const { outgoing, visible } = graphData();
  const positions = layoutNodes();
  const lineage = selectedLineage();
  replaceChildren(el.bands, renderBands(positions));

  const edgeGroups = [];
  state.edges.forEach((edge) => {
    const from = positions.get(edge.parent_id);
    const to = positions.get(edge.child_id);
    if (!from || !to) return;
    const group = svg("g", {
      class: "edge",
      tabindex: "0",
      role: "button",
      "aria-label": `Inspect transition ${edge.edge_id}`,
    });
    const path = edgePath(from, to);
    const dimmed = !visible.has(edge.parent_id) || !visible.has(edge.child_id);
    group.classList.toggle("dimmed", dimmed);
    group.classList.toggle("selected", state.selectedKind === "edge" && state.selected === edge.edge_id);
    group.classList.toggle("lineage", lineage.has(edge.edge_id));
    group.dataset.edgeId = edge.edge_id;
    group.append(svg("path", { class: "edge-path", d: path }));
    group.append(svg("path", { class: "edge-hit", d: path }));
    group.addEventListener("click", (event) => {
      event.stopPropagation();
      select("edge", edge.edge_id);
    });
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.stopPropagation();
        select("edge", edge.edge_id);
      }
    });
    edgeGroups.push(group);
  });
  replaceChildren(el.edges, edgeGroups);

  const nodeGroups = [];
  state.nodes.forEach((node, id) => {
    const position = positions.get(id);
    if (!position) return;
    const classes = ["node", outgoing.get(id) === 0 ? "frontier" : "explored"];
    if (state.selectedKind === "node" && state.selected === id) classes.push("selected");
    if (node.metadata?.best || id === currentBestId()) classes.push("best");
    if (!visible.has(id)) classes.push("dimmed");
    const group = svg("g", {
      class: classes.join(" "),
      transform: `translate(${position.x} ${position.y})`,
      tabindex: "0",
      role: "button",
      "aria-label": `Inspect state ${id}`,
    });
    group.append(svg("rect", { x: 0, y: 0, width: 142, height: 54, rx: 3 }));
    group.append(textNode(10, 17, shortId(id), "node-id"));
    group.append(textNode(10, 34, summarize(node.state), "node-summary"));
    const score = node.metadata?.score;
    if (typeof score === "number") group.append(textNode(132, 17, formatScore(score), "node-score", "end"));
    const choose = (event) => {
      event.stopPropagation();
      select("node", id);
    };
    group.addEventListener("click", choose);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") choose(event);
    });
    nodeGroups.push(group);
  });
  replaceChildren(el.nodes, nodeGroups);
  applyTransform();
}

function layoutNodes() {
  const byStep = new Map();
  state.nodes.forEach((node) => {
    const step = Number.isInteger(node.created_step) ? node.created_step : 0;
    if (!byStep.has(step)) byStep.set(step, []);
    byStep.get(step).push(node);
  });
  const steps = [...byStep.keys()].sort((a, b) => a - b);
  const positions = new Map();
  steps.forEach((step, column) => {
    byStep.get(step).sort((a, b) => a.state_id.localeCompare(b.state_id));
    byStep.get(step).forEach((node, row) => {
      positions.set(node.state_id, { x: column * 205 + 38, y: row * 82 + 42, column, step });
    });
  });
  return positions;
}

function renderBands(positions) {
  const columns = new Map();
  let maxY = 400;
  positions.forEach((position) => {
    columns.set(position.column, position.step);
    maxY = Math.max(maxY, position.y + 94);
  });
  return [...columns.entries()].map(([column, step]) => {
    const group = svg("g", { class: "growth-band" });
    group.append(svg("rect", { x: column * 205 + 14, y: 14, width: 190, height: maxY }));
    group.append(textNode(column * 205 + 28, 34, `STEP ${step}`, ""));
    return group;
  });
}

function edgePath(from, to) {
  const x1 = from.x + 142;
  const y1 = from.y + 27;
  const x2 = to.x - 4;
  const y2 = to.y + 27;
  const bend = Math.max(34, (x2 - x1) * 0.48);
  return `M${x1},${y1} C${x1 + bend},${y1} ${x2 - bend},${y2} ${x2},${y2}`;
}

function select(kind, id) {
  state.selectedKind = kind;
  state.selected = id;
  renderGraph();
  renderInspector();
}

function renderInspector() {
  if (!state.selected) return;
  if (state.selectedKind === "edge") renderEdgeInspector(state.edges.get(state.selected));
  else renderNodeInspector(state.nodes.get(state.selected));
}

function renderNodeInspector(node) {
  if (!node) return;
  const links = incidentEdges(node.state_id);
  const traces = traceItems(node.metadata, node.state);
  el["selection-kind"].textContent = "STATE NODE";
  el["selection-title"].textContent = shortId(node.state_id, 24);
  el["selection-title"].title = node.state_id;
  el["selection-subtitle"].textContent = summarize(node.state, 110);
  renderFacts([
    ["Created step", node.created_step],
    ["Created", node.created_at],
    ["Inbound", links.filter((edge) => edge.child_id === node.state_id).length],
    ["Outbound", links.filter((edge) => edge.parent_id === node.state_id).length],
  ]);
  el["state-value"].textContent = pretty(node.state);
  el["state-metadata"].textContent = pretty(node.metadata);
  renderTraces(traces);
  renderLinks(links, node.state_id);
}

function renderEdgeInspector(edge) {
  if (!edge) return;
  const traces = traceItems(edge.metadata, edge.action);
  el["selection-kind"].textContent = "TRANSITION";
  el["selection-title"].textContent = shortId(edge.edge_id, 24);
  el["selection-title"].title = edge.edge_id;
  el["selection-subtitle"].textContent = `${shortId(edge.parent_id)} → ${shortId(edge.child_id)}`;
  renderFacts([
    ["Created step", edge.created_step],
    ["Created", edge.created_at],
    ["Parent", shortId(edge.parent_id, 16)],
    ["Child", shortId(edge.child_id, 16)],
  ]);
  el["state-value"].textContent = pretty(edge.action);
  el["state-metadata"].textContent = pretty(edge.metadata);
  renderTraces(traces);
  renderLinks([edge], null);
}

function renderFacts(items) {
  const children = items.map(([name, value]) => {
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = name;
    detail.textContent = value == null ? "—" : String(value);
    detail.title = detail.textContent;
    wrapper.append(term, detail);
    return wrapper;
  });
  replaceChildren(el["selection-facts"], children);
}

function traceItems(...sources) {
  for (const source of sources) {
    const found = [];
    collectTraces(displayValue(source), found, new Set(), 0);
    if (found.length) return found;
  }
  return [];
}

function collectTraces(value, found, visited, depth) {
  if (depth > 5 || value == null || typeof value !== "object" || visited.has(value)) return;
  visited.add(value);
  if (Array.isArray(value.trace)) value.trace.forEach((item) => found.push(item));
  Object.entries(value).forEach(([key, child]) => {
    if (key !== "trace") collectTraces(child, found, visited, depth + 1);
  });
}

function renderTraces(traces) {
  el["trace-count"].textContent = String(traces.length);
  if (!traces.length) {
    const empty = document.createElement("p");
    empty.className = "placeholder";
    empty.textContent = "No trace records on this selection.";
    replaceChildren(el["trace-list"], [empty]);
    return;
  }
  const cards = traces.map((trace, index) => {
    const card = document.createElement("article");
    card.className = "trace-card";
    const header = document.createElement("header");
    const label = document.createElement("span");
    const counter = document.createElement("span");
    label.textContent = trace.tool || trace.kind || trace.event || "record";
    counter.textContent = `#${index + 1}`;
    header.append(label, counter);
    const pre = document.createElement("pre");
    pre.textContent = safeJson(trace, 2);
    card.append(header, pre);
    return card;
  });
  replaceChildren(el["trace-list"], cards);
}

function renderLinks(edges, selectedNodeId) {
  el["link-count"].textContent = String(edges.length);
  if (!edges.length) {
    const empty = document.createElement("p");
    empty.className = "placeholder";
    empty.textContent = "No transitions on this selection.";
    replaceChildren(el["link-list"], [empty]);
    return;
  }
  const cards = edges.map((edge) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link-card";
    const header = document.createElement("header");
    const direction = selectedNodeId === edge.child_id ? "INBOUND" : selectedNodeId === edge.parent_id ? "OUTBOUND" : "EDGE";
    const ids = document.createElement("span");
    const tag = document.createElement("span");
    ids.textContent = `${shortId(edge.parent_id)} → ${shortId(edge.child_id)}`;
    tag.textContent = direction;
    header.append(ids, tag);
    const pre = document.createElement("pre");
    pre.textContent = summarize(edge.action, 140);
    button.append(header, pre);
    button.addEventListener("click", () => select("edge", edge.edge_id));
    return button;
  });
  replaceChildren(el["link-list"], cards);
}

function incidentEdges(nodeId) {
  return [...state.edges.values()].filter((edge) => edge.parent_id === nodeId || edge.child_id === nodeId);
}

function selectedLineage() {
  const selectedNode = state.selectedKind === "node"
    ? state.selected
    : state.selectedKind === "edge" ? state.edges.get(state.selected)?.child_id : null;
  const result = new Set();
  if (!selectedNode) return result;
  const stack = [selectedNode];
  const visited = new Set(stack);
  while (stack.length) {
    const child = stack.pop();
    state.edges.forEach((edge) => {
      if (edge.child_id === child) {
        result.add(edge.edge_id);
        if (!visited.has(edge.parent_id)) {
          visited.add(edge.parent_id);
          stack.push(edge.parent_id);
        }
      }
    });
  }
  return result;
}

function currentBestId() {
  return typeof state.run?.metadata?.best_state_id === "string"
    ? state.run.metadata.best_state_id
    : null;
}

function fitGraph() {
  if (!state.nodes.size) return;
  const positions = layoutNodes();
  let maxX = 200;
  let maxY = 150;
  positions.forEach((position) => {
    maxX = Math.max(maxX, position.x + 180);
    maxY = Math.max(maxY, position.y + 90);
  });
  const bounds = el["graph-shell"].getBoundingClientRect();
  state.zoom = clamp(Math.min((bounds.width - 48) / maxX, (bounds.height - 48) / maxY), 0.2, 1.3);
  state.panX = 24;
  state.panY = 24;
  state.fitted = true;
  applyTransform();
}

function zoomAt(factor) {
  const bounds = el.graph.getBoundingClientRect();
  zoomAround(factor, bounds.width / 2, bounds.height / 2);
}

function zoomAround(factor, x, y) {
  const next = clamp(state.zoom * factor, 0.2, 3.5);
  const ratio = next / state.zoom;
  state.panX = x - (x - state.panX) * ratio;
  state.panY = y - (y - state.panY) * ratio;
  state.zoom = next;
  state.fitted = false;
  applyTransform();
}

function applyTransform() {
  el.viewport.setAttribute("transform", `translate(${state.panX} ${state.panY}) scale(${state.zoom})`);
}

function displayValue(value) {
  if (Array.isArray(value)) return value.map(displayValue);
  if (!value || typeof value !== "object") return value;
  const tag = value.__yggdrisil__;
  if (tag === "dict") {
    const entries = value.items.map(([key, item]) => [displayValue(key), displayValue(item)]);
    if (entries.every(([key]) => ["string", "number", "boolean"].includes(typeof key))) {
      return Object.fromEntries(entries.map(([key, item]) => [String(key), item]));
    }
    return { entries };
  }
  if (tag === "tuple") return value.items.map(displayValue);
  if (["set", "frozenset"].includes(tag)) return { [tag]: value.items.map(displayValue) };
  if (tag === "object") {
    return Object.fromEntries([["__type__", value.qualname], ...value.fields.map(([key, item]) => [key, displayValue(item)])]);
  }
  if (tag === "datetime") return value.iso;
  if (tag === "float") return value.value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, displayValue(item)]));
}

function summarize(value, length = 46) {
  const decoded = displayValue(value);
  let text;
  if (Array.isArray(decoded)) {
    text = safeJson(decoded);
  } else if (decoded && typeof decoded === "object") {
    if (Array.isArray(decoded.values)) {
      text = `[${decoded.values.join(", ")}]`;
    } else if ("left" in decoded && "right" in decoded && "op" in decoded) {
      text = `${decoded.left} ${decoded.op} ${decoded.right}`;
    } else {
      const entries = Object.entries(decoded).filter(
        ([key, item]) => key !== "__type__" && !(key === "trace" && Array.isArray(item) && item.length === 0)
      );
      text = entries.length === 1 ? `${entries[0][0]}: ${safeJson(entries[0][1])}` : safeJson(Object.fromEntries(entries));
    }
  } else {
    text = safeJson(decoded);
  }
  text = text.replace(/\s+/g, " ");
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

function pretty(value) {
  return safeJson(displayValue(value), 2);
}

function safeJson(value, spacing = 0) {
  const result = JSON.stringify(value, null, spacing);
  return result === undefined ? String(value) : result;
}

function shortId(id, length = 12) {
  if (!id) return "—";
  return id.length > length ? `${id.slice(0, length)}…` : id;
}

function formatScore(value) {
  return Number.isInteger(value) ? String(value) : value.toPrecision(4);
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function svg(name, attributes) {
  const element = document.createElementNS(NS, name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function textNode(x, y, content, className, anchor = "start") {
  const node = svg("text", { x, y, class: className, "text-anchor": anchor });
  node.textContent = content;
  return node;
}

function replaceChildren(parent, children) {
  parent.replaceChildren(...children);
}

poll();
