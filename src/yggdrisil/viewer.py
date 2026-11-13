from __future__ import annotations

import json
import re
import sqlite3
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/viewer.css": ("viewer.css", "text/css; charset=utf-8"),
    "/viewer.js": ("viewer.js", "text/javascript; charset=utf-8"),
}


class GraphReader:
    """Read raw graph rows without importing serialized application types."""

    def __init__(self, path: str | Path, *, batch_size: int = 1_000) -> None:
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"graph database does not exist: {self.path}")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.batch_size = batch_size

    def updates(
        self,
        *,
        state_after: int,
        edge_after: int,
        evaluation_after: int = 0,
        decision_after: int = 0,
        compact: bool = False,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            states = connection.execute(
                """
                SELECT rowid AS cursor, state_id, state_json, metadata_json,
                       created_at, created_step
                FROM states
                WHERE rowid > ?
                ORDER BY rowid
                LIMIT ?
                """,
                (state_after, self.batch_size + 1),
            ).fetchall()
            edges = connection.execute(
                """
                SELECT rowid AS cursor, edge_id, parent_id, child_id,
                       action_json, metadata_json, created_at, created_step
                FROM edges
                WHERE rowid > ?
                ORDER BY rowid
                LIMIT ?
                """,
                (edge_after, self.batch_size + 1),
            ).fetchall()
            evaluations = connection.execute(
                """
                SELECT rowid AS cursor, evaluation_id, evaluator_id, state_id,
                       evaluator, version, config_hash, metrics_json,
                       metadata_json, created_at
                FROM evaluations
                WHERE rowid > ?
                ORDER BY rowid
                LIMIT ?
                """,
                (evaluation_after, self.batch_size + 1),
            ).fetchall()
            decisions = connection.execute(
                """
                SELECT rowid AS cursor, decision_id, run_id, policy, role, model,
                       selected_state_ids_json, input_context_json,
                       tool_calls_json, output_json, metadata_json,
                       created_at, created_step
                FROM decisions
                WHERE rowid > ?
                ORDER BY rowid
                LIMIT ?
                """,
                (decision_after, self.batch_size + 1),
            ).fetchall()
            # Proposal outcomes update in place, so a rowid cursor would miss
            # pending -> terminal changes. Keep this table as a full snapshot.
            events = connection.execute(
                """
                SELECT rowid AS cursor, event_id, decision_id, run_id,
                       parent_id, child_id, edge_id, action_json, metadata_json,
                       outcome, error, created_at, created_step, proposal_index,
                       sequence_index
                FROM proposal_events
                ORDER BY rowid
                """
            ).fetchall()
            counts = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM states) AS states,
                    (SELECT COUNT(*) FROM edges) AS edges
                """
            ).fetchone()
            run = connection.execute(
                "SELECT * FROM runs ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()

        state_pending = len(states) > self.batch_size
        edge_pending = len(edges) > self.batch_size
        evaluation_pending = len(evaluations) > self.batch_size
        decision_pending = len(decisions) > self.batch_size
        states = states[: self.batch_size]
        edges = edges[: self.batch_size]
        evaluations = evaluations[: self.batch_size]
        decisions = decisions[: self.batch_size]
        payload: dict[str, Any] = {
            "graph": str(self.path),
            "states": [self._state(row) for row in states],
            "edges": [self._edge(row) for row in edges],
            "evaluations": [self._evaluation(row) for row in evaluations],
            "decisions": [self._decision(row) for row in decisions],
            "proposal_events": [self._proposal_event(row) for row in events],
            "state_cursor": states[-1]["cursor"] if states else state_after,
            "edge_cursor": edges[-1]["cursor"] if edges else edge_after,
            "evaluation_cursor": (
                evaluations[-1]["cursor"] if evaluations else evaluation_after
            ),
            "decision_cursor": (
                decisions[-1]["cursor"] if decisions else decision_after
            ),
            "counts": {"states": counts["states"], "edges": counts["edges"]},
            "run": self._run(run) if run is not None else None,
            "pending": any(
                (
                    state_pending,
                    edge_pending,
                    evaluation_pending,
                    decision_pending,
                )
            ),
        }
        if compact:
            payload["states"] = [_state_summary(row) for row in payload["states"]]
            payload["edges"] = [_edge_summary(row) for row in payload["edges"]]
            payload["evaluations"] = [
                _evaluation_summary(row) for row in payload["evaluations"]
            ]
            payload["decisions"] = [
                _decision_summary(row) for row in payload["decisions"]
            ]
            payload["proposal_events"] = [
                _proposal_summary(row) for row in payload["proposal_events"]
            ]
            if payload["run"] is not None:
                metadata = _display_value(payload["run"]["metadata"])
                payload["run"] = {
                    key: value
                    for key, value in payload["run"].items()
                    if key not in {"config", "metadata"}
                }
                payload["run"]["metadata"] = {
                    key: metadata[key]
                    for key in ("best_state_id", "best_score", "stop_reason")
                    if key in metadata
                }
        return payload

    def detail(self, kind: str, identifier: str) -> dict[str, Any]:
        # Table and column names come only from this allowlist, never the URL.
        tables = {
            "state": ("states", "state_id", self._state),
            "edge": ("edges", "edge_id", self._edge),
            "evaluation": ("evaluations", "evaluation_id", self._evaluation),
            "decision": ("decisions", "decision_id", self._decision),
            "proposal": ("proposal_events", "event_id", self._proposal_event),
        }
        if kind not in tables:
            raise ValueError(f"unknown detail kind: {kind}")
        table, column, convert = tables[kind]
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT rowid AS cursor, * FROM {table} WHERE {column} = ?",
                (identifier,),
            ).fetchone()
        if row is None:
            raise KeyError(f"{kind} not found: {identifier}")
        return convert(row)

    def search(self, query: str, *, limit: int = 50) -> dict[str, Any]:
        if not query.strip():
            return {"states": [], "has_more": False}
        if len(query) > 500:
            raise ValueError("search must be at most 500 characters")
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT rowid AS cursor, * FROM states
                WHERE state_id LIKE ? ESCAPE '\\'
                   OR state_json LIKE ? ESCAPE '\\'
                   OR metadata_json LIKE ? ESCAPE '\\'
                ORDER BY created_step DESC, rowid DESC LIMIT ?
                """,
                (pattern, pattern, pattern, limit + 1),
            ).fetchall()
        return {
            "states": [_state_summary(self._state(row)) for row in rows[:limit]],
            "has_more": len(rows) > limit,
        }

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.path.as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        return connection

    @staticmethod
    def _state(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": row["cursor"],
            "state_id": row["state_id"],
            "state": json.loads(row["state_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "created_step": row["created_step"],
        }

    @staticmethod
    def _edge(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": row["cursor"],
            "edge_id": row["edge_id"],
            "parent_id": row["parent_id"],
            "child_id": row["child_id"],
            "action": json.loads(row["action_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "created_step": row["created_step"],
        }

    @staticmethod
    def _evaluation(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": row["cursor"],
            "evaluation_id": row["evaluation_id"],
            "evaluator_id": row["evaluator_id"],
            "state_id": row["state_id"],
            "evaluator": row["evaluator"],
            "version": row["version"],
            "config_hash": row["config_hash"],
            "metrics": json.loads(row["metrics_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _decision(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": row["cursor"],
            "decision_id": row["decision_id"],
            "run_id": row["run_id"],
            "policy": row["policy"],
            "role": row["role"],
            "model": row["model"],
            "selected_state_ids": json.loads(row["selected_state_ids_json"]),
            "input_context": json.loads(row["input_context_json"]),
            "tool_calls": json.loads(row["tool_calls_json"]),
            "output": json.loads(row["output_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "created_at": row["created_at"],
            "created_step": row["created_step"],
        }

    @staticmethod
    def _proposal_event(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cursor": row["cursor"],
            "event_id": row["event_id"],
            "decision_id": row["decision_id"],
            "run_id": row["run_id"],
            "parent_id": row["parent_id"],
            "child_id": row["child_id"],
            "edge_id": row["edge_id"],
            "action": json.loads(row["action_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "outcome": row["outcome"],
            "error": row["error"],
            "created_at": row["created_at"],
            "created_step": row["created_step"],
            "proposal_index": row["proposal_index"],
            "sequence_index": row["sequence_index"],
        }

    @staticmethod
    def _run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "step": row["step"],
            "status": row["status"],
            "config": json.loads(row["config_json"]),
            "metadata": json.loads(row["metadata_json"]),
        }


def _display_value(value: Any) -> Any:
    """Decode display data without importing or constructing application classes."""
    if isinstance(value, list):
        return [_display_value(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__yggdrisil__")
    if tag == "dict":
        entries = [(_display_value(k), _display_value(v)) for k, v in value["items"]]
        if all(isinstance(k, (str, int, float, bool)) for k, _ in entries):
            return {str(k): v for k, v in entries}
        return {"entries": entries}
    if tag in {"tuple", "set", "frozenset"}:
        return [_display_value(item) for item in value["items"]]
    if tag == "object":
        return {k: _display_value(v) for k, v in value["fields"]}
    if tag == "datetime":
        return value["iso"]
    if tag == "float":
        return value["value"]
    return {k: _display_value(v) for k, v in value.items()}


def _summary_fields(value: Any) -> dict[str, Any]:
    decoded = _display_value(value)
    fields = decoded if isinstance(decoded, dict) else {"value": decoded}
    return {
        key: {"count": len(item)}
        if isinstance(item, (list, dict))
        else item[:160]
        if isinstance(item, str)
        else item
        for key, item in fields.items()
    }


def _value_summary(value: Any) -> str:
    fields = _summary_fields(value)
    parts = []
    for key, item in list(fields.items())[:3]:
        if isinstance(item, dict):
            label = f"{item['count']:,} items"
        else:
            label = str(item)
        parts.append(f"{key.replace('_', ' ')}: {label}")
    return " · ".join(parts)[:180] or "Empty state"


def _note(value: Any) -> str:
    decoded = _display_value(value)
    if isinstance(decoded, dict):
        for key in ("rationale", "reason", "note", "summary", "error"):
            if isinstance(decoded.get(key), str) and decoded[key].strip():
                text = str(decoded[key])
                return text[:239] + "…" if len(text) > 240 else text
    return ""


def _state_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: v for k, v in row.items() if k not in {"state", "metadata"}},
        "summary": _value_summary(row["state"]),
        "fields": _summary_fields(row["state"]),
    }


def _edge_summary(row: dict[str, Any]) -> dict[str, Any]:
    summary = _value_summary(row["action"])
    action = row["action"]
    if isinstance(action, dict) and action.get("__yggdrisil__") == "object":
        name = action["qualname"].split(":")[-1].split(".")[-1]
        name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).lower()
        summary = f"{name} · {summary}"
    return {
        **{k: v for k, v in row.items() if k not in {"action", "metadata"}},
        "summary": summary,
        "rationale": _note(row["metadata"]),
    }


def _evaluation_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{k: v for k, v in row.items() if k != "metadata"},
        "metrics": _display_value(row["metrics"]),
    }


def _decision_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **{
            k: v
            for k, v in row.items()
            if k
            not in {
                "input_context",
                "tool_calls",
                "output",
                "metadata",
                "selected_state_ids",
            }
        },
        "selected_state_ids": _display_value(row["selected_state_ids"]),
        "rationale": _note(row["output"]) or _note(row["metadata"]),
        "trace_event_count": len(_display_value(row["tool_calls"])),
    }


def _proposal_summary(row: dict[str, Any]) -> dict[str, Any]:
    return _edge_summary(row)


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        reader: GraphReader,
    ) -> None:
        super().__init__(address, ViewerHandler)
        self.reader = reader


class ViewerHandler(BaseHTTPRequestHandler):
    server: ViewerServer

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/updates":
            self._updates(parse_qs(parsed.query))
            return
        if parsed.path in {"/api/detail", "/api/search"}:
            self._lookup(parsed.path, parse_qs(parsed.query))
            return
        static = _STATIC_FILES.get(parsed.path)
        if static is not None:
            self._static(*static)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def log_message(self, format: str, *args: object) -> None:
        if self.path.startswith("/api/updates"):
            return
        super().log_message(format, *args)

    def _updates(self, query: dict[str, list[str]]) -> None:
        try:
            state_after = _cursor(query, "state_after")
            edge_after = _cursor(query, "edge_after")
            evaluation_after = _cursor(query, "evaluation_after")
            decision_after = _cursor(query, "decision_after")
            payload = self.server.reader.updates(
                state_after=state_after,
                edge_after=edge_after,
                evaluation_after=evaluation_after,
                decision_after=decision_after,
                compact=query.get("compact", ["0"])[0] == "1",
            )
        except (ValueError, sqlite3.Error) as exc:
            self._json(
                {"error": f"could not read graph: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self._json(payload)

    def _lookup(self, path: str, query: dict[str, list[str]]) -> None:
        try:
            if path == "/api/search":
                payload = self.server.reader.search(query.get("q", [""])[0])
            else:
                kind = query.get("kind", [""])[0]
                identifier = query.get("id", [""])[0]
                payload = self.server.reader.detail(kind, identifier)
        except ValueError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except KeyError as exc:
            self._json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except sqlite3.Error as exc:
            self._json(
                {"error": f"could not read graph: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        else:
            self._json(payload)

    def _static(self, name: str, content_type: str) -> None:
        data = files("yggdrisil.web").joinpath(name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy", "default-src 'self'; style-src 'self'"
        )
        self.end_headers()
        self.wfile.write(data)

    def _json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def serve(
    graph: str | Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    reader = GraphReader(graph)
    server = ViewerServer((host, port), reader)
    url = f"http://{host}:{server.server_port}"
    print(f"Inspecting {reader.path}")
    print(f"Open {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _cursor(query: dict[str, list[str]], name: str) -> int:
    raw = query.get(name, ["0"])[0]
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value
