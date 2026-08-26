"""Arithmetic probe tools. Next steps are not enumerated; they can fail."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from make24.problem import (
    DEFAULT_TARGET,
    Combine,
    Pool,
    apply_combine,
    canon,
    eval_op,
    pool_solved,
)
from yggdrisil.serialize import dumps


# --8<-- [start:kit]
@dataclass
class ArithmeticTools:
    """Probe tools bound to one pool. Failures are part of the trace."""

    pool: Pool
    target: int = DEFAULT_TARGET
    trace: list[dict[str, Any]] = field(default_factory=list)

    def add(self, a: str, b: str) -> str:
        """Add two numbers currently in the pool."""
        return self._call("add", a, b, "+")

    def subtract(self, a: str, b: str) -> str:
        """Subtract b from a, using two numbers currently in the pool."""
        return self._call("subtract", a, b, "-")

    def multiply(self, a: str, b: str) -> str:
        """Multiply two numbers currently in the pool."""
        return self._call("multiply", a, b, "*")

    def divide(self, a: str, b: str) -> str:
        """Divide a by b, using two numbers currently in the pool."""
        return self._call("divide", a, b, "/")

    def _call(self, name: str, a: str, b: str, op: str) -> str:
        record: dict[str, Any] = {"tool": name, "a": str(a), "b": str(b), "op": op}
        try:
            action = Combine(a, b, op)
            child = apply_combine(self.pool, action)
            result = canon(eval_op(action.left, action.right, action.op))
            record["ok"] = True
            record["result"] = result
            record["remaining"] = list(child.values)
            record["solved"] = pool_solved(child, self.target)
        except (ValueError, ZeroDivisionError) as exc:
            record["ok"] = False
            record["error"] = str(exc)
        self.trace.append(record)
        return dumps(record)


# --8<-- [end:kit]


_KIT: ContextVar[ArithmeticTools | None] = ContextVar("make24_kit", default=None)


def bind_kit(kit: ArithmeticTools) -> Token:
    return _KIT.set(kit)


def reset_kit(token: Token) -> None:
    _KIT.reset(token)


def _require_kit() -> ArithmeticTools:
    kit = _KIT.get()
    if kit is None:
        raise RuntimeError("arithmetic tools used outside an explorer call")
    return kit


def add(a: str, b: str) -> str:
    """Add two numbers currently in the pool."""
    return _require_kit().add(a, b)


def subtract(a: str, b: str) -> str:
    """Subtract b from a, using two numbers currently in the pool."""
    return _require_kit().subtract(a, b)


def multiply(a: str, b: str) -> str:
    """Multiply two numbers currently in the pool."""
    return _require_kit().multiply(a, b)


def divide(a: str, b: str) -> str:
    """Divide a by b, using two numbers currently in the pool."""
    return _require_kit().divide(a, b)


ARITHMETIC_TOOLS = (add, subtract, multiply, divide)
