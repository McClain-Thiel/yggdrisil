from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar

from yggdrisil.serialize import stable_hash
from yggdrisil.types import EvaluationRecord, MetricValue, StateNode

State = TypeVar("State")
EvaluatedState = TypeVar("EvaluatedState", contravariant=True)


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
    """Evaluate a state with an ordered list of independent evaluators."""

    def __init__(self, evaluators: list[Evaluator[State]]) -> None:
        self.evaluators = tuple(evaluators)

    async def evaluate(self, state: State) -> list[EvaluationResult]:
        return [await evaluator.evaluate(state) for evaluator in self.evaluators]

    async def evaluate_cached(
        self,
        store: EvaluationStore[State],
        state_id: str,
    ) -> list[EvaluationRecord]:
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
