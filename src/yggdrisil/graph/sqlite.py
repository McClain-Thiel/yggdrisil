from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, TypeVar

from yggdrisil.exceptions import CycleError, UnknownStateError
from yggdrisil.graph.base import StateGraph
from yggdrisil.serialize import dumps, loads, stable_hash
from yggdrisil.types import Edge, RunRecord, StateNode

State = TypeVar("State")
Action = TypeVar("Action")

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS states (
    state_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    created_step INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id TEXT PRIMARY KEY,
    parent_id TEXT NOT NULL REFERENCES states(state_id),
    child_id TEXT NOT NULL REFERENCES states(state_id),
    action_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    created_step INTEGER NOT NULL,
    UNIQUE(parent_id, child_id, action_json)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    step INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_edges_parent ON edges(parent_id);
CREATE INDEX IF NOT EXISTS idx_edges_child ON edges(child_id);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteStateGraph(StateGraph[State, Action], Generic[State, Action]):
    """SQLite-backed DAG of unique states and labeled actions."""

    def __init__(self, path: str | Path) -> None:
        import sqlite3

        self.path = Path(path)
        if self.path.name != ":memory:" and str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(self.path)
        else:
            db_path = ":memory:"
            self.path = Path(":memory:")
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteStateGraph[State, Action]:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add_state(
        self,
        state_id: str,
        state: State,
        metadata: dict[str, Any] | None = None,
        *,
        created_step: int = 0,
    ) -> StateNode[State]:
        existing = self._conn.execute(
            "SELECT 1 FROM states WHERE state_id = ?", (state_id,)
        ).fetchone()
        if existing:
            return self.get_state(state_id)
        payload = dumps(state)
        meta = dumps(metadata or {})
        self._conn.execute(
            """
            INSERT INTO states (state_id, state_json, metadata_json, created_at, created_step)
            VALUES (?, ?, ?, ?, ?)
            """,
            (state_id, payload, meta, _utcnow(), created_step),
        )
        self._conn.commit()
        return self.get_state(state_id)

    def add_edge(
        self,
        parent_id: str,
        child_id: str,
        action: Action,
        metadata: dict[str, Any] | None = None,
        *,
        created_step: int = 0,
    ) -> Edge[Action]:
        if not self.has_state(parent_id):
            raise UnknownStateError(parent_id)
        if not self.has_state(child_id):
            raise UnknownStateError(child_id)
        if parent_id == child_id or self._reaches(child_id, parent_id):
            raise CycleError(
                f"edge {parent_id} -> {child_id} would create a cycle"
            )
        action_json = dumps(action)
        edge_id = stable_hash(
            {"parent_id": parent_id, "child_id": child_id, "action": action_json}
        )
        existing = self._conn.execute(
            "SELECT edge_id FROM edges WHERE edge_id = ?", (edge_id,)
        ).fetchone()
        if existing:
            return self._load_edge(edge_id)
        self._conn.execute(
            """
            INSERT INTO edges (
                edge_id, parent_id, child_id, action_json,
                metadata_json, created_at, created_step
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge_id,
                parent_id,
                child_id,
                action_json,
                dumps(metadata or {}),
                _utcnow(),
                created_step,
            ),
        )
        self._conn.commit()
        return self._load_edge(edge_id)

    def get_state(self, state_id: str) -> StateNode[State]:
        row = self._conn.execute(
            "SELECT * FROM states WHERE state_id = ?", (state_id,)
        ).fetchone()
        if row is None:
            raise UnknownStateError(state_id)
        return self._row_to_state(row)

    def has_state(self, state_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM states WHERE state_id = ?", (state_id,)
        ).fetchone()
        return row is not None

    def parents(self, state_id: str) -> list[StateNode[State]]:
        self.get_state(state_id)
        rows = self._conn.execute(
            """
            SELECT s.* FROM states s
            JOIN edges e ON e.parent_id = s.state_id
            WHERE e.child_id = ?
            ORDER BY s.state_id
            """,
            (state_id,),
        ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def children(self, state_id: str) -> list[StateNode[State]]:
        self.get_state(state_id)
        rows = self._conn.execute(
            """
            SELECT s.* FROM states s
            JOIN edges e ON e.child_id = s.state_id
            WHERE e.parent_id = ?
            ORDER BY s.state_id
            """,
            (state_id,),
        ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def ancestors(self, state_id: str) -> list[StateNode[State]]:
        return self._walk(state_id, upward=True)

    def descendants(self, state_id: str) -> list[StateNode[State]]:
        return self._walk(state_id, upward=False)

    def frontier(self) -> list[StateNode[State]]:
        rows = self._conn.execute(
            """
            SELECT s.* FROM states s
            LEFT JOIN edges e ON e.parent_id = s.state_id
            WHERE e.edge_id IS NULL
            ORDER BY s.state_id
            """
        ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def states(self) -> list[StateNode[State]]:
        rows = self._conn.execute(
            "SELECT * FROM states ORDER BY created_step, state_id"
        ).fetchall()
        return [self._row_to_state(r) for r in rows]

    def edges(self) -> list[Edge[Action]]:
        rows = self._conn.execute(
            "SELECT edge_id FROM edges ORDER BY created_step, edge_id"
        ).fetchall()
        return [self._load_edge(r["edge_id"]) for r in rows]

    def __len__(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM states").fetchone()
        return int(row["n"])

    def edge_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()
        return int(row["n"])

    def save_run(
        self,
        run_id: str,
        *,
        step: int,
        status: str,
        config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunRecord:
        now = _utcnow()
        existing = self._conn.execute(
            "SELECT created_at FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        created = existing["created_at"] if existing else now
        self._conn.execute(
            """
            INSERT INTO runs (
                run_id, created_at, updated_at, step, status, config_json, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                updated_at = excluded.updated_at,
                step = excluded.step,
                status = excluded.status,
                config_json = excluded.config_json,
                metadata_json = excluded.metadata_json
            """,
            (
                run_id,
                created,
                now,
                step,
                status,
                dumps(config or {}),
                dumps(metadata or {}),
            ),
        )
        self._conn.commit()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> RunRecord:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return RunRecord(
            run_id=row["run_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            step=row["step"],
            status=row["status"],
            config=loads(row["config_json"]),
            metadata=loads(row["metadata_json"]),
        )

    def latest_run(self) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT run_id FROM runs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return self.get_run(row["run_id"])

    def _row_to_state(self, row: Any) -> StateNode[State]:
        return StateNode(
            state_id=row["state_id"],
            state=loads(row["state_json"]),
            metadata=loads(row["metadata_json"]),
            created_at=row["created_at"],
            created_step=row["created_step"],
        )

    def _load_edge(self, edge_id: str) -> Edge[Action]:
        row = self._conn.execute(
            "SELECT * FROM edges WHERE edge_id = ?", (edge_id,)
        ).fetchone()
        return Edge(
            edge_id=row["edge_id"],
            parent_id=row["parent_id"],
            child_id=row["child_id"],
            action=loads(row["action_json"]),
            metadata=loads(row["metadata_json"]),
            created_at=row["created_at"],
            created_step=row["created_step"],
        )

    def _adjacency(self) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        children: dict[str, list[str]] = {}
        parents: dict[str, list[str]] = {}
        for row in self._conn.execute("SELECT parent_id, child_id FROM edges"):
            children.setdefault(row["parent_id"], []).append(row["child_id"])
            parents.setdefault(row["child_id"], []).append(row["parent_id"])
        return parents, children

    def _reaches(self, start: str, target: str) -> bool:
        _, children = self._adjacency()
        seen = {start}
        stack = [start]
        while stack:
            node = stack.pop()
            for nxt in children.get(node, []):
                if nxt == target:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    def _walk(self, state_id: str, *, upward: bool) -> list[StateNode[State]]:
        self.get_state(state_id)
        parents, children = self._adjacency()
        links = parents if upward else children
        ordered: list[str] = []
        seen = {state_id}
        queue = [state_id]
        while queue:
            node = queue.pop(0)
            nxts = sorted(links.get(node, []))
            for nxt in nxts:
                if nxt not in seen:
                    seen.add(nxt)
                    ordered.append(nxt)
                    queue.append(nxt)
        return [self.get_state(sid) for sid in ordered]
