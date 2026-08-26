from __future__ import annotations

from pathlib import Path

import pytest

from yggdrisil.cache import ToolCache, cached_tool


@pytest.mark.asyncio
async def test_cached_tool_reuses_results(tmp_path: Path) -> None:
    cache = ToolCache(tmp_path / "tools.sqlite")
    calls = {"n": 0}

    @cached_tool(cache, name="double", version="1")
    async def double(state_id: str, x: int) -> int:
        calls["n"] += 1
        return x * 2

    assert await double("s1", 3) == 6
    assert await double("s1", 3) == 6
    assert calls["n"] == 1
    assert await double("s1", 4) == 8
    assert calls["n"] == 2
    assert await double("s2", 3) == 6
    assert calls["n"] == 3
    cache.close()
