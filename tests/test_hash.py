from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from make24 import Combine, Make24, Pool

from yggdrisil.exceptions import SerializationError
from yggdrisil.serialize import dumps, loads, serializable, stable_hash


@serializable
@dataclass(frozen=True)
class ExampleValue:
    label: str


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
    assert stable_hash({1: "value"}) != stable_hash({"1": "value"})


def test_combine_roundtrip() -> None:
    original = Combine("3", "4", "/")
    assert loads(dumps(original)) == original
    pool = Pool.from_ints(1, 3, "3/4", 6)
    assert loads(dumps(pool)) == pool


def test_reserved_looking_dictionary_roundtrips() -> None:
    value = {"__type__": "pathlib:Path", "__yggdrisil__": "object"}
    assert loads(dumps(value)) == value


def test_registered_value_roundtrips() -> None:
    assert loads(dumps(ExampleValue("safe"))) == ExampleValue("safe")


def test_loader_does_not_import_unregistered_types() -> None:
    payload = {
        "__yggdrisil__": "object",
        "kind": "dataclass",
        "qualname": "pathlib:Path",
        "fields": [["value", "unexpected"]],
    }
    with pytest.raises(SerializationError, match="unregistered"):
        loads(json.dumps(payload))


def test_legacy_object_encoding_fails_with_migration_message() -> None:
    legacy = {"__type__": "make24.problem:Pool", "values": ["1", "3"]}
    with pytest.raises(SerializationError, match="legacy serialized value"):
        loads(json.dumps(legacy))


def test_unregistered_dataclass_is_rejected() -> None:
    @dataclass
    class Unregistered:
        value: int

    with pytest.raises(SerializationError, match="not registered"):
        dumps(Unregistered(1))
