# Yggdrisil

A small framework for agentic optimization through persistent tree/DAG search.
Interchangeable policies choose states and propose transitions; the runtime
applies them, merges equivalent states, records decisions and evaluator
evidence, tracks an optional objective, and stops at hard limits.

**Documentation:** [mcclain-thiel.github.io/yggdrisil](https://mcclain-thiel.github.io/yggdrisil/)

Yggdrisil is a search substrate, not a general agent framework. Domain tools,
models, simulators, and scientific ontologies stay in application code.

## Install

```bash
pip install "yggdrisil @ git+https://github.com/McClain-Thiel/yggdrisil.git"
pip install "yggdrisil[agents] @ git+https://github.com/McClain-Thiel/yggdrisil.git"
```

The second command adds the optional PydanticAI adapters. A PyPI release is
planned; the package is currently installed from GitHub. Python 3.11+. Usage,
the Make-24 walkthrough, and the API are in the [documentation](https://mcclain-thiel.github.io/yggdrisil/).

Inspect a completed or running search locally:

```bash
yggdrisil inspect runs/search.sqlite
```

The inspector follows the SQLite graph as it grows. Select a state or edge to
inspect its value, evaluations, linked decisions, tool calls, and proposal
outcomes. It reads tagged JSON directly and does not import application types.

Runs are resumable from SQLite. The runner checkpoints completed steps,
reconciles pending or finalized-but-uncheckpointed proposal events after an
interruption, and never overwrites an existing run when `resume=False`.

## Core model

1. **State is domain data.** `Problem.state_key` maps logically identical
   states to the same id. Scores, prompts, and agent transcripts do not belong
   in the state.
2. **Policies return decisions.** A `Decision` records the context and output of
   one policy operation and contains zero or more `Proposal`s.
3. **The runner owns mutation.** Policies receive a query-only graph. The runner
   validates actions and materializes transitions.
4. **Edges are canonical.** One `(parent, child, action)` edge may be proposed by
   many decisions. `ProposalEvent` records each attempt and whether it created,
   reused, skipped, or failed.
5. **Evaluation is evidence.** Evaluators return named scalar metrics.
   `EvaluatorSuite` is an ordered list of evaluators with per-state caching by
   evaluator name, version, and configuration.
6. **Objectives are run logic.** An optional scalar `Objective` tracks the best
   state and can stop a run without writing scores into state metadata.
7. **Policies are replaceable.** Random search, best-first expansion, and
   navigator–explorer agents use the same runner and graph.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Optional extras: `[agents]` for PydanticAI and `[docs]` to build the site.
