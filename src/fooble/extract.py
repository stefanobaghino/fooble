"""Parse cached recipe HTML into normalized JSON records."""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

from selectolax.parser import HTMLParser

from .normalize import canonical

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = 1
_DURATION_RE = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?$")
_NUMBER_RE = re.compile(r"[\d.]+")


class ExtractError(Exception):
    pass


def iso_duration_minutes(value: str | None) -> int | None:
    if not value:
        return None
    m = _DURATION_RE.match(value)
    if not m:
        return None
    days, hours, minutes = (int(x) if x else 0 for x in m.groups())
    return days * 24 * 60 + hours * 60 + minutes


def _number(value: str | None) -> float | None:
    if not value:
        return None
    m = _NUMBER_RE.search(value)
    return float(m.group()) if m else None


def _ld_recipe(tree: HTMLParser) -> dict[str, Any]:
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            # strict=False tolerates raw newlines inside strings, which fooby emits at times.
            data = json.loads(node.text(), strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "Recipe":
            return data
    raise ExtractError("no JSON-LD Recipe block")


def _ingredients(tree: HTMLParser) -> list[dict[str, Any]]:
    """Ingredients from the DOM list (text, group) merged with the portion-calculator JSON
    (quantity, unit, short description), which are emitted in the same order."""
    items: list[dict[str, Any]] = []
    for lst in tree.css("div.recipe-ingredientlist"):
        group = None
        for child in lst.iter():
            classes = child.attributes.get("class", "") or ""
            if child.tag == "p" and "heading" in classes:
                group = child.text(strip=True) or None
            elif "recipe-ingredientlist__step-wrapper" in classes:
                for row in child.css("div.recipe-ingredientlist__ingredient-wrapper"):
                    qty_node = row.css_first("span.recipe-ingredientlist__ingredient-quantity")
                    desc_node = row.css_first("span.recipe-ingredientlist__ingredient-desc")
                    items.append(
                        {
                            "quantity_text": re.sub(r"\s+", " ", qty_node.text(strip=True))
                            if qty_node
                            else "",
                            "text": desc_node.text(strip=True) if desc_node else "",
                            "group": group,
                        }
                    )
    calc_node = tree.css_first("[data-portion-calculator-initial-all-ingredients]")
    calc: list[dict[str, Any]] = []
    if calc_node is not None:
        raw = html.unescape(calc_node.attributes["data-portion-calculator-initial-all-ingredients"])
        calc = json.loads(raw).get("ingredients", [])
    if calc and len(calc) != len(items):
        log.warning("portion calculator lists %d ingredients, DOM %d", len(calc), len(items))
        calc = []
    out = []
    for i, item in enumerate(items):
        c = calc[i] if calc else {}
        desc = (c.get("desc") or item["text"]).strip()
        for name, matched in _names(desc):
            out.append(
                {
                    "text": item["text"],
                    "quantity": c.get("quantity") or None,
                    "unit": (c.get("measure") or "").strip() or None,
                    "desc": desc,
                    "name": name,
                    "matched": matched,
                    "group": item["group"],
                }
            )
    return out


def _names(desc: str) -> list[tuple[str, bool]]:
    """Canonical names for one ingredient line. "salt and pepper" yields two names,
    but only when both halves are known ingredients ("sweet and sour sauce" stays whole)."""
    if " and " in desc:
        parts = [canonical(p) for p in desc.split(" and ", 1)]
        if all(matched for _, matched in parts):
            return parts
    return [canonical(desc)]


def extract(page: str, recipe_id: int, url: str | None = None) -> dict[str, Any]:
    tree = HTMLParser(page)
    ld = _ld_recipe(tree)
    canonical_link = tree.css_first('link[rel="canonical"]')
    nutrition = ld.get("nutrition") or {}
    keywords = [k.strip() for k in (ld.get("keywords") or "").split(",") if k.strip()]
    tags = [t.text(strip=True) for t in tree.css("a.tag")]
    steps = []
    for step in ld.get("recipeInstructions") or []:
        if isinstance(step, dict):
            steps.append(
                {"name": step.get("name") or None, "text": (step.get("text") or "").strip()}
            )
        else:
            steps.append({"name": None, "text": str(step).strip()})
    return {
        "id": recipe_id,
        "url": url or (canonical_link.attributes.get("href") if canonical_link else None),
        "title": ld["name"].strip(),
        "description": ld.get("description") or None,
        "image": ld.get("image") or None,
        "category": ld.get("recipeCategory") or None,
        "cuisine": ld.get("recipeCuisine") or None,
        "keywords": keywords,
        "tags": tags,
        "prep_minutes": iso_duration_minutes(ld.get("prepTime")),
        "total_minutes": iso_duration_minutes(ld.get("totalTime")),
        "yield": ld.get("recipeYield") or None,
        "serving_size": nutrition.get("servingSize") or None,
        "nutrition": {
            "calories": _number(nutrition.get("calories")),
            "fat_g": _number(nutrition.get("fatContent")),
            "carbohydrate_g": _number(nutrition.get("carbohydrateContent")),
            "protein_g": _number(nutrition.get("proteinContent")),
        },
        "ingredients": _ingredients(tree),
        "steps": steps,
        "published": ld.get("datePublished") or None,
        "extractor_version": EXTRACTOR_VERSION,
    }


def extract_all(data_dir: Path, ids: list[int] | None = None) -> tuple[int, int]:
    """Extract every cached page (or just `ids`) into data/recipes/<id>.json."""
    html_dir = data_dir / "html"
    out_dir = data_dir / "recipes"
    out_dir.mkdir(parents=True, exist_ok=True)
    unmatched: dict[str, int] = {}
    ok = failed = 0
    paths = [html_dir / f"{i}.html" for i in ids] if ids else sorted(html_dir.glob("*.html"))
    for path in paths:
        rid = int(path.stem)
        try:
            record = extract(path.read_text(encoding="utf-8"), rid)
        except Exception as e:  # noqa: BLE001 - report and continue
            log.error("recipe %d: %s", rid, e)
            failed += 1
            continue
        (out_dir / f"{rid}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        for ing in record["ingredients"]:
            if not ing["matched"]:
                unmatched[ing["name"]] = unmatched.get(ing["name"], 0) + 1
        ok += 1
    lines = [f"{n}\t{name}" for name, n in sorted(unmatched.items(), key=lambda x: (-x[1], x[0]))]
    (data_dir / "unmatched.tsv").write_text(
        "".join(f"{line}\n" for line in lines), encoding="utf-8"
    )
    log.info("%d unmatched ingredient names written to unmatched.tsv", len(unmatched))
    return ok, failed
