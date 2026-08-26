from __future__ import annotations

import random
from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from yggdrisil.graph.base import ReadOnlyStateGraph
from yggdrisil.limits import RunStatus
from yggdrisil.policy import Proposal
from yggdrisil.types import StateNode

State = TypeVar("State")
Action = TypeVar("Action")

ActionSampler = Callable[[State, random.Random], Sequence[Action]]


class RandomPolicy(Generic[State, Action]):
    """Baseline policy: sample actions from existing states."""

    def __init__(
        self,
        sample_actions: ActionSampler,
        *,
        n_proposals: int = 1,
        seed: int | None = None,
        frontier_only: bool = False,
    ) -> None:
        self.sample_actions = sample_actions
        self.n_proposals = n_proposals
        self.frontier_only = frontier_only
        self._rng = random.Random(seed)

    async def step(
        self,
        graph: ReadOnlyStateGraph[State, Action],
        status: RunStatus,
    ) -> list[Proposal[Action]]:
        del status
        nodes = graph.frontier() if self.frontier_only else graph.states()
        if not nodes:
            return []
        proposals: list[Proposal[Action]] = []
        for _ in range(self.n_proposals):
            parent = self._pick(nodes)
            actions = list(self.sample_actions(parent.state, self._rng))
            if not actions:
                continue
            action = actions[0] if len(actions) == 1 else self._rng.choice(list(actions))
            proposals.append(Proposal(parent_id=parent.state_id, action=action))
        return proposals

    def _pick(self, nodes: Sequence[StateNode[State]]) -> StateNode[State]:
        ordered = sorted(nodes, key=lambda n: n.state_id)
        return ordered[self._rng.randrange(len(ordered))]
