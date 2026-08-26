# API reference

Import runtime types from `yggdrisil`. Agent helpers live under
`yggdrisil.agents`. Problems and tools are application code; see
[Build a search](tutorial.md).

## Runtime

::: yggdrisil.problem.Problem

::: yggdrisil.policy.Proposal

::: yggdrisil.policy.Policy

::: yggdrisil.runner.Runner

::: yggdrisil.limits.RunLimits

::: yggdrisil.limits.RunStatus

::: yggdrisil.types.RunResult

## Graph

::: yggdrisil.graph.sqlite.SQLiteStateGraph
    options:
      inherited_members: true
      members:
        - add_state
        - add_edge
        - get_state
        - parents
        - children
        - ancestors
        - descendants
        - frontier
        - states
        - edges
        - readonly
        - to_networkx
        - export_json
        - export_graphml
        - save_run
        - latest_run
        - close

::: yggdrisil.graph.base.ReadOnlyStateGraph

::: yggdrisil.types.StateNode

::: yggdrisil.types.Edge

## Policies

::: yggdrisil.policies.random.RandomPolicy

::: yggdrisil.agents.navigator_explorer.NavigatorExplorerPolicy

::: yggdrisil.agents.navigator_explorer.NavigationPlan

::: yggdrisil.agents.navigator_explorer.ExplorationRequest

::: yggdrisil.agents.navigator_explorer.ExplorerResult

## Errors

::: yggdrisil.exceptions.YggdrisilError

::: yggdrisil.exceptions.UnknownStateError

::: yggdrisil.exceptions.CycleError
