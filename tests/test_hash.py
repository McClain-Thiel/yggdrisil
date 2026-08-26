from __future__ import annotations

from make24 import Combine, Make24, Pool
from yggdrisil.serialize import dumps, loads, stable_hash


def test_same_pool_same_key() -> None:
    problem = Make24()
    a = Pool.from_ints(1, 3, 4, 6)
    b = Pool.from_ints(6, 4, 3, 1)
    assert problem.state_key(a) == problem.state_key(b)


def test_order_of_construction_does_not_change_hash() -> None:
    assert stable_hash({"b": 1, "a": 2}) == stable_hash({"a": 2, "b": 1})
    assert stable_hash(frozenset([1, 2, 3])) == stable_hash(frozenset([3, 2, 1]))


def test_distinct_types_do_not_collide() -> None:
    assert stable_hash([1, 2]) != stable_hash((1, 2))
    assert stable_hash({1, 2}) != stable_hash(frozenset([1, 2]))


def test_combine_roundtrip() -> None:
    original = Combine("3", "4", "/")
    assert loads(dumps(original)) == original
    pool = Pool.from_ints(1, 3, "3/4", 6)
    assert loads(dumps(pool)) == pool
