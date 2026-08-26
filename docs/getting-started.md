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

- a **problem** — registered state/action types, `state_key`, `apply`, and optionally
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
from yggdrisil import BestFirstPolicy, RandomPolicy
from policy import llm_policy

RandomPolicy(problem.sample_actions, n_proposals=2, seed=0)
BestFirstPolicy(problem.sample_actions, n_proposals=2, seed=0)
llm_policy("openai:gpt-4o-mini")
```

Persisted dataclasses and Pydantic models must opt in explicitly. This keeps
loading a graph from importing modules named by the database:

```python
from dataclasses import dataclass
from yggdrisil import serializable

@serializable
@dataclass(frozen=True)
class State:
    value: float
```

## Runtime imports

```python
from yggdrisil import (
    BestFirstPolicy,
    Objective,
    RandomPolicy,
    Runner,
    RunLimits,
    SQLiteStateGraph,
)
from yggdrisil.agents import NavigatorExplorerPolicy
```

Everything else — number pools, arithmetic tools, navigator and
explorer roles — is application code.

## Persistence and limits

`SQLiteStateGraph("run.sqlite")` is the durable store. Reopening the
same file resumes the last run. `:memory:` is valid for tests.
Use one file per problem configuration. The runner fingerprints the problem
type, initial state, and public instance configuration and rejects a mismatch.
For configuration objects that are not serializable, expose a
`problem_fingerprint` attribute or zero-argument method returning serializable
identity data.

The tagged storage format was replaced before the 0.1 release. Experimental
graphs written by earlier repository revisions fail with an explicit migration
error; rebuild them rather than loading class names from an untrusted database.

[`RunLimits`][yggdrisil.limits.RunLimits] are hard stops:

- `max_states` — unique nodes, including the initial state
- `max_steps` — policy calls
- `max_wall_time_s` — wall clock

Pass an optional `Objective(score=..., goal_reached=...)` to the runner. Scores
are stored in node metadata, the best state is recorded on `RunResult`, and a
goal can stop the run. `BestFirstPolicy` expands the highest-scored frontier
state. Omit the objective if the policy owns all ranking and stopping logic.

After a run, inspect `node.state` (including any trace you stamped with
`decorate`) and export with `graph.export_json` or `graph.export_graphml`.
The bundled inspector can follow the graph while it runs:

```bash
yggdrisil inspect run.sqlite
```

It opens a local, read-only web view of nodes, transitions, scores, metadata,
and traces. Use `--no-open` on a remote machine.
