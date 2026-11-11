# ruff: noqa: E402
"""Exercise the actual ADK runner, persistence and delegation without paid calls."""

import json
from typing import Any

import pytest

# Keep the base test suite usable without the optional agent runtime.
pytest.importorskip("google.adk")
from google.adk import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from pydantic import BaseModel

from yggdrisil.agents import ToolInputError
from yggdrisil.agents.adk import (
    AdkInvocation,
    UsageLimits,
    make_explorer,
    retryable_tool,
)


class ScriptedModel(BaseLlm):
    respond: Any

    async def generate_content_async(self, llm_request, stream=False):
        parts = self.respond(llm_request)
        yield LlmResponse(
            content=types.Content(role="model", parts=parts),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5
            ),
            custom_metadata={"cost_usd": "0.001"},
        )


def call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def answer(value):
    return types.Part(text=json.dumps(value))


async def test_native_shared_state_delegation_restart_and_completed_replay(tmp_path):
    calls = []

    def save_plan(plan: str, tool_context: ToolContext) -> str:
        tool_context.state["user:plan"] = plan
        return "saved"

    class Report(BaseModel):
        note: str

    def make():
        def research(request):
            calls.append("research")
            return [answer({"note": "Cited finding"})]

        def manager(request):
            calls.append("manager")
            responses = [
                p.function_response
                for c in request.contents
                for p in c.parts or []
                if p.function_response
            ]
            if not responses:
                return [
                    call("save_plan", plan="Keep this plan"),
                    call("research", request="Find evidence"),
                ]
            return [answer({"note": "Use shared evidence"})]

        child = Agent(
            name="research",
            mode="single_turn",
            model=ScriptedModel(model="fixture", respond=research),
            output_schema=Report,
            output_key="report",
        )
        parent = Agent(
            name="navigator",
            model=ScriptedModel(model="fixture", respond=manager),
            tools=[save_plan],
            sub_agents=[child],
            output_schema=Report,
            output_key="result",
        )
        return AdkInvocation(
            parent, session_path=tmp_path / "agents.sqlite", user_id="experiment"
        )

    runtime = make()
    assert await runtime.run("Explore", request_id="step-0") == {
        "note": "Use shared evidence"
    }
    assert calls == ["manager", "research", "manager"]
    assert sum(e.get("requests", 0) for e in runtime.last_trace) == 3
    assert {e.get("agent") for e in runtime.last_trace} >= {"navigator", "research"}
    restored = make()
    assert await restored.run("Explore", request_id="step-0") == {
        "note": "Use shared evidence"
    }
    assert len(calls) == 3
    from google.adk.sessions.sqlite_session_service import SqliteSessionService

    sessions = SqliteSessionService(str(tmp_path / "agents.sqlite"))
    session = await sessions.get_session(
        app_name="yggdrisil", user_id="experiment", session_id="navigator"
    )
    assert session.state["user:plan"] == "Keep this plan"
    assert session.state["report"] == {"note": "Cited finding"}
    await sessions.close()


async def test_tool_input_is_feedback_but_infrastructure_failure_propagates():
    def bad_input() -> None:
        raise ToolInputError("Unknown identifier; resolve it first")

    assert "resolve it first" in (await retryable_tool(bad_input)())["error"]

    async def unavailable() -> None:
        raise RuntimeError("storage unavailable")

    with pytest.raises(RuntimeError, match="storage unavailable"):
        await retryable_tool(unavailable)()


@pytest.mark.parametrize("limit", ["request", "tool", "cost"])
async def test_budgets_include_delegated_calls(limit):
    from decimal import Decimal

    counter = []

    def retrieve() -> str:
        counter.append(1)
        return "evidence"

    model = ScriptedModel(model="fixture", respond=lambda request: [call("retrieve")])
    runtime = make_explorer(
        model,
        str,
        tools=[retrieve],
        usage_limits=UsageLimits(
            request_limit=1 if limit == "request" else 10,
            tool_calls_limit=1 if limit == "tool" else 10,
            cost_limit=Decimal("0.001") if limit == "cost" else None,
        ),
    ).invocation
    with pytest.raises((RuntimeError, Exception), match="limit|LLM call"):
        await runtime.run("Retrieve")
    assert len(counter) == 1
    assert runtime.last_trace


