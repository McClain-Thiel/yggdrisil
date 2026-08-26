from __future__ import annotations

from make24 import ArithmeticTools, Combine, Make24, Pool, apply_combine


def test_classic_solution_uses_all_numbers() -> None:
    problem = Make24()
    state = problem.initial_state
    for action in (
        Combine("3", "4", "/"),
        Combine("1", "3/4", "-"),
        Combine("6", "1/4", "/"),
    ):
        problem.validate_action(state, action)
        state = problem.apply(state, action)
    assert problem.solved(state)
    assert state.values == ("24",)
    assert problem.legal_actions(state) == []


def test_missing_operand_is_illegal() -> None:
    problem = Make24()
    try:
        problem.validate_action(problem.initial_state, Combine("1", "1", "+"))
    except ValueError as exc:
        assert "pool" in str(exc)
    else:
        raise AssertionError("expected missing-operand error")


def test_divide_by_zero_is_illegal() -> None:
    state = Pool.from_ints(1, 0)
    try:
        apply_combine(state, Combine("1", "0", "/"))
    except ValueError as exc:
        assert "zero" in str(exc)
    else:
        raise AssertionError("expected division-by-zero error")


def test_pool_identity_ignores_construction_order() -> None:
    problem = Make24()
    assert problem.state_key(Pool.from_ints(6, 1, 4, 3)) == problem.state_key(
        problem.initial_state
    )


def test_state_key_ignores_trace() -> None:
    problem = Make24()
    bare = Pool.from_ints(1, 3, 4, 6)
    stamped = problem.decorate(bare, {"trace": [{"tool": "add", "a": "1", "b": "3"}]})
    assert stamped.trace[0]["tool"] == "add"
    assert problem.state_key(bare) == problem.state_key(stamped)


def test_arithmetic_tools_record_success_and_failure() -> None:
    kit = ArithmeticTools(Pool.from_ints(1, 3, 4, 6))
    kit.add("1", "3")
    kit.divide("4", "0")
    kit.multiply("9", "1")
    assert kit.trace[0]["ok"] is True
    assert kit.trace[0]["result"] == "4"
    assert kit.trace[0]["remaining"] == ["4", "4", "6"]
    assert kit.trace[1]["ok"] is False
    assert kit.trace[2]["ok"] is False
    assert "pool" in kit.trace[2]["error"]
