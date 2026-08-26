from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from yggdrisil.graph.base import ReadOnlyStateGraph
from yggdrisil.serialize import dumps


def to_networkx(graph: ReadOnlyStateGraph[Any, Any]) -> Any:
    import networkx as nx

    g: Any = nx.MultiDiGraph()
    for node in graph.states():
        g.add_node(
            node.state_id,
            metadata=node.metadata,
            created_at=node.created_at,
            created_step=node.created_step,
            state=node.state,
        )
    for edge in graph.edges():
        g.add_edge(
            edge.parent_id,
            edge.child_id,
            edge_id=edge.edge_id,
            action=edge.action,
            metadata=edge.metadata,
            created_at=edge.created_at,
            created_step=edge.created_step,
        )
    return g


def export_json(
    graph: ReadOnlyStateGraph[Any, Any],
    path: str | Path,
) -> None:
    payload: dict[str, Any] = {
        "states": [
            {
                "state_id": n.state_id,
                "state": json.loads(dumps(n.state)),
                "metadata": json.loads(dumps(n.metadata)),
                "created_at": n.created_at,
                "created_step": n.created_step,
            }
            for n in graph.states()
        ],
        "edges": [
            {
                "edge_id": e.edge_id,
                "parent_id": e.parent_id,
                "child_id": e.child_id,
                "action": json.loads(dumps(e.action)),
                "metadata": json.loads(dumps(e.metadata)),
                "created_at": e.created_at,
                "created_step": e.created_step,
            }
            for e in graph.edges()
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def export_graphml(
    graph: ReadOnlyStateGraph[Any, Any],
    path: str | Path,
) -> None:
    import networkx as nx

    g = to_networkx(graph)
    for _, data in g.nodes(data=True):
        data["state"] = dumps(data.get("state"))
        data["metadata"] = dumps(data.get("metadata") or {})
    for _, _, _, data in g.edges(keys=True, data=True):
        data["action"] = dumps(data.get("action"))
        data["metadata"] = dumps(data.get("metadata") or {})
    nx.write_graphml(g, path)
