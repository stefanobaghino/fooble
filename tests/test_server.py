import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fooble import server
from fooble.extract import extract
from fooble.index import build_index


@pytest.fixture
def mcp(tmp_path: Path, fixtures: Path):
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    for rid in (1001, 1002):
        record = extract((fixtures / f"{rid}.html").read_text(encoding="utf-8"), rid)
        (recipes / f"{rid}.json").write_text(json.dumps(record), encoding="utf-8")
    build_index(tmp_path)
    server.configure(tmp_path)
    return server.mcp


async def call(mcp, tool, **args):
    async with Client(mcp) as client:
        return (await client.call_tool(tool, args)).data


async def test_lists_three_tools(mcp):
    async with Client(mcp) as client:
        assert sorted(t.name for t in await client.list_tools()) == [
            "find_ingredients",
            "get_recipe",
            "search_recipes",
        ]


async def test_find_ingredients(mcp):
    hits = await call(mcp, "find_ingredients", query="Garlic")
    assert hits[0] == {"name": "garlic", "recipes": 1}
    hits = await call(mcp, "find_ingredients", query="zucchini")
    assert hits == [{"name": "courgette", "recipes": 0}]
    assert await call(mcp, "find_ingredients", query="  ") == []
    names = [h["name"] for h in await call(mcp, "find_ingredients", query="pepper")]
    assert names[:2] == ["pepper", "bell pepper"]
    assert names.index("mint") > names.index("chilli"), "prefix-only match ranks last"


async def test_search_by_ingredients(mcp):
    res = await call(mcp, "search_recipes", include=["beef", "paprika"])
    assert res["total"] == 1
    assert res["results"][0]["id"] == 1001
    assert "squash" in res["results"][0]["ingredients"]

    res = await call(mcp, "search_recipes", include=["egg"], exclude=["beef"])
    assert [r["id"] for r in res["results"]] == [1002]

    res = await call(mcp, "search_recipes", exclude=["salt"])
    assert res["total"] == 0


async def test_search_by_text_category_tag_and_limits(mcp):
    assert (await call(mcp, "search_recipes", text="braised squash"))["total"] == 1
    assert (await call(mcp, "search_recipes", text='"drop table"'))["total"] == 0
    assert (await call(mcp, "search_recipes", category="MAIN DISH"))["total"] == 1
    assert (await call(mcp, "search_recipes", tag="autumn"))["total"] == 1
    res = await call(mcp, "search_recipes", max_total_minutes=80)
    assert [r["id"] for r in res["results"]] == [1002]
    assert (await call(mcp, "search_recipes", max_calories=300))["total"] == 1
    res = await call(mcp, "search_recipes", limit=1)
    assert res["total"] == 2 and len(res["results"]) == 1
    assert res["results"][0]["id"] == 1002, "shortest total time first"


async def test_get_recipe(mcp):
    recipe = await call(mcp, "get_recipe", recipe_id=1001)
    assert recipe["title"] == "Braised beef with squash"
    assert recipe["url"].startswith("https://recipes.example/en/recipes/1001/")
    assert recipe["ingredients"][1]["quantity"] == 400.0
    assert "matched" not in recipe["ingredients"][0]
    assert "extractor_version" not in recipe
    with pytest.raises(ToolError, match="no recipe with id 1"):
        await call(mcp, "get_recipe", recipe_id=1)
