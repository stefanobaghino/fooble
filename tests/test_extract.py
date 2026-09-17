import json
from pathlib import Path

import pytest

from fooble.extract import ExtractError, extract, extract_all, iso_duration_minutes


@pytest.mark.parametrize(
    ("value", "minutes"),
    [
        ("PT45M", 45),
        ("PT2H45M", 165),
        ("PT1H", 60),
        ("P1DT2H", 1560),
        ("", None),
        (None, None),
        ("bogus", None),
    ],
)
def test_iso_duration_minutes(value, minutes):
    assert iso_duration_minutes(value) == minutes


def test_extract_stew(fixtures: Path):
    r = extract((fixtures / "1001.html").read_text(encoding="utf-8"), 1001)
    assert r["id"] == 1001
    assert r["url"] == "https://recipes.example/en/recipes/1001/braised-beef-with-squash"
    assert r["title"] == "Braised beef with squash"
    assert r["image"] == "https://images.example/1001.jpg"
    assert r["category"] == "main dish"
    assert r["keywords"] == ["main dish", "Soups and stew", "Autumn"]
    assert r["tags"] == ["main dish", "Soups and stew", "Autumn"]
    assert (r["prep_minutes"], r["total_minutes"]) == (20, 90)
    assert (r["yield"], r["serving_size"]) == ("4", "4 person")
    assert r["nutrition"] == {
        "calories": 321.0,
        "fat_g": 12.0,
        "carbohydrate_g": 34.0,
        "protein_g": 15.0,
    }
    assert r["published"] == "2024-02-29"
    assert [s["name"] for s in r["steps"]] == ["Stew", "To finish"]
    assert r["steps"][0]["text"].startswith("Heat a dash of oil")
    assert not r["steps"][0]["text"].endswith(" "), "step text is stripped"

    ings = r["ingredients"]
    assert len(ings) == 11
    assert ings[0] == {
        "text": "oil for frying",
        "quantity": None,
        "unit": None,
        "desc": "oil",
        "name": "oil",
        "matched": True,
        "group": "Stew",
    }
    assert ings[1] == {
        "text": "beef ragout",
        "quantity": 400.0,
        "unit": "g",
        "desc": "beef ragout",
        "name": "beef",
        "matched": True,
        "group": "Stew",
    }
    squash = ings[8]
    assert squash["text"].startswith("squash (e.g. red kuri)")
    assert (squash["desc"], squash["name"], squash["group"]) == ("squash", "squash", "To finish")
    assert ings[10]["quantity"] == 0.5 and ings[10]["unit"] == "bunch"


def test_extract_tolerates_raw_newlines_in_json_ld_and_splits_compound_lines(fixtures: Path):
    r = extract((fixtures / "1002.html").read_text(encoding="utf-8"), 1002)
    assert r["title"] == "Herb rice balls", "title is stripped"
    assert r["category"] == "Aperitif fingerfood"
    assert "\n" in r["description"]
    names = [(i["name"], i["group"]) for i in r["ingredients"]]
    assert ("salt", "Risotto") in names and ("pepper", "Risotto") in names
    assert [n for n, _ in names].count("egg") == 2
    assert {g for _, g in names} == {"Risotto", "To shape", "Crumb coating", "To deep-fry"}
    assert all(i["matched"] for i in r["ingredients"])


def test_extract_without_recipe_block_raises():
    with pytest.raises(ExtractError):
        extract("<html><body>nothing</body></html>", 1)


def test_extract_all_writes_json_and_unmatched(tmp_path: Path, fixtures: Path):
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    for name in ("1001.html", "1002.html"):
        (html_dir / name).write_bytes((fixtures / name).read_bytes())
    (html_dir / "1.html").write_text("<html></html>")
    page = (fixtures / "1001.html").read_text(encoding="utf-8")
    (html_dir / "2.html").write_text(page.replace("beef ragout", "zorkleberries"), encoding="utf-8")

    assert extract_all(tmp_path) == (3, 1)
    record = json.loads((tmp_path / "recipes" / "1001.json").read_text(encoding="utf-8"))
    assert record["title"] == "Braised beef with squash"
    assert not (tmp_path / "recipes" / "1.json").exists()
    assert (tmp_path / "unmatched.tsv").read_text() == "1\tzorkleberry\n"

    assert extract_all(tmp_path, ids=[1002]) == (1, 0)
