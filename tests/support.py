from __future__ import annotations

from dataclasses import dataclass, field

from yggdrisil.policy import Decision


@dataclass
class ScriptedPolicy:
    batches: list[list]
    seen_status: list = field(default_factory=list)
    _i: int = 0

    async def step(self, graph, status):
        self.seen_status.append(status)
        if self._i >= len(self.batches):
            return []
        batch = self.batches[self._i]
        self._i += 1
        if not batch:
            return []
        return [
            Decision(
                role="scripted",
                proposals=batch,
                selected_state_ids=list(
                    dict.fromkeys(proposal.parent_id for proposal in batch)
                ),
            )
        ]
