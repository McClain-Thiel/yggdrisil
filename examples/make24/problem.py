"""Make 24 as a Yggdrisil problem: pools of numbers, Combine actions.

This is tutorial code, not part of the `yggdrisil` package. Copy it and
change the domain.
"""

from __future__ import annotations

import operator
import random
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from yggdrisil.serialize import serializable, stable_hash

DEFAULT_NUMBERS = (1, 3, 4, 6)
DEFAULT_TARGET = 24

_OPS = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
}
_OP_ALIASES = {
    "add": "+",
    "plus": "+",
    "subtract": "-",
    "minus": "-",
    "sub": "-",
    "multiply": "*",
    "times": "*",
    "mul": "*",
    "divide": "/",
    "div": "/",
}


def canon(value: str | int | Fraction) -> str:
    frac = value if isinstance(value, Fraction) else Fraction(str(value))
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"


def as_fraction(value: str | int | Fraction) -> Fraction:
    return value if isinstance(value, Fraction) else Fraction(str(value))


def _sorted_values(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(values, key=lambda item: (as_fraction(item), item)))


# --8<-- [start:pool]
@serializable
@dataclass(frozen=True)
class Pool:
    """Remaining numbers plus the explorer trace that produced this node.

    `values` is identity. `trace` is memory. `state_key` must ignore `trace`.
    """

    values: tuple[str, ...]
    trace: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        normalized = _sorted_values(tuple(canon(v) for v in self.values))
        object.__setattr__(self, "values", normalized)
        object.__setattr__(self, "trace", tuple(self.trace))

    @classmethod
    def from_ints(cls, *numbers: int | str | Fraction) -> Pool:
        return cls(tuple(canon(n) for n in numbers))


# --8<-- [end:pool]


# --8<-- [start:combine]
@serializable
@dataclass(frozen=True)
class Combine:
    """Replace `left` and `right` in the pool with `left op right`."""

    left: str
    right: str
    op: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "left", canon(self.left))
        object.__setattr__(self, "right", canon(self.right))
        op = _OP_ALIASES.get(self.op, self.op)
        if op not in _OPS:
            raise ValueError(f"unknown op {self.op!r}")
        object.__setattr__(self, "op", op)


# --8<-- [end:combine]


def eval_op(left: str, right: str, op: str) -> Fraction:
    op = _OP_ALIASES.get(op, op)
    if op not in _OPS:
        raise ValueError(f"unknown op {op!r}")
    rhs = as_fraction(right)
    if op == "/" and rhs == 0:
        raise ValueError("division by zero")
    return _OPS[op](as_fraction(left), rhs)


def apply_combine(state: Pool, action: Combine) -> Pool:
    remaining = list(state.values)
    try:
        remaining.remove(action.left)
        remaining.remove(action.right)
    except ValueError as exc:
        raise ValueError("operands are not both in the pool") from exc
    remaining.append(canon(eval_op(action.left, action.right, action.op)))
    return Pool(tuple(remaining))


def pool_solved(pool: Pool, target: int = DEFAULT_TARGET) -> bool:
    return len(pool.values) == 1 and as_fraction(pool.values[0]) == target


class Make24:
    """Countdown / make-24. Default puzzle is 1, 3, 4, 6 → 24."""

    def __init__(
        self,
        numbers: tuple[int, ...] = DEFAULT_NUMBERS,
        *,
        target: int = DEFAULT_TARGET,
    ) -> None:
        if len(numbers) < 2:
            raise ValueError("need at least two starting numbers")
        self.numbers = tuple(numbers)
        self.target = target
        self.initial_state = Pool.from_ints(*numbers)

    # --8<-- [start:state_key]
    def state_key(self, state: Pool) -> str:
        return stable_hash({"values": state.values, "target": self.target})

    # --8<-- [end:state_key]

    def apply(self, state: Pool, action: Combine) -> Pool:
        return apply_combine(state, action)

    # --8<-- [start:decorate]
    def decorate(self, state: Pool, metadata: dict[str, Any]) -> Pool:
        """Stamp the explorer's tool trace onto the child state.

        Identity is unchanged: `state_key` still hashes `values` only.
        The first writer wins; later merges keep the stored state.
        """
        raw = metadata.get("trace") or ()
        return Pool(values=state.values, trace=tuple(raw))

    # --8<-- [end:decorate]

    def validate_state(self, state: Pool) -> None:
        for value in state.values:
            as_fraction(value)

    def validate_action(self, state: Pool, action: Combine) -> None:
        apply_combine(state, action)

    def legal_actions(self, state: Pool) -> list[Combine]:
        if self.solved(state) or len(state.values) < 2:
            return []
        seen: set[tuple[str, str, str]] = set()
        actions: list[Combine] = []
        values = state.values
        for i, left in enumerate(values):
            for j, right in enumerate(values):
                if i == j:
                    continue
                for op in _OPS:
                    if op == "/" and as_fraction(right) == 0:
                        continue
                    key = (left, right, op)
                    if key in seen:
                        continue
                    seen.add(key)
                    actions.append(Combine(left, right, op))
        return actions

    def sample_actions(self, state: Pool, rng: random.Random) -> list[Combine]:
        legal = self.legal_actions(state)
        if not legal:
            return []
        return [rng.choice(legal)]

    def solved(self, state: Pool) -> bool:
        return pool_solved(state, self.target)

    def distance(self, state: Pool) -> float:
        if not state.values:
            return float(self.target)
        return min(abs(float(as_fraction(v) - self.target)) for v in state.values)


def render_pool(pool: Pool, *, target: int = DEFAULT_TARGET) -> str:
    inner = ", ".join(pool.values)
    mark = ""
    if pool_solved(pool, target):
        mark = f" = {target}"
    return f"[{inner}]{mark}"
