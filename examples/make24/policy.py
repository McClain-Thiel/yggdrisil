"""Navigator + explorer policies for the Make 24 tutorial."""

from __future__ import annotations

import random
from typing import Any

from make24.problem import (
    DEFAULT_TARGET,
    Combine,
    Make24,
    Pool,
    render_pool,
)
from make24.tools import (
    ARITHMETIC_TOOLS,
    ArithmeticTools,
    bind_kit,
    reset_kit,
)
from yggdrisil.agents import (
    ExplorationRequest,
    ExplorerResult,
    NavigationPlan,
    NavigatorExplorerPolicy,
)

GOAL = "Make 24. Use every number in the pool; order of operations can vary."


class TinyMake24LM:
    """Offline stand-in: same tools as a real explorer, no API key.

    It probes the current pool with add/subtract/multiply/divide, ranks
    first steps by whether a short lookahead still hits the target, and
    returns those Combine actions plus the tool trace.
    """

    def __init__(
        self,
        problem: Make24 | None = None,
        *,
        seed: int = 0,
        n_requests: int = 2,
        n_moves: int = 2,
    ) -> None:
        self.problem = problem or Make24()
        self.n_requests = n_requests
        self.n_moves = n_moves
        self._rng = random.Random(seed)

    async def plan(self, context) -> NavigationPlan:
        live = [
            state_id
            for state_id in context.frontier_ids
            if not self._solved_id(context, state_id)
        ]
        if not live:
            live = list(context.frontier_ids)
        self._rng.shuffle(live)
        chosen = live[: self.n_requests] or list(context.frontier_ids)[:1]
        return NavigationPlan(
            requests=[
                ExplorationRequest(
                    state_id=state_id,
                    guidance="Probe with arithmetic tools; prefer lines that can still hit 24.",
                )
                for state_id in chosen
            ]
        )

    # --8<-- [start:explore]
    async def explore(self, context) -> ExplorerResult[Combine]:
        pool: Pool = context.state
        kit = ArithmeticTools(pool, target=self.problem.target)
        if self.problem.solved(pool) or len(pool.values) < 2:
            return ExplorerResult(
                actions=[], note="solved or terminal", trace=kit.trace
            )
        ranked = self._rank_first_moves(pool, kit)
        picks = [action for action, _score in ranked[: self.n_moves]]
        note = None
        if picks:
            first = picks[0]
            note = f"{first.left} {first.op} {first.right}"
        return ExplorerResult(actions=picks, note=note, trace=kit.trace)

    # --8<-- [end:explore]

    def _rank_first_moves(
        self, pool: Pool, kit: ArithmeticTools
    ) -> list[tuple[Combine, float]]:
        scored: list[tuple[Combine, float]] = []
        seen: set[tuple[str, str, str]] = set()
        values = pool.values
        for i, left in enumerate(values):
            for j, right in enumerate(values):
                if i == j:
                    continue
                for name, op in (
                    ("add", "+"),
                    ("subtract", "-"),
                    ("multiply", "*"),
                    ("divide", "/"),
                ):
                    key = (left, right, op)
                    if key in seen:
                        continue
                    seen.add(key)
                    getattr(kit, name)(left, right)
                    record = kit.trace[-1]
                    if not record.get("ok"):
                        continue
                    action = Combine(left, right, op)
                    child = Pool(tuple(record["remaining"]))
                    scored.append((action, self._lookahead(child)))
        scored.sort(key=lambda item: (item[1], self._rng.random()), reverse=True)
        return scored

    def _lookahead(self, state: Pool, depth: int = 3) -> float:
        if self.problem.solved(state):
            return 1_000.0 - 10.0 * (3 - depth)
        if len(state.values) < 2 or depth <= 0:
            dist = self.problem.distance(state)
            leftover = len(state.values) - 1
            return -dist - 8.0 * leftover
        best = -1e9
        for action in self.problem.legal_actions(state):
            child = self.problem.apply(state, action)
            best = max(best, self._lookahead(child, depth - 1))
            if best >= 900:
                break
        return best

    def _solved_id(self, context, state_id: str) -> bool:
        note = context.summaries.get(state_id, "")
        if note == "solved or terminal":
            return True
        for item in context.recent:
            if item["state_id"] == state_id:
                meta = item.get("metadata") or {}
                if meta.get("note") == "solved or terminal":
                    return True
        return False


def tiny_policy(
    *,
    seed: int = 0,
    problem: Make24 | None = None,
    goal: str = GOAL,
    max_requests: int = 2,
) -> NavigatorExplorerPolicy[Pool, Combine]:
    puzzle = problem or Make24()
    lm = TinyMake24LM(puzzle, seed=seed, n_requests=max_requests)
    return NavigatorExplorerPolicy(lm, lm, goal=goal, max_requests=max_requests)


def format_make24_prompt(context) -> str:
    pool: Pool = context.state
    lines = [
        f"GOAL: {context.goal or GOAL}",
        f"POOL: {render_pool(pool)}",
        f"TARGET: {DEFAULT_TARGET}",
        "Tools: add, subtract, multiply, divide.",
        "They operate on numbers currently in the pool and fail otherwise.",
        "There is no list of legal moves. Probe with tools, then propose",
        "Combine(left, right, op) for the one-step combinations to commit.",
        f"Navigator guidance: {context.guidance or '(none)'}",
    ]
    if pool.trace:
        lines.append("TRACE ALREADY ON THIS STATE:")
        for step in pool.trace[-8:]:
            lines.append(f"  {step}")
    return "\n".join(lines)


class _ToolBoundExplorer:
    def __init__(self, inner: Any, target: int) -> None:
        self._inner = inner
        self._target = target

    async def explore(self, context) -> ExplorerResult[Combine]:
        kit = ArithmeticTools(context.state, target=self._target)
        token = bind_kit(kit)
        try:
            result = await self._inner.explore(context)
        finally:
            reset_kit(token)
        trace = list(kit.trace) or list(result.trace)
        return ExplorerResult(
            actions=result.actions,
            note=result.note,
            trace=trace,
        )


def llm_policy(
    model: str,
    *,
    problem: Make24 | None = None,
    goal: str = GOAL,
    max_requests: int = 2,
) -> NavigatorExplorerPolicy[Pool, Combine]:
    """Navigator–explorer policy backed by a real PydanticAI model + arithmetic tools."""
    from yggdrisil.agents.pydantic_ai import make_explorer, make_navigator

    puzzle = problem or Make24()
    navigator = make_navigator(
        model,
        instructions=(
            "You navigate a Make-24 search DAG. Pick existing state ids "
            "from the frontier. Prefer pools that can still reach 24."
        ),
    )
    explorer = make_explorer(
        model,
        Combine,
        tools=ARITHMETIC_TOOLS,
        instructions=(
            "You explore one number pool. Use add/subtract/multiply/divide "
            "to try combinations — do not invent arithmetic. Then return "
            "Combine actions using only numbers currently in the pool. "
            "op must be one of +, -, *, /."
        ),
        prompt=format_make24_prompt,
    )
    return NavigatorExplorerPolicy(
        navigator,
        _ToolBoundExplorer(explorer, puzzle.target),
        goal=goal,
        max_requests=max_requests,
    )
