from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from yggdrisil.serialize import stable_hash
from yggdrisil.types import EvaluationRecord, MetricValue, StateNode

State = TypeVar("State")
EvaluatedState = TypeVar("EvaluatedState", contravariant=True)
ConcurrentResult = TypeVar("ConcurrentResult")


@dataclass(frozen=True)
class EvaluationResult:
    """Structured evidence returned by one evaluator."""

    metrics: dict[str, MetricValue]
    metadata: dict[str, Any] = field(default_factory=dict)


class Evaluator(Protocol[EvaluatedState]):
    name: str
    version: str
    config: Any

    async def evaluate(self, state: EvaluatedState) -> EvaluationResult: ...


class EvaluationStore(Protocol[State]):
    def get_state(self, state_id: str) -> StateNode[State]: ...

    def get_evaluation(
        self,
        state_id: str,
        evaluator_id: str,
    ) -> EvaluationRecord | None: ...

    def add_evaluation(
        self,
        state_id: str,
        *,
        evaluator_id: str,
        evaluator: str,
        version: str,
        config_hash: str,
        result: EvaluationResult,
    ) -> EvaluationRecord: ...


class EvaluatorSuite(Generic[State]):
    """Evaluate an ordered list, optionally starting independent work together."""

    def __init__(
        self,
        evaluators: list[Evaluator[State]],
        *,
        concurrent: bool = False,
    ) -> None:
        self.evaluators = tuple(evaluators)
        self.concurrent = concurrent

    async def evaluate(self, state: State) -> list[EvaluationResult]:
        if self.concurrent:
            return await _gather_cancel_on_error(
                *(evaluator.evaluate(state) for evaluator in self.evaluators)
            )
        return [await evaluator.evaluate(state) for evaluator in self.evaluators]

    async def evaluate_cached(
        self,
        store: EvaluationStore[State],
        state_id: str,
    ) -> list[EvaluationRecord]:
        if self.concurrent:
            ordered_ids: list[str] = []
            unique: dict[str, Evaluator[State]] = {}
            for evaluator in self.evaluators:
                evaluator_id, _ = evaluator_identity(evaluator)
                ordered_ids.append(evaluator_id)
                unique.setdefault(evaluator_id, evaluator)
            computed = await _gather_cancel_on_error(
                *(
                    evaluate_cached(store, state_id, evaluator)
                    for evaluator in unique.values()
                )
            )
            by_id = dict(zip(unique, computed, strict=True))
            return [by_id[evaluator_id] for evaluator_id in ordered_ids]
        records: list[EvaluationRecord] = []
        for evaluator in self.evaluators:
            records.append(await evaluate_cached(store, state_id, evaluator))
        return records


async def evaluate_cached(
    store: EvaluationStore[State],
    state_id: str,
    evaluator: Evaluator[State],
) -> EvaluationRecord:
    """Return cached evidence or run and persist the evaluator once."""

    evaluator_id, config_hash = evaluator_identity(evaluator)
    existing = store.get_evaluation(state_id, evaluator_id)
    if existing is not None:
        return existing

    state = store.get_state(state_id).state
    result = await evaluator.evaluate(state)
    return store.add_evaluation(
        state_id,
        evaluator_id=evaluator_id,
        evaluator=evaluator.name,
        version=evaluator.version,
        config_hash=config_hash,
        result=result,
    )


def evaluator_identity(evaluator: Evaluator[Any]) -> tuple[str, str]:
    if not evaluator.name:
        raise ValueError("evaluator name must not be empty")
    if not evaluator.version:
        raise ValueError("evaluator version must not be empty")
    config_hash = stable_hash(evaluator.config)
    evaluator_id = stable_hash(
        {
            "name": evaluator.name,
            "version": evaluator.version,
            "config_hash": config_hash,
        }
    )
    return evaluator_id, config_hash


async def _gather_cancel_on_error(
    *awaitables: Awaitable[ConcurrentResult],
) -> list[ConcurrentResult]:
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
