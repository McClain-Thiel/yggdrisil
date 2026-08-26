from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any

from yggdrisil.exceptions import SerializationError

_TYPE = "__type__"


def stable_hash(data: Any) -> str:
    """SHA-256 of a canonical encoding. Same logical value → same digest."""
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def canonical_json(data: Any) -> str:
    return json.dumps(
        to_tagged(data),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def dumps(data: Any) -> str:
    return canonical_json(data)


def loads(text: str) -> Any:
    return from_tagged(json.loads(text))


def to_tagged(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return {_TYPE: "float", "value": str(obj)}
        return obj
    if isinstance(obj, datetime):
        return {_TYPE: "datetime", "iso": obj.isoformat()}
    if isinstance(obj, frozenset):
        return {_TYPE: "frozenset", "items": _sorted_tagged(obj)}
    if isinstance(obj, set):
        return {_TYPE: "set", "items": _sorted_tagged(obj)}
    if isinstance(obj, tuple):
        return {_TYPE: "tuple", "items": [to_tagged(x) for x in obj]}
    if isinstance(obj, list):
        return [to_tagged(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): to_tagged(v) for k, v in obj.items()}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            _TYPE: "dataclass",
            "qualname": _qualname(type(obj)),
            "fields": {f.name: to_tagged(getattr(obj, f.name)) for f in fields(obj)},
        }
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return {
            _TYPE: "pydantic",
            "qualname": _qualname(type(obj)),
            "fields": to_tagged(dump(mode="python")),
        }
    raise SerializationError(f"cannot serialize {type(obj).__name__}")


def from_tagged(obj: Any) -> Any:
    if isinstance(obj, list):
        return [from_tagged(x) for x in obj]
    if not isinstance(obj, dict) or _TYPE not in obj:
        if isinstance(obj, dict):
            return {k: from_tagged(v) for k, v in obj.items()}
        return obj
    tag = obj[_TYPE]
    if tag == "float":
        return float(obj["value"])
    if tag == "datetime":
        return datetime.fromisoformat(obj["iso"])
    if tag == "frozenset":
        return frozenset(from_tagged(x) for x in obj["items"])
    if tag == "set":
        return set(from_tagged(x) for x in obj["items"])
    if tag == "tuple":
        return tuple(from_tagged(x) for x in obj["items"])
    if tag in ("dataclass", "pydantic"):
        cls = _load_qualname(obj["qualname"])
        values = from_tagged(obj["fields"])
        return cls(**values)
    raise SerializationError(f"unknown type tag: {tag}")


def _sorted_tagged(items: Any) -> list[Any]:
    tagged = [to_tagged(x) for x in items]
    tagged.sort(key=lambda v: json.dumps(v, sort_keys=True, separators=(",", ":")))
    return tagged


def _qualname(cls: type) -> str:
    return f"{cls.__module__}:{cls.__qualname__}"


def _load_qualname(qualname: str) -> type:
    module_name, _, rest = qualname.partition(":")
    obj: Any = importlib.import_module(module_name)
    for part in rest.split("."):
        obj = getattr(obj, part)
    return obj
