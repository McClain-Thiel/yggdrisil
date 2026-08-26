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
from yggdrisil.policies import RandomPolicy
from yggdrisil.policy import Policy, Proposal
from yggdrisil.runner import Runner
from yggdrisil.serialize import stable_hash
from yggdrisil.types import Edge, RunResult, StateNode

__all__ = [
    "CycleError",
    "Edge",
    "GraphError",
    "NavigatorExplorerPolicy",
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
    "stable_hash",
]


def __getattr__(name: str):
    if name == "NavigatorExplorerPolicy":
        from yggdrisil.agents.navigator_explorer import NavigatorExplorerPolicy

        return NavigatorExplorerPolicy
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
