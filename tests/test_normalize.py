import pytest

from fooble.normalize import alias_map, canonical, clean, singularize


def test_alias_map_is_consistent():
    aliases = alias_map()
    canonicals = set(aliases.values())
    for alias, name in aliases.items():
        assert alias == alias.lower().strip(), alias
        assert name in canonicals
        assert aliases[name] == name, f"canonical {name!r} must map to itself"


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("onions", "onion"),
        ("tomatoes", "tomato"),
        ("cherries", "cherry"),
        ("leaves", "leaf"),
        ("radishes", "radish"),
        ("peas", "pea"),
        ("asparagus", "asparagus"),
        ("cress", "cress"),
        ("egg", "egg"),
    ],
)
def test_singularize(word, expected):
    assert singularize(word) == expected


@pytest.mark.parametrize(
    ("desc", "expected"),
    [
        ("onions, thinly sliced", "onions"),
        ("squash (e.g. red kuri), cut into approx. 2 cm pieces", "squash"),
        ("flat-leaf parsley, finely chopped", "flat-leaf parsley"),
        ("oil for frying", "oil"),
        ("salt to taste", "salt"),
        ("Fresh basil", "basil"),
        ("2 garlic cloves", "garlic cloves"),
        ("lemon or lime", "lemon"),
    ],
)
def test_clean(desc, expected):
    assert clean(desc) == expected


@pytest.mark.parametrize(
    ("desc", "name", "matched"),
    [
        ("beef ragout", "beef", True),
        ("hot paprika", "paprika", True),
        ("garlic cloves, squeezed", "garlic", True),
        ("meat bouillon", "stock", True),
        ("sour single cream", "sour cream", True),
        ("hot smoked paprika", "paprika", True),  # suffix fallback
        ("zorkleberries, washed", "zorkleberry", False),  # unknown: cleaned + singularized
        ("mostbröckli", "dried meat", True),
        ("spring onion incl. green part", "spring onion", True),
        ("", "", False),
    ],
)
def test_canonical(desc, name, matched):
    assert canonical(desc) == (name, matched)
