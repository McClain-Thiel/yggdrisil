# API reference

Import runtime types from `yggdrisil`. Agent helpers live under
`yggdrisil.agents`. Problems, tools, and domain evaluators are application code;
see [Build a search](tutorial.md).

## Runtime

::: yggdrisil.problem.Problem

::: yggdrisil.policy.Proposal

::: yggdrisil.policy.Decision

::: yggdrisil.policy.PolicyStepError

::: yggdrisil.policy.InterruptedDecisionProvider

::: yggdrisil.policy.Policy

::: yggdrisil.runner.Runner

::: yggdrisil.objective.Objective

::: yggdrisil.limits.RunLimits

::: yggdrisil.limits.RunStatus

::: yggdrisil.types.RunResult

## Evaluation

::: yggdrisil.evaluation.EvaluationResult

::: yggdrisil.evaluation.Evaluator

::: yggdrisil.evaluation.EvaluatorSuite

::: yggdrisil.evaluation.evaluate_cached

::: yggdrisil.evaluation.evaluator_identity

::: yggdrisil.types.EvaluationRecord

## Graph and provenance

::: yggdrisil.graph.sqlite.SQLiteStateGraph
    options:
      inherited_members: true
      members:
        - add_state
        - add_edge
        - add_transition
        - get_state
        - parents
        - children
        - ancestors
        - descendants
        - frontier
        - states
        - edges
        - add_evaluation
        - get_evaluation
        - evaluations
        - decisions
        - proposal_events
        - readonly
        - to_networkx
        - export_json
        - export_graphml
        - save_run
        - get_run
        - latest_run
        - close

::: yggdrisil.graph.base.ReadOnlyStateGraph

::: yggdrisil.types.StateNode

::: yggdrisil.types.Edge

::: yggdrisil.types.DecisionRecord

::: yggdrisil.types.ProposalEvent

## Policies

::: yggdrisil.policies.random.RandomPolicy

::: yggdrisil.policies.best_first.BestFirstPolicy

::: yggdrisil.agents.navigator_explorer.NavigatorExplorerPolicy

::: yggdrisil.agents.navigator_explorer.NavigationPlan

::: yggdrisil.agents.navigator_explorer.ExplorationRequest

::: yggdrisil.agents.navigator_explorer.ExplorationRequestSelector

::: yggdrisil.agents.navigator_explorer.ExplorerResult

## Errors

::: yggdrisil.exceptions.YggdrisilError

::: yggdrisil.exceptions.UnknownStateError

::: yggdrisil.exceptions.CycleError

::: yggdrisil.exceptions.SerializationError