@pytest.mark.parametrize("cost_limit", [None, "0.002"])
async def test_nested_research_budgets_include_children_and_reserve_parent(cost_limit):
    from decimal import Decimal

    requests = []

    def retrieve() -> str:
        return "evidence"

    class Report(BaseModel):
        note: str

    def research(request):
        requests.append(("research", list(request.tools_dict)))
        if "retrieve" in request.tools_dict:
            return [call("retrieve")]
        return [answer({"note": "Research complete"})]

    def manager(request):
        requests.append(("navigator", list(request.tools_dict)))
        if len(requests) == 1:
            return [call("research", request="Retrieve evidence")]
        if "retrieve" in request.tools_dict:
            return [call("retrieve")]
        return [answer({"note": "Plan complete"})]

    child = Agent(
        name="research",
        mode="single_turn",
        model=ScriptedModel(model="fixture", respond=research),
        tools=[retrieve],
        output_schema=Report,
        output_key="report",
    )
    parent = Agent(
        name="navigator",
        model=ScriptedModel(model="fixture", respond=manager),
        tools=[retrieve],
        sub_agents=[child],
        output_schema=Report,
        output_key="result",
    )
    runtime = AdkInvocation(
        parent,
        usage_limits=UsageLimits(
            request_limit=12,
            tool_calls_limit=64,
            cost_limit=Decimal(cost_limit) if cost_limit else None,
        ),
    )
    if cost_limit:
        with pytest.raises(RuntimeError, match="cost_limit"):
            await runtime.run("Research and plan")
        assert len(requests) == 2
        return
    assert await runtime.run("Research and plan") == {"note": "Plan complete"}
    assert len(requests) <= 12
    assert requests[-1][0] == "navigator"


@pytest.mark.parametrize("batch_size", [64, 83])
async def test_tool_limit_preserves_structured_output_and_completed_replay(
    tmp_path, batch_size
):
    from pydantic import Field

    executed = []
    requests = []

    def inspect_gene(gene: int) -> str:
        executed.append(gene)
        return "evidence"

    class Report(BaseModel):
        note: str = Field(min_length=1)

    def respond(request):
        requests.append(request)
        if len(requests) == 1:
            return [call("inspect_gene", gene=i) for i in range(batch_size)]
        assert set(request.tools_dict) == {"set_model_response"}
        # Invalid output still receives ADK validation feedback after tools end.
        return [call("set_model_response", note="" if len(requests) == 2 else "Done")]

    agent = Agent(
        name="navigator",
        model=ScriptedModel(model="fixture", respond=respond),
        tools=[inspect_gene],
        output_schema=Report,
        output_key="result",
    )
    runtime = AdkInvocation(
        agent,
        session_path=tmp_path / "agents.sqlite",
        usage_limits=UsageLimits(request_limit=6, tool_calls_limit=64),
    )
    assert await runtime.run("Inspect", request_id="step-0") == {"note": "Done"}
    assert len(executed) == 64
    blocked = [
        event
        for event in runtime.last_trace
        if event["role"] == "tool_return"
        and "tool_calls_limit exhausted" in str(event["content"])
    ]
    assert len(blocked) == batch_size - 64
    assert len(requests) == 3
    assert await runtime.run("Inspect", request_id="step-0") == {"note": "Done"}
    assert len(executed) == 64
    assert len(requests) == 3


async def test_exhausted_tool_budget_allows_child_and_parent_results():
    executed = []
    requests = []

    def retrieve(index: int) -> str:
        executed.append(index)
        return "evidence"

    class Report(BaseModel):
        note: str

    def research(request):
        requests.append("research")
        if "retrieve" in request.tools_dict:
            return [call("retrieve", index=i) for i in range(5)]
        return [call("set_model_response", note="Partial research")]

    def manager(request):
        requests.append("navigator")
        if len(requests) == 1:
            return [call("research", request="Find evidence")]
        assert set(request.tools_dict) == {"set_model_response"}
        return [call("set_model_response", note="Plan from available evidence")]

    child = Agent(
        name="research",
        mode="single_turn",
        model=ScriptedModel(model="fixture", respond=research),
        tools=[retrieve],
        output_schema=Report,
        output_key="report",
    )
    parent = Agent(
        name="navigator",
        model=ScriptedModel(model="fixture", respond=manager),
        sub_agents=[child],
        output_schema=Report,
        output_key="result",
    )
    runtime = AdkInvocation(
        parent, usage_limits=UsageLimits(request_limit=12, tool_calls_limit=3)
    )
    assert await runtime.run("Research") == {"note": "Plan from available evidence"}
    assert len(executed) == 2  # Delegating to the child uses the first task call.
    assert requests == ["navigator", "research", "research", "navigator"]


async def test_native_user_state_is_isolated_between_experiments(tmp_path):
    from google.adk.sessions.sqlite_session_service import SqliteSessionService

    sessions = SqliteSessionService(str(tmp_path / "agents.sqlite"))
    await sessions.create_session(
        app_name="yggdrisil",
        user_id="one",
        session_id="navigator",
        state={"user:plan": "private plan"},
    )
    await sessions.create_session(
        app_name="yggdrisil", user_id="two", session_id="navigator"
    )
    first = await sessions.get_session(
        app_name="yggdrisil", user_id="one", session_id="navigator"
    )
    other = await sessions.get_session(
        app_name="yggdrisil", user_id="two", session_id="navigator"
    )
    assert first.state["user:plan"] == "private plan"
    assert "user:plan" not in other.state
    await sessions.close()


