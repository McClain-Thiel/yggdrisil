# Yggdrisil

A small framework for agentic optimization through persistent tree/DAG search.
Interchangeable policies propose transitions over user-defined states; the
runtime applies them, deduplicates equivalent states, records traces, tracks an
optional objective, and stops at hard limits.

**Documentation:** [mcclain-thiel.github.io/yggdrisil](https://mcclain-thiel.github.io/yggdrisil/)

It is a search substrate, not a general agent framework. Domain tools,
evaluators, and scientific ontologies belong in the problem or policy, not in
the core.

## Install

```bash
pip install yggdrisil
pip install "yggdrisil[agents]"   # optional: PydanticAI policies
```

Python 3.11+. Usage, the Make-24 walkthrough, and the API are in the
[documentation](https://mcclain-thiel.github.io/yggdrisil/).

Inspect a completed or running search locally:

```bash
yggdrisil inspect runs/search.sqlite
```

The inspector follows new SQLite rows and shows the DAG, node values, actions,
metadata, and agent traces. It does not import or reconstruct application
classes.

## Invariants

1. **Logical identity.** `Problem.state_key` maps logically identical states to
   the same id, so distinct trajectories can converge on one node.
2. **The runner owns graph mutation.** Policies receive a query-only interface
   and return `Proposal`s. This is an API boundary, not a security sandbox.
3. **Every edge is reproducible** from parent state + action via `Problem.apply`.
4. **Duplicate states merge.** The search structure is a DAG, not a tree of
   copies.
5. **Agent memory is explicit.** Persistent information lives in the graph (and
   optional tool caches), not in hidden chat history. Explorer traces are
   stamped onto the child state via optional `Problem.decorate`. `state_key`
   must ignore that trace.
6. **Policies are replaceable.** Random search, best-first search, and
   navigator–explorer agents use the same runner.
7. **The core does not understand domain tools.** KEGG, FBA, simulators, and
   literature search belong to the experiment.

## Development

```bash
pip install -e ".[dev]"
pytest
```

Optional extras: `[agents]` for PydanticAI, `[docs]` to build the site.
The first scientific experiment (minimal *E. coli*) should live in a
separate package so this repository stays domain-free.
