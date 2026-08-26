---
hide:
  - toc
---

# Yggdrisil

A persistent DAG-search runtime. Policies propose transitions over
user-defined states; the framework applies, deduplicates, records, and
stops at hard limits.

Yggdrisil is a **search substrate**, not a general agent framework. You
write the problem, the tools, and the policy. Scientific ontologies,
simulators, and evaluators stay in that application code.

<div class="grid cards" markdown>

-   :fontawesome-solid-diagram-project: __Persistent DAG__

    ---

    The same logical state is stored once. Two trajectories that collide
    on [`state_key`][yggdrisil.problem.Problem] become one node with two
    parents.

-   :fontawesome-solid-shuffle: __Replaceable policies__

    ---

    Random search, a tool-using stand-in, or a language model. Same
    [`Runner`][yggdrisil.runner.Runner], same graph, same limits.

-   :fontawesome-solid-lock: __Policies cannot write__

    ---

    A policy sees a
    [`ReadOnlyStateGraph`][yggdrisil.graph.base.ReadOnlyStateGraph] and
    returns [`Proposal`][yggdrisil.policy.Proposal]s. The runner applies
    them. Agent traces belong on the **state**, not in chat history.

</div>

```mermaid
flowchart TD
  P["Problem<br/>state + action semantics"] --> Pol["Policy<br/>proposals only"]
  Pol --> R["Runner<br/>validate / apply / decorate / store"]
  R --> G["StateGraph<br/>persistent DAG"]
```

The worked example composes `1, 3, 4, 6` with arithmetic until the
value 24. Next steps are not a menu of legal moves: the policy calls
tools, and the transcript is stamped onto `node.state`.

[Install](getting-started.md){ .md-button .md-button--primary }
[Build a search](tutorial.md){ .md-button }
[Architecture](architecture.md){ .md-button }
