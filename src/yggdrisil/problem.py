from __future__ import annotations

from typing import Any, Protocol, TypeVar

State = TypeVar("State", covariant=True)
Action = TypeVar("Action", contravariant=True)
StateInv = TypeVar("StateInv")
ActionInv = TypeVar("ActionInv")


class Problem(Protocol[StateInv, ActionInv]):
    """State-space semantics. The framework never interprets domain meaning."""

    @property
    def initial_state(self) -> StateInv: ...

    def state_key(self, state: StateInv) -> str: ...

    def apply(self, state: StateInv, action: ActionInv) -> StateInv: ...

    def validate_state(self, state: StateInv) -> None:
        """Optional. Raise if `state` is illegal."""

    def validate_action(self, state: StateInv, action: ActionInv) -> None:
        """Optional. Raise if `action` cannot be applied to `state`."""

    def decorate(self, state: StateInv, metadata: dict[str, Any]) -> StateInv:
        """Optional. Fold proposal metadata into the child state.

        Use this to persist an agent trace on the state object itself.
        Must not change what `state_key` returns, or DAG merge breaks.
        """
        return state
