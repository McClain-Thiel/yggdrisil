from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Generic, TypeVar

from yggdrisil.agents.navigator_explorer import (
    ExplorerContext,
    ExplorerResult,
    NavigationPlan,
    NavigatorContext,
    format_explorer_prompt,
    format_navigator_prompt,
)

Action = TypeVar("Action")
State = TypeVar("State")

DEFAULT_NAVIGATOR_INSTRUCTIONS = """\
You are the Navigator for a DAG search. Choose which existing states
deserve the next batch of exploration. Return only state ids that already
exist. Do not invent states or apply actions yourself.
"""

DEFAULT_EXPLORER_INSTRUCTIONS = """\
You are an Explorer for a DAG search. You see one state and may use tools
to probe it — next steps are not pre-enumerated and may be wrong. After
playing, propose only direct child actions for that state. Do not plan
multi-step paths. Each invocation is independent; there is no chat history.
"""


def _require_pydantic_ai() -> Any:
    try:
        from pydantic_ai import Agent
    except ImportError as exc:
        raise ImportError(
            "Install the agents extra: pip install 'yggdrisil[agents]'"
        ) from exc
    return Agent


class PydanticAINavigator:
    def __init__(
        self,
        agent: Any,
        *,
        model: str | None = None,
        prompt: Callable[[NavigatorContext], str] | None = None,
    ) -> None:
        self.agent = agent
        self.model = model
        self.prompt = prompt or format_navigator_prompt
        self.last_trace: list[dict[str, Any]] = []

    async def plan(self, context: NavigatorContext) -> NavigationPlan:
        result = await self.agent.run(self.format_prompt(context))
        self.last_trace = _trace_from_run(result)
        output = result.output
        if isinstance(output, NavigationPlan):
            return output
        requests = getattr(output, "requests", None)
        if requests is None:
            raise TypeError(f"navigator output has no requests: {type(output)}")
        from yggdrisil.agents.navigator_explorer import ExplorationRequest

        return NavigationPlan(
            requests=[
                req
                if isinstance(req, ExplorationRequest)
                else ExplorationRequest(
                    state_id=req.state_id, guidance=getattr(req, "guidance", None)
                )
                for req in requests
            ]
        )

    def format_prompt(self, context: NavigatorContext) -> str:
        return self.prompt(context)


class PydanticAIExplorer(Generic[State, Action]):
    def __init__(
        self,
        agent: Any,
        *,
        model: str | None = None,
        prompt: Callable[[ExplorerContext[State]], str] | None = None,
    ) -> None:
        self.agent = agent
        self.model = model
        self.prompt = prompt or format_explorer_prompt

    async def explore(self, context: ExplorerContext[State]) -> ExplorerResult[Action]:
        result = await self.agent.run(self.format_prompt(context))
        output = result.output
        if isinstance(output, ExplorerResult):
            if not output.trace:
                return ExplorerResult(
                    actions=output.actions,
                    note=output.note,
                    trace=_trace_from_run(result),
                )
            return output
        actions = list(output.actions)
        note = getattr(output, "note", None)
        trace = list(getattr(output, "trace", None) or [])
        trace.extend(_trace_from_run(result))
        return ExplorerResult(actions=actions, note=note, trace=trace)

    def format_prompt(self, context: ExplorerContext[State]) -> str:
        return self.prompt(context)


def make_navigator(
    model: str,
    *,
    instructions: str | None = None,
    tools: Sequence[Callable[..., Any]] = (),
    prompt: Callable[[NavigatorContext], str] | None = None,
) -> PydanticAINavigator:
    Agent = _require_pydantic_ai()
    agent = Agent(
        model,
        output_type=NavigationPlan,
        instructions=instructions or DEFAULT_NAVIGATOR_INSTRUCTIONS,
        tools=list(tools),
    )
    return PydanticAINavigator(agent, model=model, prompt=prompt)


def make_explorer(
    model: str,
    action_type: type[Action],
    *,
    instructions: str | None = None,
    tools: Sequence[Callable[..., Any]] = (),
    prompt: Callable[[ExplorerContext[Any]], str] | None = None,
) -> PydanticAIExplorer[Any, Action]:
    Agent = _require_pydantic_ai()
    from pydantic import BaseModel, Field

    class Output(BaseModel):
        actions: list[action_type]  # type: ignore[valid-type]
        note: str | None = Field(default=None)

    agent = Agent(
        model,
        output_type=Output,
        instructions=instructions or DEFAULT_EXPLORER_INSTRUCTIONS,
        tools=list(tools),
    )
    return PydanticAIExplorer(agent, model=model, prompt=prompt)


def _trace_from_run(result: Any) -> list[dict[str, Any]]:
    """Best-effort extract of tool calls and usage from a PydanticAI result."""
    messages = getattr(result, "all_messages", None)
    if callable(messages):
        messages = messages()
    if not messages:
        messages = getattr(result, "new_messages", None)
        if callable(messages):
            messages = messages()
    trace: list[dict[str, Any]] = []
    for message in messages or ():
        parts = getattr(message, "parts", None) or ()
        for part in parts:
            kind = getattr(part, "part_kind", None) or getattr(part, "kind", "")
            if kind in {"tool-call", "tool_call"}:
                trace.append(
                    {
                        "role": "tool_call",
                        "tool": getattr(part, "tool_name", None),
                        "args": getattr(part, "args", None),
                    }
                )
            elif kind in {"tool-return", "tool_return"}:
                trace.append(
                    {
                        "role": "tool_return",
                        "tool": getattr(part, "tool_name", None),
                        "content": getattr(part, "content", None),
                    }
                )
    usage = getattr(result, "usage", None)
    if callable(usage):
        usage = usage()
    if usage is not None:
        cost = getattr(usage, "cost", None)
        trace.append(
            {
                "role": "usage",
                "requests": getattr(usage, "requests", 0),
                "tool_calls": getattr(usage, "tool_calls", 0),
                "input_tokens": getattr(usage, "input_tokens", 0),
                "output_tokens": getattr(usage, "output_tokens", 0),
                "cache_read_tokens": getattr(usage, "cache_read_tokens", 0),
                "cache_write_tokens": getattr(usage, "cache_write_tokens", 0),
                "cost_usd": str(cost) if cost is not None else None,
            }
        )
    return trace
