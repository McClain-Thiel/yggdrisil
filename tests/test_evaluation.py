from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from yggdrisil import evaluator_identity
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
    assert evaluator_identity(
        RecordingEvaluator("metric", "2", {"offset": 1}, calls)
    )[0] == records[-1].evaluator_id


@pytest.mark.asyncio
async def test_evaluator_suite_can_run_concurrently_and_preserve_order(
    tmp_path: Path,
) -> None:
    graph = SQLiteStateGraph[str, str](tmp_path / "graph.sqlite")
    graph.add_state("state", "state")
    started: list[str] = []
    both_started = asyncio.Event()

    @dataclass
    class CoordinatedEvaluator:
        name: str
        version: str = "1"
        config: dict[str, int] = field(default_factory=dict)

        async def evaluate(self, state: str) -> EvaluationResult:
            started.append(self.name)
            if len(started) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.1)
            return EvaluationResult(metrics={"length": len(state)})

    suite = EvaluatorSuite(
        [CoordinatedEvaluator("first"), CoordinatedEvaluator("second")],
        concurrent=True,
    )

    records = await suite.evaluate_cached(graph, "state")

    assert started == ["first", "second"]
    assert [record.evaluator for record in records] == ["first", "second"]


@pytest.mark.asyncio
async def test_concurrent_suite_cancels_siblings_after_failure() -> None:
    peer_started = asyncio.Event()
    peer_cancelled = asyncio.Event()

    class FailingEvaluator:
        name = "failing"
        version = "1"
        config = None

        async def evaluate(self, state: str) -> EvaluationResult:
            await peer_started.wait()
            raise RuntimeError("failed")

    class WaitingEvaluator:
        name = "waiting"
        version = "1"
        config = None

        async def evaluate(self, state: str) -> EvaluationResult:
            peer_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                peer_cancelled.set()
                raise
            return EvaluationResult(metrics={})

    suite = EvaluatorSuite(
        [FailingEvaluator(), WaitingEvaluator()],
        concurrent=True,
    )

    with pytest.raises(RuntimeError, match="failed"):
        await suite.evaluate("state")

    assert peer_cancelled.is_set()


@pytest.mark.asyncio
async def test_concurrent_cached_suite_coalesces_duplicate_identity(
    tmp_path: Path,
) -> None:
    graph = SQLiteStateGraph[str, str](tmp_path / "graph.sqlite")
    graph.add_state("state", "state")
    calls: list[str] = []
    evaluator = RecordingEvaluator("same", "1", {"offset": 0}, calls)
    suite = EvaluatorSuite([evaluator, evaluator], concurrent=True)

    records = await suite.evaluate_cached(graph, "state")

    assert calls == ["same"]
    assert len(records) == 2
    assert records[0].evaluation_id == records[1].evaluation_id
