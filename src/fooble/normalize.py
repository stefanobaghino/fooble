"""Turn free-text ingredient descriptions into canonical ingredient names."""

from __future__ import annotations

import re
import tomllib
from functools import lru_cache
from importlib import resources

# Trailing or leading phrases that describe preparation, not the ingredient.
_PREP_WORDS = (
    "finely|coarsely|thinly|thickly|roughly|freshly|approx\\.?|about|"
    "chopped|sliced|diced|grated|crushed|minced|squeezed|peeled|cored|"
    "halved|quartered|cubed|shredded|julienned|melted|softened|beaten|"
    "toasted|roasted|cooked|boiled|drained|rinsed|washed|trimmed|shelled|pitted|skinned|"
    "boneless|skinless|deboned|prepared|preprepared|ready-made|ready-to-use|day-old|"
    "hard-boiled|soft-boiled|fried|grilled|freeze-dried|preserved|unsalted|unsweetened|"
    "sweetened|by the piece|"
    "for frying|for greasing|for dusting|for brushing|for sprinkling|for garnish|"
    "for the tin|for the tray|for the baking tray|for the mould|for the dish|"
    "to taste|to serve|as needed|if needed|optional|plus extra|extra|"
    "for the sauce|for sauce|for the dressing|for the filling|for the topping|"
    "in slices|in strips|in pieces|in cubes|in chunks|in oil|in brine|in syrup|in juice|"
    "incl\\.? .*|including .*"
)
_PREP_RE = re.compile(rf"\b(?:{_PREP_WORDS})\b", re.I)
_PAREN_RE = re.compile(r"\([^)]*\)")
_LEADING_QTY_RE = re.compile(
    r"^\s*(?:approx\.?\s*)?[\d½¼¾⅓⅔⅛.,/\-–\s]+(?:[a-z]{1,4}\b)?\s*(?:of\s+)?", re.I
)
# Words naming a part or cut of the ingredient; dropped as a last resort ("sage leaf" -> "sage").
_PART_WORDS = frozenset(
    "leaf sprig needle kernel sliver strip cube slice granule fillet piece chunk floret stalk "
    "stick head bunch clove ball pearl".split()
)
_DESCRIPTOR_RE = re.compile(
    r"\b(?:fresh|frozen|dried|raw|ripe|large|small|medium|big|little|whole|"
    r"organic|good|best|quality|quick|some|a little|a few|a pinch of|pinch of|dash of)\b",
    re.I,
)


@lru_cache(maxsize=1)
def alias_map() -> dict[str, str]:
    """{alias: canonical} loaded from aliases.toml, including canonical -> canonical."""
    data = tomllib.loads(resources.files(__package__).joinpath("aliases.toml").read_text("utf-8"))
    out: dict[str, str] = {}
    for canonical, aliases in data["aliases"].items():
        out[canonical] = canonical
        for a in aliases:
            out[a] = canonical
    return out


def singularize(word: str) -> str:
    if len(word) <= 3:
        return word
    for suffix, repl in (
        ("ies", "y"),
        ("oes", "o"),
        ("ches", "ch"),
        ("shes", "sh"),
        ("sses", "ss"),
    ):
        if word.endswith(suffix):
            return word[: -len(suffix)] + repl
    if word.endswith("ss") or word.endswith("us"):
        return word
    if word.endswith("ves"):
        return word[:-3] + "f"
    if word.endswith("s"):
        return word[:-1]
    return word


def clean(desc: str) -> str:
    """Strip quantities, prep notes and descriptors; keep the head noun phrase."""
    s = desc.lower().replace("\xa0", " ")
    s = _PAREN_RE.sub(" ", s)
    s = s.split(" or ", 1)[0]
    s = s.split(" with ", 1)[0]
    # "shelled, raw prawns": keep the first comma-separated part that still names something.
    for part in s.split(","):
        part = _clean_part(part)
        if part:
            return part
    return ""


def _clean_part(s: str) -> str:
    s = _LEADING_QTY_RE.sub("", s) if re.match(r"^\s*[\d½¼¾⅓⅔⅛]", s) else s
    s = _PREP_RE.sub(" ", s)
    s = _DESCRIPTOR_RE.sub(" ", s)
    s = re.sub(r"[^a-zà-ÿ' \-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -'")
    return s


def canonical(desc: str) -> tuple[str, bool]:
    """Return (canonical_name, matched_alias). Unmatched names are cleaned and singularized."""
    aliases = alias_map()
    s = clean(desc)
    if not s:
        return desc.lower().strip(), False
    if s in aliases:
        return aliases[s], True
    words = s.split()
    sing = " ".join(words[:-1] + [singularize(words[-1])])
    if sing in aliases:
        return aliases[sing], True
    # Try progressively shorter suffixes: "hot smoked paprika" -> "smoked paprika" -> "paprika".
    for i in range(1, len(words)):
        tail = " ".join(words[i:])
        if tail in aliases:
            return aliases[tail], True
        tail_s = " ".join(words[i:-1] + [singularize(words[-1])])
        if tail_s in aliases:
            return aliases[tail_s], True
    # Drop a trailing part word: "sage leaves" -> "sage", "chicken strips" -> "chicken".
    if len(words) > 1 and singularize(words[-1]) in _PART_WORDS:
        name, matched = canonical(" ".join(words[:-1]))
        if matched:
            return name, True
    return sing, False
