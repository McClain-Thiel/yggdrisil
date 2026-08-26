# Getting started

Yggdrisil requires **Python 3.11** or newer.

## Install

```bash
pip install yggdrisil
```

Language-model policies need PydanticAI:

```bash
pip install "yggdrisil[agents]"
```

That extra does not change the runner. It only adds adapters to call a
model. Provider credentials (for example `OPENAI_API_KEY`) come from
your environment, not from Yggdrisil.

## What you install

The package is a search runtime: a problem protocol, a policy protocol,
a persistent DAG, and a runner that applies proposals under hard
limits. It does not include a domain. You write:

- a **problem** — states, actions, `state_key`, `apply`, and optionally
  `decorate` so agent traces live on the state object
- **tools** — probes the policy may call; they can fail
- a **policy** — returns `Proposal`s; it must not write the graph

Those three pieces are [built in the next page](tutorial.md). Once they
exist as modules in your project (`problem.py`, `tools.py`,
`policy.py`), a run looks like this:

```python
import asyncio

from yggdrisil import Runner, RunLimits, SQLiteStateGraph

from problem import Make24
from policy import tiny_policy


async def main() -> None:
    problem = Make24()
    graph = SQLiteStateGraph("run.sqlite")
    result = await Runner(
        problem,
        tiny_policy(seed=0, problem=problem),
        graph,
        RunLimits(max_states=40),
    ).run()
    print(result.stop_reason, result.unique_states, result.edges)
    for node in graph.states():
        if problem.solved(node.state):
            print(node.state.values, len(node.state.trace), "tool calls")
    graph.export_json("run.json")
    graph.close()


asyncio.run(main())
```

The same problem and limits work with a random baseline or a real
model. Only the policy object changes:

```python
from yggdrisil import RandomPolicy
from policy import llm_policy

RandomPolicy(problem.sample_actions, n_proposals=2, seed=0)
llm_policy("openai:gpt-4o-mini")
```

## Runtime imports

```python
from yggdrisil import RandomPolicy, Runner, RunLimits, SQLiteStateGraph
from yggdrisil.agents import NavigatorExplorerPolicy
```

Everything else — number pools, arithmetic tools, navigator and
explorer roles — is application code.

## Persistence and limits

`SQLiteStateGraph("run.sqlite")` is the durable store. Reopening the
same file resumes the last run. `:memory:` is valid for tests.

[`RunLimits`][yggdrisil.limits.RunLimits] are hard stops:

- `max_states` — unique nodes, including the initial state
- `max_steps` — policy calls
- `max_wall_time_s` — wall clock

There is no built-in objective. If a state is “better,” put that on the
state (or in node metadata) and let the policy read it.

After a run, inspect `node.state` (including any trace you stamped with
`decorate`) and export with `graph.export_json` or
`graph.export_graphml` if you need a notebook or viewer.
