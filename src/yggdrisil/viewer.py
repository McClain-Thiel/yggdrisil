from __future__ import annotations

import json
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

    def updates(self, *, state_after: int, edge_after: int) -> dict[str, Any]:
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
        states = states[: self.batch_size]
        edges = edges[: self.batch_size]
        return {
            "graph": str(self.path),
            "states": [self._state(row) for row in states],
            "edges": [self._edge(row) for row in edges],
            "state_cursor": states[-1]["cursor"] if states else state_after,
            "edge_cursor": edges[-1]["cursor"] if edges else edge_after,
            "counts": {"states": counts["states"], "edges": counts["edges"]},
            "run": self._run(run) if run is not None else None,
            "pending": state_pending or edge_pending,
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
            payload = self.server.reader.updates(
                state_after=state_after,
                edge_after=edge_after,
            )
        except (ValueError, sqlite3.Error) as exc:
            self._json(
                {"error": f"could not read graph: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
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
