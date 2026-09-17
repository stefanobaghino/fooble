"""MCP server exposing the recipe index to Claude."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from .index import db_path

mcp = FastMCP(
    "fooble",
    instructions=(
        "Search English recipes from fooby.ch by ingredient. Ingredient names are canonical "
        "singular English nouns such as 'onion', 'bell pepper', 'chicken'. When unsure how an "
        "ingredient is named in the index, call find_ingredients first, then search_recipes, "
        "then get_recipe for full details of a chosen recipe."
    ),
)
_db_file: Path | None = None


def _connect() -> sqlite3.Connection:
    assert _db_file is not None, "server not configured"
    con = sqlite3.connect(f"file:{_db_file}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: each word quoted, prefix-matched, AND-ed."""
    words = [w.replace('"', "") for w in text.split()]
    return " ".join(f'"{w}"*' for w in words if w)


@mcp.tool
def find_ingredients(
    query: Annotated[str, Field(description="Free text, e.g. 'pepper' or 'chick'")],
    limit: Annotated[int, Field(ge=1, le=50)] = 15,
) -> list[dict[str, Any]]:
    """Resolve free text to canonical ingredient names used by search_recipes.

    Matches both canonical names and known aliases, case-insensitively, and returns how
    many recipes use each name. Prefer the returned names when calling search_recipes.
    """
    q = query.strip().lower()
    if not q:
        return []
    # Match at word starts only: "pepper" finds "bell pepper" and "peppercorns", not "gingerbread".
    patterns = (q, f"{q}%", f"% {q}%")
    with _connect() as con:
        rows = con.execute(
            """
            WITH hits AS (
                SELECT name FROM alias WHERE alias = ? OR alias LIKE ? OR alias LIKE ?
                UNION SELECT DISTINCT name FROM ingredient
                WHERE name = ? OR name LIKE ? OR name LIKE ?
            )
            SELECT h.name, COUNT(DISTINCT i.recipe_id) AS recipes
            FROM hits h LEFT JOIN ingredient i ON i.name = h.name
            GROUP BY h.name
            """,
            (*patterns, *patterns),
        ).fetchall()

    def rank(row: sqlite3.Row) -> tuple[int, int, int, str]:
        name = row["name"]
        return (name != q, q not in name.split(), -row["recipes"], name)

    rows = sorted(rows, key=rank)[:limit]
    return [{"name": r["name"], "recipes": r["recipes"]} for r in rows]


@mcp.tool
def search_recipes(
    include: Annotated[
        list[str] | None, Field(description="Canonical ingredient names that must all be present")
    ] = None,
    exclude: Annotated[
        list[str] | None, Field(description="Canonical ingredient names that must not be present")
    ] = None,
    text: Annotated[
        str | None, Field(description="Free-text match on title, keywords and ingredients")
    ] = None,
    category: Annotated[
        str | None, Field(description="e.g. 'main dish', 'dessert', 'starter', 'snack'")
    ] = None,
    tag: Annotated[str | None, Field(description="e.g. 'Vegetarian', 'Autumn', 'Quick'")] = None,
    max_total_minutes: Annotated[int | None, Field(ge=0)] = None,
    max_calories: Annotated[float | None, Field(ge=0)] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    """Search recipes by ingredients and other properties.

    All filters combine with AND. Results are compact; call get_recipe for the full record.
    Ingredient filters use canonical names (see find_ingredients); matching is exact.
    """
    where: list[str] = []
    params: list[Any] = []
    for name in include or []:
        where.append("EXISTS (SELECT 1 FROM ingredient i WHERE i.recipe_id = r.id AND i.name = ?)")
        params.append(name.strip().lower())
    for name in exclude or []:
        where.append(
            "NOT EXISTS (SELECT 1 FROM ingredient i WHERE i.recipe_id = r.id AND i.name = ?)"
        )
        params.append(name.strip().lower())
    if text and text.strip():
        where.append("r.id IN (SELECT rowid FROM recipe_fts WHERE recipe_fts MATCH ?)")
        params.append(_fts_query(text))
    if category:
        where.append("r.category = ? COLLATE NOCASE")
        params.append(category.strip())
    if tag:
        where.append(
            "EXISTS (SELECT 1 FROM tag t WHERE t.recipe_id = r.id AND t.tag = ? COLLATE NOCASE)"
        )
        params.append(tag.strip())
    if max_total_minutes is not None:
        where.append("r.total_minutes <= ?")
        params.append(max_total_minutes)
    if max_calories is not None:
        where.append("r.calories <= ?")
        params.append(max_calories)
    sql = "FROM recipe r" + (" WHERE " + " AND ".join(where) if where else "")
    with _connect() as con:
        total = con.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0]
        rows = con.execute(
            f"SELECT r.id, r.title, r.category, r.total_minutes, r.calories {sql} "
            "ORDER BY r.total_minutes IS NULL, r.total_minutes, r.id LIMIT ?",
            [*params, limit],
        ).fetchall()
        ids = [r["id"] for r in rows]
        names: dict[int, list[str]] = {i: [] for i in ids}
        if ids:
            marks = ",".join("?" * len(ids))
            for rec_id, name in con.execute(
                f"SELECT DISTINCT recipe_id, name FROM ingredient WHERE recipe_id IN ({marks}) "
                "ORDER BY recipe_id, position",
                ids,
            ):
                names[rec_id].append(name)
    return {
        "total": total,
        "results": [
            {
                "id": r["id"],
                "title": r["title"],
                "category": r["category"],
                "total_minutes": r["total_minutes"],
                "calories": r["calories"],
                "ingredients": names[r["id"]],
            }
            for r in rows
        ],
    }


@mcp.tool
def get_recipe(
    recipe_id: Annotated[int, Field(description="Recipe id from search_recipes")],
) -> dict[str, Any]:
    """Return the full recipe: url, times, yield, nutrition, ingredients with quantities, steps."""
    with _connect() as con:
        row = con.execute("SELECT json FROM recipe WHERE id = ?", (recipe_id,)).fetchone()
    if row is None:
        raise ValueError(f"no recipe with id {recipe_id}")
    record = json.loads(row["json"])
    record.pop("extractor_version", None)
    for ing in record["ingredients"]:
        ing.pop("matched", None)
    return record


def configure(data_dir: Path) -> None:
    global _db_file
    _db_file = db_path(data_dir).resolve()
    if not _db_file.exists():
        raise FileNotFoundError(f"index not found: {_db_file} (run `fooble index` first)")


def serve(data_dir: Path, stdio: bool = False, host: str = "0.0.0.0", port: int = 8000) -> None:
    configure(data_dir)
    if stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="http", host=host, port=port, path="/mcp")
