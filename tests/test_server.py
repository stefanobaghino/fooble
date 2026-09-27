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


async def test_lists_tools(mcp):
    async with Client(mcp) as client:
        assert sorted(t.name for t in await client.list_tools()) == [
            "find_ingredients",
            "get_recipe",
            "list_tags",
            "search_recipes",
        ]


async def test_list_tags(mcp):
    tags = await call(mcp, "list_tags")
    assert {"tag": "autumn", "recipes": 1} in tags
    assert all(t["tag"] == t["tag"].lower() for t in tags)
    counts = [t["recipes"] for t in tags]
    assert counts == sorted(counts, reverse=True)


async def test_find_ingredients(mcp):
    hits = (await call(mcp, "find_ingredients", query="Garlic"))["ingredients"]
    assert hits[0] == {"name": "garlic", "recipes": 1}
    res = await call(mcp, "find_ingredients", query="zucchini")
    assert res == {
        "total": 1,
        "next_offset": None,
        "ingredients": [{"name": "courgette", "recipes": 0}],
    }
    empty = await call(mcp, "find_ingredients", query="  ")
    assert empty == {"total": 0, "next_offset": None, "ingredients": []}
    res = await call(mcp, "find_ingredients", query="pepper")
    names = [h["name"] for h in res["ingredients"]]
    assert names[:2] == ["pepper", "bell pepper"]
    assert names.index("mint") > names.index("chilli"), "prefix-only match ranks last"


async def test_find_ingredients_paging(mcp):
    full = await call(mcp, "find_ingredients", query="pepper")
    names = [h["name"] for h in full["ingredients"]]
    assert len(names) >= 3 and full["next_offset"] is None
    first = await call(mcp, "find_ingredients", query="pepper", limit=2)
    assert first["total"] == full["total"] and first["next_offset"] == 2
    second = await call(mcp, "find_ingredients", query="pepper", limit=2, offset=2)
    assert [h["name"] for h in first["ingredients"] + second["ingredients"]] == names[:4]


async def test_search_by_ingredients(mcp):
    res = await call(mcp, "search_recipes", include=["beef", "paprika"])
    assert res["total"] == 1
    assert res["results"][0]["id"] == 1001
    assert "squash" in res["results"][0]["ingredients"]

    res = await call(mcp, "search_recipes", include=["egg"], exclude=["beef"])
    assert [r["id"] for r in res["results"]] == [1002]

    res = await call(mcp, "search_recipes", exclude=["salt"])
    assert res["total"] == 0


async def test_search_include_matches_by_containment(mcp):
    res = await call(mcp, "search_recipes", include=["cream"])
    assert [r["id"] for r in res["results"]] == [1001]
    assert res["include_matches"] == {"cream": ["sour cream"]}

    res = await call(mcp, "search_recipes", include=["Eggs "])
    assert [r["id"] for r in res["results"]] == [1002], "plural stems to the singular name"
    assert res["include_matches"] == {"eggs": ["egg"]}

    res = await call(mcp, "search_recipes", include=["olive oil"])
    assert res["total"] == 1 and res["include_matches"] == {"olive oil": ["olive oil"]}
    assert (await call(mcp, "search_recipes", include=["oil olive"]))["total"] == 0

    res = await call(mcp, "search_recipes", include=["zucchini"])
    assert res["total"] == 0 and res["include_matches"] == {"zucchini": []}

    res = await call(mcp, "search_recipes", include=["oil"], exclude=["sour cream"])
    assert [r["id"] for r in res["results"]] == [1002]
    assert (await call(mcp, "search_recipes", exclude=["cream"]))["total"] == 2, "exclude is exact"


def test_denotes():
    assert server._denotes("cream", "sour cream")
    assert server._denotes("cream", "cream")
    assert not server._denotes("cream", "ice cream")
    assert not server._denotes("chicken", "vegan chicken substitute")
    assert not server._denotes("wine", "wine vinegar")
    assert not server._denotes("pepper", "bell pepper")
    assert server._denotes("bell pepper", "bell pepper")


async def test_search_by_text_category_tag_and_limits(mcp):
    assert (await call(mcp, "search_recipes", text="braised squash"))["total"] == 1
    assert (await call(mcp, "search_recipes", text='"drop table"'))["total"] == 0
    assert (await call(mcp, "search_recipes", category="MAIN DISH"))["total"] == 1
    assert (await call(mcp, "search_recipes", tag="autumn"))["total"] == 1
    assert (await call(mcp, "search_recipes", tag="Soups and Stew"))["total"] == 1
    assert (await call(mcp, "search_recipes", category="main dish", tag="autumn"))["total"] == 1
    assert (await call(mcp, "search_recipes", category="main dish", tag="nope"))["total"] == 0
    res = await call(mcp, "search_recipes", max_total_minutes=80)
    assert [r["id"] for r in res["results"]] == [1002]
    assert (await call(mcp, "search_recipes", max_calories=300))["total"] == 1
    res = await call(mcp, "search_recipes", limit=1)
    assert res["total"] == 2 and len(res["results"]) == 1
    assert res["results"][0]["id"] == 1002, "shortest total time first"


async def test_search_nutrition_filters_and_sort(mcp):
    async def ids(**args):
        return [r["id"] for r in (await call(mcp, "search_recipes", **args))["results"]]

    assert await ids(max_prep_minutes=30) == [1001]
    assert await ids(max_total_minutes=80) == [1002], "prep and total time differ"
    assert await ids(min_protein_g=10) == [1001]
    assert await ids(max_fat_g=10) == [1002]
    assert await ids(max_carbohydrate_g=33) == [1002]
    assert await ids(min_protein_g=10, max_fat_g=10) == []
    assert await ids() == [1002, 1001]
    assert await ids(sort="prep_time") == [1001, 1002]
    assert await ids(sort="calories") == [1002, 1001]
    assert await ids(sort="protein") == [1001, 1002]
    assert await ids(sort="newest") == [1002, 1001]
    with pytest.raises(ToolError):
        await call(mcp, "search_recipes", sort="random")
    first = (await call(mcp, "search_recipes", sort="protein", limit=1))["results"][0]
    assert first == {
        "id": 1001,
        "title": "Braised beef with squash",
        "category": "main dish",
        "total_minutes": 90,
        "prep_minutes": 20,
        "serving_size": "4 person",
        "calories": 321.0,
        "protein_g": 15.0,
        "fat_g": 12.0,
        "carbohydrate_g": 34.0,
        "ingredients": first["ingredients"],
    }


async def test_search_paging(mcp):
    first = await call(mcp, "search_recipes", limit=1)
    assert first["next_offset"] == 1
    second = await call(mcp, "search_recipes", limit=1, offset=1)
    assert [r["id"] for r in second["results"]] == [1001]
    assert second["total"] == 2 and second["next_offset"] is None
    past = await call(mcp, "search_recipes", limit=1, offset=5)
    assert past["results"] == [] and past["total"] == 2 and past["next_offset"] is None
    none = await call(mcp, "search_recipes", include=["zucchini"])
    assert none["total"] == 0 and none["next_offset"] is None


async def test_get_recipe(mcp):
    recipe = await call(mcp, "get_recipe", recipe_id=1001)
    assert recipe["title"] == "Braised beef with squash"
    assert recipe["url"].startswith("https://recipes.example/en/recipes/1001/")
    assert recipe["ingredients"][1]["quantity"] == 400.0
    assert "matched" not in recipe["ingredients"][0]
    assert "extractor_version" not in recipe
    with pytest.raises(ToolError, match="no recipe with id 1"):
        await call(mcp, "get_recipe", recipe_id=1)
