# Getting started

Yggdrisil requires **Python 3.11** or newer.

## Install

```bash
pip install "yggdrisil @ git+https://github.com/McClain-Thiel/yggdrisil.git"
```

Language-model policies need the optional PydanticAI adapters:

```bash
pip install "yggdrisil[agents] @ git+https://github.com/McClain-Thiel/yggdrisil.git"
```

The package is not on PyPI yet. Provider credentials come from your
environment, not from Yggdrisil.

## What you provide

The package supplies a runner, SQLite DAG, policy interfaces, evaluation
records, limits, exports, and a local inspector. Application code supplies:

- a **problem** — registered state/action types, `state_key`, and `apply`
- optional **evaluators** — independent measurements of a state
- a **policy** — returns `Decision`s containing proposed transitions
- optional **tools** — probes used by a policy or agent

The complete Make-24 example is [built on the next page](tutorial.md). Once the
problem and policy exist, a run is small:

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
    for decision in graph.decisions(result.run_id):
        if decision.tool_calls:
            print(decision.role, len(decision.tool_calls), "tool calls")

    graph.export_json("run.json")
    graph.close()


asyncio.run(main())
```

The same problem and limits work with several policies. Best-first ranking is
explicit and may use the state, its stored evaluations, or both:

```python
from yggdrisil import BestFirstPolicy, RandomPolicy
from policy import llm_policy

RandomPolicy(problem.sample_actions, n_proposals=2, seed=0)

BestFirstPolicy(
    problem.sample_actions,
    lambda node, evaluations: -problem.distance(node.state),
    n_proposals=2,
    seed=0,
    eligible=lambda node, evaluations: bool(evaluations),
)

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

## Evaluations

An evaluator returns metrics plus optional structured metadata. A suite keeps
the evaluators in a stable order and runs them sequentially by default.

```python
from yggdrisil import EvaluationResult, EvaluatorSuite


class Distance:
    name = "distance"
    version = "1"
    config = {"target": 24}
    cost = 1.0

    async def evaluate(self, state):
        return EvaluationResult(metrics={"distance": problem.distance(state)})


records = await EvaluatorSuite([Distance()]).evaluate_cached(graph, state_id)
print(records[0].metrics["distance"])
```

For search-time evidence, pass the suite to the runner. Every state is evaluated
before a policy can observe it. Restarts reuse cached records and backfill a new
evaluator version or configuration across restored states:

```python
suite = EvaluatorSuite([Distance()], concurrent=True)
result = await Runner(
    problem,
    policy,
    graph,
    RunLimits(max_states=40),
    evaluators=suite,
).run()
```

Use `concurrent=True` only when evaluators are independent. Results retain the
suite's declared order. Evaluation time is covered by `max_wall_time_s`.

An evaluator may declare a non-negative scalar `cost`; the default is one unit.
The units are application-defined, so they can represent oracle calls, money,
or a calibrated heuristic. `max_evaluation_cost` stops the runner before a
state's missing evaluator suite would exceed the budget. Cached records cost
zero, and a suite starts only when all of its missing evaluations fit:

```python
result = await Runner(
    problem,
    policy,
    graph,
    RunLimits(max_evaluation_cost=1_000),
    evaluators=suite,
).run()
print(result.evaluation_cost)
```

The cache key is `(state_id, evaluator name, version, config)`. Change a version
or configuration to produce a distinct evaluation record.

## Persistence and limits

`SQLiteStateGraph("run.sqlite")` is the durable store. Reopening the same file
resumes the requested or latest run. `:memory:` is useful for tests. Use one
file per problem configuration; the runner fingerprints the problem and
rejects a mismatch before mutation.

The SQLite database is the resume checkpoint. States, edges, decisions, and
proposal events are committed as they are written; evaluation records are
cached independently. The run step and metadata are checkpointed after every
completed proposal batch. On reopen, the runner first backfills configured
evaluations and then reconciles an interrupted batch:

- a pending proposal is applied again safely against the deduplicated DAG
- proposal order and edge metadata are preserved during replay
- a finalized batch whose step was not saved advances the checkpoint
- an identical decision that previously failed is recorded as a new attempt
- stored run metadata is preserved and merged with new metadata

Pass `run_id="..."` to resume a particular run. With no id, `resume=True`
selects the latest run in the file. Use `resume=False` for a new run; an
existing explicit id is rejected rather than overwritten.

Custom policy object state is not serialized. Resumable policies should derive
their context from the graph and `RunStatus`, as the agent policies do. Seeded
`RandomPolicy` and `BestFirstPolicy` derive randomness from the saved step, so
reopening them with the same seed matches an uninterrupted run.

[`RunLimits`][yggdrisil.limits.RunLimits] are hard stops:

- `max_states` — unique nodes, including the initial state
- `max_steps` — policy calls
- `max_wall_time_s` — wall clock
- `max_evaluation_cost` — application-defined evaluator cost units

An optional `Objective(score=..., goal_reached=...)` tracks `best_state_id` and
`best_score` on the run and may stop it. Objective scores are run logic, so they
are not copied into states or evaluation records.

## Inspect while running

```bash
yggdrisil inspect run.sqlite
```

The local read-only inspector follows new rows while the run is active. It
shows the DAG, state/action payloads, evaluations, decisions, tool calls, and
proposal outcomes. Use `--no-open` on a remote machine. The inspector has no
authentication; keep the default loopback binding or put it behind an SSH
tunnel rather than exposing it directly.
