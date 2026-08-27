from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from yggdrisil.evaluation import EvaluationResult, EvaluatorSuite, evaluate_cached
from yggdrisil.graph import SQLiteStateGraph


@dataclass
class RecordingEvaluator:
    name: str
    version: str
    config: dict[str, int]
    calls: list[str]

    async def evaluate(self, state: str) -> EvaluationResult:
        self.calls.append(self.name)
        return EvaluationResult(
            metrics={"length": len(state)},
            metadata={"source": self.name},
        )


@pytest.mark.asyncio
async def test_evaluator_suite_is_ordered_and_cached(tmp_path: Path) -> None:
    graph = SQLiteStateGraph[str, str](tmp_path / "graph.sqlite")
    graph.add_state("hello", "hello")
    calls: list[str] = []
    suite = EvaluatorSuite(
        [
            RecordingEvaluator("length", "1", {"offset": 0}, calls),
            RecordingEvaluator("other", "1", {"offset": 1}, calls),
        ]
    )

    first = await suite.evaluate_cached(graph, "hello")
    second = await suite.evaluate_cached(graph, "hello")

    assert calls == ["length", "other"]
    assert [record.evaluator for record in first] == ["length", "other"]
    assert [record.evaluation_id for record in second] == [
        record.evaluation_id for record in first
    ]
    assert graph.evaluations("hello")[0].metrics == {"length": 5}


@pytest.mark.asyncio
async def test_evaluator_identity_includes_version_and_config(tmp_path: Path) -> None:
    graph = SQLiteStateGraph[str, str](tmp_path / "graph.sqlite")
    graph.add_state("state", "state")
    calls: list[str] = []

    records = [
        await evaluate_cached(
            graph,
            "state",
            RecordingEvaluator("metric", version, config, calls),
        )
        for version, config in [
            ("1", {"offset": 0}),
            ("2", {"offset": 0}),
            ("2", {"offset": 1}),
        ]
    ]

    assert len({record.evaluator_id for record in records}) == 3
    assert len(graph.evaluations("state")) == 3
