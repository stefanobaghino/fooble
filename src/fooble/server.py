"""MCP server exposing the recipe index to Claude."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from pydantic import Field

from .index import db_path

mcp = FastMCP(
    "fooble",
    instructions=(
        "Search English recipes from fooby.ch by ingredient. Ingredient names are canonical "
        "singular English nouns such as 'onion', 'bell pepper', 'chicken'. When unsure how an "
        "ingredient is named in the index, call find_ingredients first, then search_recipes, "
        "then get_recipe for full details of a chosen recipe. Call list_tags before filtering "
        "by tag: tags are a fixed vocabulary such as 'main dish' or 'quick recipes'. For "
        "'what can I make with what I have', call search_by_pantry instead of search_recipes."
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


_OFFSET_DESCRIPTION = "Results to skip, for paging; pass next_offset from the previous page"


def _next_offset(offset: int, returned: int, total: int) -> int | None:
    """The offset of the following page, or None when this page reaches the end."""
    end = offset + returned
    return end if returned and end < total else None


# Each sort has a fixed direction; unknown values go last and ties break by id, so pages are
# stable under any sort.
_ORDER_BY = {
    "total_time": "r.total_minutes IS NULL, r.total_minutes",
    "prep_time": "r.prep_minutes IS NULL, r.prep_minutes",
    "calories": "r.calories IS NULL, r.calories",
    "protein": "r.protein_g IS NULL, r.protein_g DESC",
    "newest": "r.published IS NULL, r.published DESC",
}
Sort = Literal["total_time", "prep_time", "calories", "protein", "newest"]
_PER_SERVING = "per serving; see serving_size in results"
_RESULT_COLUMNS = (
    "id",
    "title",
    "category",
    "total_minutes",
    "prep_minutes",
    "serving_size",
    "calories",
    "protein_g",
    "fat_g",
    "carbohydrate_g",
)

# Parameters shared by search_recipes and search_by_pantry.
Exclude = Annotated[
    list[str] | None, Field(description="Canonical ingredient names that must not be present")
]
Tag = Annotated[
    str | None,
    Field(description="One tag from list_tags, e.g. 'main dish', 'vegetarian', 'autumn'"),
]
MaxTotalMinutes = Annotated[
    int | None, Field(ge=0, description="Including resting, marinating and baking")
]
MaxPrepMinutes = Annotated[int | None, Field(ge=0, description="Hands-on time only")]
MaxCalories = Annotated[float | None, Field(ge=0, description=f"kcal {_PER_SERVING}")]
Grams = Annotated[float | None, Field(ge=0, description=f"Grams {_PER_SERVING}")]
Limit = Annotated[int, Field(ge=1, le=100)]
Offset = Annotated[int, Field(ge=0, description=_OFFSET_DESCRIPTION)]


def _shared_filters(
    *,
    exclude: list[str] | None,
    tags: tuple[str | None, ...],
    max_total_minutes: int | None,
    max_prep_minutes: int | None,
    max_calories: float | None,
    min_protein_g: float | None,
    max_fat_g: float | None,
    max_carbohydrate_g: float | None,
) -> tuple[list[str], list[Any]]:
    """WHERE conditions on recipe `r` for the filters both search tools accept."""
    where: list[str] = []
    params: list[Any] = []
    for name in exclude or []:
        where.append("r.id NOT IN (SELECT recipe_id FROM ingredient WHERE name = ?)")
        params.append(name.strip().lower())
    for label in {t.strip().lower() for t in tags if t and t.strip()}:
        where.append("r.id IN (SELECT recipe_id FROM tag WHERE tag = ?)")
        params.append(label)
    for column, op, value in (
        ("total_minutes", "<=", max_total_minutes),
        ("prep_minutes", "<=", max_prep_minutes),
        ("calories", "<=", max_calories),
        ("protein_g", ">=", min_protein_g),
        ("fat_g", "<=", max_fat_g),
        ("carbohydrate_g", "<=", max_carbohydrate_g),
    ):
        if value is not None:
            where.append(f"r.{column} {op} ?")
            params.append(value)
    return where, params


def _page(
    con: sqlite3.Connection,
    sql: str,
    params: list[Any],
    order_by: str,
    limit: int,
    offset: int,
    *,
    prefix: str = "",
    extra: str = "",
) -> tuple[list[sqlite3.Row], int]:
    """One page of `sql` (a FROM clause over recipe `r`) and the total number of matches.

    `prefix` is a WITH clause and `extra` more result columns; `params` covers both.
    """
    columns = ", ".join("r." + c for c in _RESULT_COLUMNS)
    # The window count rides along with the page, saving a second pass over the matches.
    rows = con.execute(
        f"{prefix} SELECT {columns}{extra}, COUNT(*) OVER () AS total {sql} "
        f"ORDER BY {order_by}, r.id LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    if rows:
        return rows, rows[0]["total"]
    # An empty page carries no count: nothing matched, or offset is past the end.
    return rows, con.execute(f"{prefix} SELECT COUNT(*) {sql}", params).fetchone()[0]


# Names containing any of these words are stand-ins, not the ingredient they name:
# "vegan chicken substitute" is not chicken, "wine vinegar" is not wine.
_STAND_IN_WORDS = frozenset({"substitute", "stabilizer", "stabiliser", "vinegar"})
# Compound names that contain a term without being that ingredient.
_NOT_CONTAINED: dict[str, frozenset[str]] = {
    "cream": frozenset({"ice cream", "cream cheese"}),
    "butter": frozenset({"peanut butter"}),
    "pepper": frozenset({"bell pepper"}),
}


def _denotes(term: str, name: str) -> bool:
    """Whether canonical ingredient `name`, which contains `term`, is a kind of `term`."""
    if name == term:
        return True
    if _STAND_IN_WORDS & set(name.split()):
        return False
    return name not in _NOT_CONTAINED.get(term, ())


def _contained_names(con: sqlite3.Connection, term: str) -> list[str]:
    """Canonical ingredient names that contain `term` (or its canonical alias) as whole words.

    Words are stemmed, so "onions" finds "onion" and "red onion"; a multi-word term matches as a
    phrase, so "red onion" does not find "onion".
    """
    phrases = {term}
    row = con.execute("SELECT name FROM alias WHERE alias = ?", (term,)).fetchone()
    if row:
        phrases.add(row["name"])
    match = " OR ".join(f'"{p.replace(chr(34), "")}"' for p in sorted(phrases))
    rows = con.execute(
        "SELECT name FROM ingredient_name_fts WHERE ingredient_name_fts MATCH ?", (match,)
    )
    return sorted(r["name"] for r in rows if _denotes(term, r["name"]))


@mcp.tool
def find_ingredients(
    query: Annotated[str, Field(description="Free text, e.g. 'pepper' or 'chick'")],
    limit: Annotated[int, Field(ge=1, le=50)] = 15,
    offset: Annotated[int, Field(ge=0, description=_OFFSET_DESCRIPTION)] = 0,
) -> dict[str, Any]:
    """Resolve free text to canonical ingredient names used by search_recipes.

    Matches both canonical names and known aliases, case-insensitively, and returns how
    many recipes use each name, best matches first. Prefer the returned names when calling
    search_recipes. `next_offset` is set when more matches follow.
    """
    q = query.strip().lower()
    if not q:
        return {"total": 0, "next_offset": None, "ingredients": []}
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

    page = sorted(rows, key=rank)[offset : offset + limit]
    return {
        "total": len(rows),
        "next_offset": _next_offset(offset, len(page), len(rows)),
        "ingredients": [{"name": r["name"], "recipes": r["recipes"]} for r in page],
    }


@mcp.tool
def search_recipes(
    include: Annotated[
        list[str] | None,
        Field(
            description="Ingredients that must all be present. Each matches any ingredient name "
            "containing it as whole words: 'onion' also finds 'red onion' and 'spring onion'."
        ),
    ] = None,
    exclude: Exclude = None,
    text: Annotated[
        str | None, Field(description="Free-text match on title, keywords and ingredients")
    ] = None,
    tag: Tag = None,
    category: Annotated[
        str | None,
        Field(description="Same as tag: a recipe's category is its primary tag. Prefer tag."),
    ] = None,
    max_total_minutes: MaxTotalMinutes = None,
    max_prep_minutes: MaxPrepMinutes = None,
    max_calories: MaxCalories = None,
    min_protein_g: Grams = None,
    max_fat_g: Grams = None,
    max_carbohydrate_g: Grams = None,
    sort: Annotated[
        Sort,
        Field(
            description="total_time, prep_time and calories sort lowest first; protein highest "
            "first; newest by publication date"
        ),
    ] = "total_time",
    limit: Limit = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Search recipes by ingredients and other properties.

    All filters combine with AND. Results are compact; call get_recipe for the full record.
    `next_offset` is set when more results follow. Nutrition is per serving, and a serving
    is whatever `serving_size` says: usually a person, often a piece, sometimes a glass.
    `include` terms match by containment; `include_matches` in the result lists the canonical
    names each term matched. `exclude` names must match a canonical name exactly (see
    find_ingredients) so that nothing is hidden by accident.
    """
    where: list[str] = []
    params: list[Any] = []
    include_matches: dict[str, list[str]] = {}
    con = _connect()
    for term in include or []:
        term = term.strip().lower()
        if not term:
            continue
        names = _contained_names(con, term)
        include_matches[term] = names
        if not names:
            where.append("0")
            continue
        # A non-correlated IN builds the id set once from the (name, recipe_id) index;
        # a correlated EXISTS probes it once per recipe and is an order of magnitude slower.
        marks = ",".join("?" * len(names))
        where.append(f"r.id IN (SELECT recipe_id FROM ingredient WHERE name IN ({marks}))")
        params.extend(names)
    if text and text.strip():
        where.append("r.id IN (SELECT rowid FROM recipe_fts WHERE recipe_fts MATCH ?)")
        params.append(_fts_query(text))
    # Every category is also one of the recipe's tags, and tags cover recipes with no category,
    # so both filter on the tag table.
    shared, shared_params = _shared_filters(
        exclude=exclude,
        tags=(tag, category),
        max_total_minutes=max_total_minutes,
        max_prep_minutes=max_prep_minutes,
        max_calories=max_calories,
        min_protein_g=min_protein_g,
        max_fat_g=max_fat_g,
        max_carbohydrate_g=max_carbohydrate_g,
    )
    where += shared
    params += shared_params
    sql = "FROM recipe r" + (" WHERE " + " AND ".join(where) if where else "")
    with con:
        rows, total = _page(con, sql, params, _ORDER_BY[sort], limit, offset)
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
        "next_offset": _next_offset(offset, len(rows), total),
        "include_matches": include_matches,
        "results": [
            {**{c: r[c] for c in _RESULT_COLUMNS}, "ingredients": names[r["id"]]} for r in rows
        ],
    }


