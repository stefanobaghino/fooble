import json
import sqlite3
from pathlib import Path

from fooble.extract import extract
from fooble.index import build_index, db_path


def make_data_dir(tmp_path: Path, fixtures: Path) -> Path:
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    for rid in (1001, 1002):
        record = extract((fixtures / f"{rid}.html").read_text(encoding="utf-8"), rid)
        (recipes / f"{rid}.json").write_text(json.dumps(record), encoding="utf-8")
    return tmp_path


def test_build_index(tmp_path: Path, fixtures: Path):
    data_dir = make_data_dir(tmp_path, fixtures)
    assert build_index(data_dir) == 2
    con = sqlite3.connect(db_path(data_dir))

    assert con.execute(
        "SELECT id, title, total_minutes, calories FROM recipe ORDER BY id"
    ).fetchall() == [
        (1001, "Braised beef with squash", 90, 321.0),
        (1002, "Herb rice balls", 75, 250.0),
    ]
    assert con.execute(
        "SELECT recipe_id FROM ingredient WHERE name = 'garlic' ORDER BY recipe_id"
    ).fetchall() == [(1001,)]
    assert con.execute("SELECT tag FROM tag WHERE recipe_id = 1001 ORDER BY tag").fetchall() == [
        ("Autumn",),
        ("Soups and stew",),
        ("main dish",),
    ]
    assert con.execute("SELECT name FROM alias WHERE alias = 'zucchini'").fetchone() == (
        "courgette",
    )
    assert con.execute(
        "SELECT rowid FROM recipe_fts WHERE recipe_fts MATCH 'braised'"
    ).fetchall() == [(1001,)]
    assert con.execute(
        "SELECT rowid FROM recipe_fts WHERE recipe_fts MATCH 'ingredients: risotto'"
    ).fetchall() == [(1002,)]
    assert (
        json.loads(con.execute("SELECT json FROM recipe WHERE id = 1002").fetchone()[0])["title"]
        == "Herb rice balls"
    )
    assert con.execute("SELECT value FROM meta WHERE key = 'recipes'").fetchone() == ("2",)


def test_build_index_replaces_existing_db_atomically(tmp_path: Path, fixtures: Path):
    data_dir = make_data_dir(tmp_path, fixtures)
    build_index(data_dir)
    (data_dir / "recipes" / "1002.json").unlink()
    assert build_index(data_dir) == 1
    con = sqlite3.connect(db_path(data_dir))
    assert con.execute("SELECT COUNT(*) FROM recipe").fetchone() == (1,)
    assert not db_path(data_dir).with_suffix(".db.tmp").exists()
