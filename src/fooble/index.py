"""Build the SQLite index (data/fooble.db) from extracted recipe JSON."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from .normalize import alias_map

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE recipe (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    image TEXT,
    category TEXT,
    prep_minutes INTEGER,
    total_minutes INTEGER,
    yield_text TEXT,
    serving_size TEXT,
    calories REAL,
    fat_g REAL,
    carbohydrate_g REAL,
    protein_g REAL,
    published TEXT,
    json TEXT NOT NULL
);
CREATE TABLE ingredient (
    recipe_id INTEGER NOT NULL REFERENCES recipe(id),
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    desc TEXT NOT NULL,
    text TEXT NOT NULL,
    quantity REAL,
    unit TEXT,
    "group" TEXT,
    PRIMARY KEY (recipe_id, position)
);
CREATE INDEX ingredient_name ON ingredient(name, recipe_id);
CREATE TABLE tag (
    recipe_id INTEGER NOT NULL REFERENCES recipe(id),
    tag TEXT NOT NULL,
    PRIMARY KEY (recipe_id, tag)
);
CREATE INDEX tag_tag ON tag(tag COLLATE NOCASE);
CREATE TABLE alias (
    alias TEXT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE VIRTUAL TABLE recipe_fts USING fts5(
    title, keywords, ingredients, tokenize = 'porter unicode61'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def db_path(data_dir: Path) -> Path:
    return data_dir / "fooble.db"


def build_index(data_dir: Path) -> int:
    """Rebuild the database from scratch, atomically replacing any existing one."""
    recipes_dir = data_dir / "recipes"
    target = db_path(data_dir)
    tmp = target.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    n = 0
    for path in sorted(recipes_dir.glob("*.json")):
        r = json.loads(path.read_text(encoding="utf-8"))
        nut = r.get("nutrition") or {}
        con.execute(
            "INSERT INTO recipe VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                r["id"],
                r["url"],
                r["title"],
                r.get("description"),
                r.get("image"),
                r.get("category"),
                r.get("prep_minutes"),
                r.get("total_minutes"),
                r.get("yield"),
                r.get("serving_size"),
                nut.get("calories"),
                nut.get("fat_g"),
                nut.get("carbohydrate_g"),
                nut.get("protein_g"),
                r.get("published"),
                json.dumps(r, ensure_ascii=False),
            ),
        )
        con.executemany(
            "INSERT INTO ingredient VALUES (?,?,?,?,?,?,?,?)",
            [
                (
                    r["id"],
                    i,
                    ing["name"],
                    ing["desc"],
                    ing["text"],
                    ing.get("quantity"),
                    ing.get("unit"),
                    ing.get("group"),
                )
                for i, ing in enumerate(r["ingredients"])
            ],
        )
        con.executemany(
            "INSERT OR IGNORE INTO tag VALUES (?,?)", [(r["id"], t) for t in r.get("tags", [])]
        )
        con.execute(
            "INSERT INTO recipe_fts(rowid, title, keywords, ingredients) VALUES (?,?,?,?)",
            (
                r["id"],
                r["title"],
                ", ".join(r.get("keywords", [])),
                ", ".join(sorted({ing["name"] for ing in r["ingredients"]})),
            ),
        )
        n += 1
    con.executemany("INSERT INTO alias VALUES (?,?)", list(alias_map().items()))
    con.execute("INSERT INTO meta VALUES ('recipes', ?)", (str(n),))
    con.commit()
    con.close()
    tmp.replace(target)
    log.info("indexed %d recipes into %s", n, target)
    return n