async def test_interrupted_invocation_restores_committed_tool_state(tmp_path):
    import asyncio

    waiting = asyncio.Event()
    release = asyncio.Event()
    submissions = []

    def save_plan(tool_context: ToolContext) -> str:
        tool_context.state["user:plan"] = "Retained after interruption"
        submissions.append(1)
        return "saved"

    class Output(BaseModel):
        note: str

    class PausingModel(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            responses = [
                p.function_response
                for c in llm_request.contents
                for p in c.parts or []
                if p.function_response
            ]
            if not responses:
                parts = [call("save_plan")]
            else:
                waiting.set()
                await release.wait()
                parts = [answer({"note": "Resumed"})]
            yield LlmResponse(
                content=types.Content(role="model", parts=parts),
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=10, candidates_token_count=5
                ),
            )

    def make():
        agent = Agent(
            name="navigator",
            model=PausingModel(model="fixture"),
            tools=[save_plan],
            output_schema=Output,
            output_key="result",
        )
        return AdkInvocation(agent, session_path=tmp_path / "agents.sqlite")

    task = asyncio.create_task(make().run("Explore", request_id="step-0"))
    await asyncio.wait_for(waiting.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    assert await make().run("Explore", request_id="step-0") == {"note": "Resumed"}
    assert submissions == [1]


async def test_parallel_explorers_have_independent_call_budgets():
    import asyncio

    from yggdrisil.agents import ExplorerContext

    async def retrieve() -> str:
        await asyncio.sleep(0.01)
        return "evidence"

    def respond(request):
        responses = [
            p.function_response
            for content in request.contents
            for p in content.parts or []
            if p.function_response
        ]
        return [answer({"actions": ["action"]})] if responses else [call("retrieve")]

    explorer = make_explorer(
        ScriptedModel(model="fixture", respond=respond),
        str,
        tools=[retrieve],
        usage_limits=UsageLimits(request_limit=2, tool_calls_limit=1),
    )
    results = await asyncio.gather(
        *(
            explorer.explore(ExplorerContext(None, state, state, [], None))
            for state in ("one", "two")
        )
    )
    assert all(result.actions == ["action"] for result in results)
    assert all(
        sum(event.get("requests", 0) for event in result.trace) == 2
        for result in results
    )


async def test_resume_rejects_missing_cost_before_another_model_call(tmp_path):
    from decimal import Decimal

    from google.adk.events.event import Event
    from google.adk.sessions.sqlite_session_service import SqliteSessionService

    sessions = SqliteSessionService(str(tmp_path / "agents.sqlite"))
    session = await sessions.create_session(
        app_name="yggdrisil", user_id="search", session_id="explorer"
    )
    await sessions.append_event(
        session,
        Event(
            author="user",
            invocation_id="interrupted",
            content=types.Content(
                role="user", parts=[types.Part(text="REQUEST_ID: step-0\nExplore")]
            ),
        ),
    )
    await sessions.append_event(
        session,
        Event(
            author="explorer",
            invocation_id="interrupted",
            content=types.Content(role="model", parts=[call("retrieve")]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=5
            ),
        ),
    )
    await sessions.close()

    def unexpected(request):
        raise AssertionError("Missing prior billing must stop before a model call")

    invocation = make_explorer(
        ScriptedModel(model="fixture", respond=unexpected),
        str,
        session_path=tmp_path / "agents.sqlite",
        usage_limits=UsageLimits(cost_limit=Decimal("1")),
    ).invocation
    with pytest.raises(RuntimeError, match="Stored provider cost is missing"):
        await invocation.run("Explore", request_id="step-0")


@pytest.mark.parametrize("recover", [True, False])
async def test_resume_preserves_old_errors_but_only_raises_new_errors(
    tmp_path, recover
):
    class Report(BaseModel):
        note: str

    calls = []

    class RecoveringModel(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            calls.append(llm_request)
            if len(calls) == 1 or not recover:
                yield LlmResponse(
                    error_code="CONNECTION_ERROR", error_message=f"attempt {len(calls)}"
                )
            else:
                yield LlmResponse(
                    content=types.Content(
                        role="model", parts=[answer({"note": "recovered"})]
                    )
                )

    def invocation():
        return AdkInvocation(
            Agent(
                name="navigator",
                model=RecoveringModel(model="fixture"),
                output_schema=Report,
                output_key="result",
            ),
            session_path=tmp_path / "agents.sqlite",
        )

    with pytest.raises(RuntimeError, match="attempt 1"):
        await invocation().run("Explore", request_id="same-request")
    resumed = invocation()
    if recover:
        assert await resumed.run("Explore", request_id="same-request") == {
            "note": "recovered"
        }
        assert any(e.get("error_message") == "attempt 1" for e in resumed.last_trace)
        assert await invocation().run("Explore", request_id="same-request") == {
            "note": "recovered"
        }
    else:
        with pytest.raises(RuntimeError, match="attempt 2"):
            await resumed.run("Explore", request_id="same-request")
    assert len(calls) == 2
