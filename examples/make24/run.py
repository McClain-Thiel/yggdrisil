"""Search Make 24 with a random policy or the tiny offline tool-using LM."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(EXAMPLES))

from make24.policy import llm_policy, tiny_policy  # noqa: E402
from make24.problem import Make24, render_pool  # noqa: E402
from yggdrisil import (  # noqa: E402
    BestFirstPolicy,
    Objective,
    RandomPolicy,
    RunLimits,
    Runner,
    SQLiteStateGraph,
)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        choices=("random", "best-first", "tiny", "llm"),
        default="tiny",
    )
    parser.add_argument("--model", default="openai:gpt-4o-mini")
    parser.add_argument("--max-states", type=int, default=40)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("runs/make24/graph.sqlite"))
    args = parser.parse_args()

    problem = Make24()
    if args.policy == "random":
        policy = RandomPolicy(problem.sample_actions, n_proposals=2, seed=args.seed)
    elif args.policy == "best-first":
        policy = BestFirstPolicy(
            lambda state, _rng: problem.legal_actions(state),
            lambda node, _evaluations: (
                -problem.distance(node.state) - len(node.state.values) + 1
            ),
            n_proposals=2,
            seed=args.seed,
        )
    elif args.policy == "tiny":
        policy = tiny_policy(seed=args.seed, problem=problem)
    else:
        policy = llm_policy(args.model, problem=problem)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    graph = SQLiteStateGraph(args.out)
    objective = Objective(
        score=lambda state: -problem.distance(state) - len(state.values) + 1,
        goal_reached=problem.solved,
    )
    result = await Runner(
        problem,
        policy,
        graph,
        RunLimits(max_states=args.max_states),
        objective=objective,
    ).run()

    hits = [n for n in graph.states() if problem.solved(n.state)]
    print(
        f"{result.stop_reason}: {result.unique_states} states, "
        f"{result.edges} edges, {result.step} steps"
    )
    print(f"solutions found: {len(hits)}")
    if hits:
        print(render_pool(hits[0].state, target=problem.target))
        events = graph.proposal_events(state_id=hits[0].state_id)
        decision_ids = {event.decision_id for event in events}
        tool_calls = sum(
            len(decision.tool_calls)
            for decision in graph.decisions(result.run_id)
            if decision.decision_id in decision_ids
        )
        print(f"tool calls in linked decisions: {tool_calls}")
    json_path = args.out.with_suffix(".json")
    graph.export_json(json_path)
    print(f"wrote {args.out} and {json_path}")
    graph.close()


if __name__ == "__main__":
    asyncio.run(main())