@mcp.tool
def search_by_pantry(
    have: Annotated[
        list[str],
        Field(
            description="Ingredients the cook has. Each covers any ingredient name containing "
            "it as whole words, as in search_recipes include."
        ),
    ],
    use_up: Annotated[
        list[str] | None,
        Field(
            description="Ingredients to use up first, e.g. ones about to expire. Matched like "
            "`have`; recipes using more of them rank first."
        ),
    ] = None,
    lacking: Annotated[
        list[str] | None,
        Field(description="Staples the cook is out of, e.g. 'butter'; they count as missing"),
    ] = None,
    max_missing: Annotated[
        float,
        Field(ge=0, description="Most missing ingredients allowed; a missing basic counts half"),
    ] = 2,
    exclude: Exclude = None,
    tag: Tag = None,
    max_total_minutes: MaxTotalMinutes = None,
    max_prep_minutes: MaxPrepMinutes = None,
    max_calories: MaxCalories = None,
    min_protein_g: Grams = None,
    max_fat_g: Grams = None,
    max_carbohydrate_g: Grams = None,
    limit: Limit = 20,
    offset: Offset = 0,
) -> dict[str, Any]:
    """Find recipes to cook from what the cook has, ranked by how much of it they use.

    Salt, pepper, water and cooking oils are assumed present. Basics such as butter, flour,
    milk, lemon and stock are probably present: a missing one counts half. Everything else
    counts one. Results use the most of `use_up`, then of `have` and `use_up` together, then
    need the least; each lists what it uses, what is missing (with its group, e.g. 'To
    serve') and which basics it needs. Other filters work as in search_recipes;
    `next_offset` is set when more results follow.
    """
    con = _connect()

    def matches(terms: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for term in terms:
            term = term.strip().lower()
            if term:
                out[term] = _contained_names(con, term)
        return out

    have_matches = matches(have)
    use_up_matches = matches(use_up or [])
    urgent = {n for names in use_up_matches.values() for n in names}
    mine = urgent | {n for names in have_matches.values() for n in names}
    echo = {"have_matches": have_matches}
    if use_up is not None:
        echo["use_up_matches"] = use_up_matches
    out_of = {x.strip().lower() for x in lacking or []}
    staples = {
        r["name"]: r["weight"]
        for r in con.execute("SELECT name, weight FROM staple")
        if r["name"] not in mine and r["name"] not in out_of
    }
    if not mine:
        return {"total": 0, "next_offset": None, **echo, "results": []}
    # k lists every name that lowers a recipe's missing count: the cook's own (weight 0,
    # counted as used, and as urgent if in use_up) and the staples (their weight).
    # Starting from the name index keeps the scan to recipes that use at least one of them.
    known = [(n, 0.0, 1, int(n in urgent)) for n in sorted(mine)] + [
        (n, w, 0, 0) for n, w in sorted(staples.items())
    ]
    rows_sql = ",".join(["(?,?,?,?)"] * len(known))
    prefix = f"""WITH k(name, weight, mine, urgent) AS (VALUES {rows_sql}),
    hit AS (
        SELECT i.recipe_id, SUM(1 - k.weight) AS saved, SUM(k.mine) AS used,
               SUM(k.urgent) AS used_up
        FROM (SELECT DISTINCT recipe_id, name FROM ingredient
              WHERE name IN (SELECT name FROM k)) i
        JOIN k USING (name)
        GROUP BY i.recipe_id HAVING used > 0
    )"""
    params: list[Any] = [v for row in known for v in row]
    where, shared_params = _shared_filters(
        exclude=exclude,
        tags=(tag,),
        max_total_minutes=max_total_minutes,
        max_prep_minutes=max_prep_minutes,
        max_calories=max_calories,
        min_protein_g=min_protein_g,
        max_fat_g=max_fat_g,
        max_carbohydrate_g=max_carbohydrate_g,
    )
    sql = " AND ".join(
        [
            "FROM hit h JOIN recipe r ON r.id = h.recipe_id WHERE r.n_ingredients - h.saved <= ?",
            *where,
        ]
    )
    params += [max_missing, *shared_params]
    with con:
        rows, total = _page(
            con,
            sql,
            params,
            "h.used_up DESC, h.used DESC, missing_cost",
            limit,
            offset,
            prefix=prefix,
            extra=", h.used, r.n_ingredients - h.saved AS missing_cost",
        )
        ids = [r["id"] for r in rows]
        detail: dict[int, dict[str, list[Any]]] = {
            i: {"uses": [], "missing": [], "missing_basics": []} for i in ids
        }
        seen: set[tuple[int, str]] = set()
        if ids:
            marks = ",".join("?" * len(ids))
            for rec_id, name, group in con.execute(
                f'SELECT recipe_id, name, "group" FROM ingredient WHERE recipe_id IN ({marks}) '
                "ORDER BY recipe_id, position",
                ids,
            ):
                if (rec_id, name) in seen:
                    continue
                seen.add((rec_id, name))
                if name in mine:
                    detail[rec_id]["uses"].append(name)
                    continue
                weight = staples.get(name, 1.0)
                if weight == 0:
                    continue
                item = {"name": name, "group": group} if group else {"name": name}
                detail[rec_id]["missing" if weight == 1 else "missing_basics"].append(item)
    return {
        "total": total,
        "next_offset": _next_offset(offset, len(rows), total),
        **echo,
        "results": [
            {
                **{c: r[c] for c in _RESULT_COLUMNS},
                "missing_cost": r["missing_cost"],
                **detail[r["id"]],
                **(
                    {"uses_up": [n for n in detail[r["id"]]["uses"] if n in urgent]}
                    if use_up is not None
                    else {}
                ),
            }
            for r in rows
        ],
    }


@mcp.tool
def list_tags() -> list[dict[str, Any]]:
    """List every tag usable with search_recipes(tag=...), with recipe counts, most used first.

    Tags cover courses ('main dish', 'desserts'), diets ('vegetarian', 'vegan'), seasons,
    occasions and cuisines ('swiss cuisine').
    """
    with _connect() as con:
        rows = con.execute(
            "SELECT tag, COUNT(*) AS recipes FROM tag GROUP BY tag ORDER BY recipes DESC, tag"
        ).fetchall()
    return [{"tag": r["tag"], "recipes": r["recipes"]} for r in rows]


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
