from typing import Any

from yggdrisil.cache import ToolCache, cached_tool
from yggdrisil.exceptions import (
    CycleError,
    GraphError,
    SerializationError,
    UnknownStateError,
    YggdrisilError,
)
from yggdrisil.graph import ReadOnlyStateGraph, SQLiteStateGraph
from yggdrisil.limits import RunLimits, RunStatus
from yggdrisil.objective import Objective
from yggdrisil.policies import BestFirstPolicy, RandomPolicy
from yggdrisil.policy import Policy, Proposal
from yggdrisil.runner import Runner
from yggdrisil.serialize import serializable, stable_hash
from yggdrisil.types import Edge, RunResult, StateNode

__all__ = [
    "CycleError",
    "BestFirstPolicy",
    "Edge",
    "GraphError",
    "NavigatorExplorerPolicy",
    "Objective",
    "Policy",
    "Proposal",
    "RandomPolicy",
    "ReadOnlyStateGraph",
    "RunLimits",
    "RunResult",
    "RunStatus",
    "Runner",
    "SQLiteStateGraph",
    "SerializationError",
    "StateNode",
    "ToolCache",
    "UnknownStateError",
    "YggdrisilError",
    "cached_tool",
    "serializable",
    "stable_hash",
]


def __getattr__(name: str) -> Any:
    if name == "NavigatorExplorerPolicy":
        from yggdrisil.agents.navigator_explorer import NavigatorExplorerPolicy

        return NavigatorExplorerPolicy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
