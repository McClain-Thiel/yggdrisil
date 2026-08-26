# Yggdrisil

A persistent DAG-search runtime: interchangeable policies propose transitions
over user-defined states, and the framework applies, deduplicates, records,
and stops at hard limits.

**Documentation:** [mcclainthiel.github.io/yggdrisil](https://mcclainthiel.github.io/yggdrisil/)

It is a search substrate, not a general agent framework. Domain tools,
evaluators, and scientific ontologies belong in the problem or policy, not in
the core.

## Install

```bash
pip install yggdrisil
pip install "yggdrisil[agents]"   # optional: PydanticAI policies
```

Python 3.11+. Usage, the Make-24 walkthrough, and the API are in the
[documentation](https://mcclainthiel.github.io/yggdrisil/).

## Invariants

1. **Logical identity.** `Problem.state_key` maps logically identical states to
   the same id, so distinct trajectories can converge on one node.
2. **Policies cannot mutate the graph.** They receive a read-only view and
   return `Proposal`s. The runner applies them.
3. **Every edge is reproducible** from parent state + action via `Problem.apply`.
4. **Duplicate states merge.** The search structure is a DAG, not a tree of
   copies.
5. **Agent memory is explicit.** Persistent information lives in the graph (and
   optional tool caches), not in hidden chat history. Explorer traces are
   stamped onto the child state via optional `Problem.decorate`. `state_key`
   must ignore that trace.
6. **Policies are replaceable.** Random search and navigator–explorer agents
   use the same runner.
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
