from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from yggdrisil.graph.base import ReadOnlyStateGraph
from yggdrisil.limits import RunStatus
from yggdrisil.policy import Proposal

State = TypeVar("State")
Action = TypeVar("Action")

ActionSampler = Callable[[State, random.Random], Sequence[Action]]


class BestFirstPolicy(Generic[State, Action]):
    """Expand the highest-scoring frontier states first."""

    def __init__(
        self,
        sample_actions: ActionSampler[State, Action],
        *,
        n_proposals: int = 1,
        maximize: bool = True,
        seed: int | None = None,
        frontier_limit: int = 1_000,
    ) -> None:
        if n_proposals < 0:
            raise ValueError("n_proposals must be non-negative")
        if frontier_limit <= 0:
            raise ValueError("frontier_limit must be positive")
        self.sample_actions = sample_actions
        self.n_proposals = n_proposals
        self.maximize = maximize
        self.frontier_limit = frontier_limit
        self._rng = random.Random(seed)

    async def step(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> list[Proposal[Action]]:
        del status
        if self.n_proposals == 0:
            return []
        nodes = graph.frontier(limit=self.frontier_limit)
        scored = [node for node in nodes if _score(node.metadata) is not None]
        if nodes and not scored:
            raise ValueError(
                "BestFirstPolicy requires Runner(..., objective=Objective(...))"
            )
        scored.sort(
            key=lambda node: (_score(node.metadata), node.state_id),
            reverse=self.maximize,
        )

        proposals: list[Proposal[Action]] = []
        for node in scored:
            actions = list(self.sample_actions(node.state, self._rng))
            self._rng.shuffle(actions)
            for action in actions:
                proposals.append(
                    Proposal(
                        parent_id=node.state_id,
                        action=action,
                        metadata={"policy": "best_first"},
                    )
                )
                if len(proposals) >= self.n_proposals:
                    return proposals
        return proposals


def _score(metadata: dict[str, object]) -> float | None:
    score = metadata.get("score")
    if isinstance(score, (int, float)):
        return float(score)
    return None
