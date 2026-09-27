---
name: fooble
description: Find recipes from fooby.ch by ingredient, time, tag (course, cuisine, diet, season, occasion) or free text using the fooble MCP tools. Use when the user asks what to cook, wants recipes with or without certain ingredients, or mentions fooby.
---

# Finding recipes with fooble

The `fooble` MCP server indexes the English recipes of fooby.ch. Five tools:

1. `find_ingredients(query, limit, offset)`: resolve a word to the canonical ingredient names the
   index uses, with recipe counts. Names are lowercase singular nouns such as
   `onion`, `bell pepper`, `chicken`, `stock`. Call it whenever you're unsure how an
   ingredient is named, or when a search returns nothing.
2. `search_recipes(include, exclude, text, tag, category, max_total_minutes, max_prep_minutes, max_calories, min_protein_g, max_fat_g, max_carbohydrate_g, sort, limit, offset)`:
   all filters combine with AND. `include` terms match any ingredient name that
   contains them as whole words (`onion` also finds `red onion` and `spring onion`;
   `include_matches` in the result shows what each term matched). `exclude` takes
   exact canonical names so nothing is hidden by accident. Results are compact
   (id, title, category, times, serving size, nutrition, ingredient names).
   `sort` is `total_time` (default), `prep_time`, `calories`, `protein` (highest
   first) or `newest`. Nutrition is per serving, and `serving_size` says what a
   serving is: usually a person, often a piece.
3. `search_by_pantry(have, use_up, lacking, max_missing, exclude, tag, max_total_minutes, max_prep_minutes, max_calories, min_protein_g, max_fat_g, max_carbohydrate_g, limit, offset)`:
   recipes to cook from what the user has. `have` terms match like `include`.
   Salt, pepper, water and cooking oils are assumed; basics such as butter, flour,
   milk, lemon and stock count half when missing; `lacking` names basics the user
   is out of. `max_missing` (default 2) caps the weighted count. `use_up` names
   what should go first, such as food about to expire; it counts as had. Results
   use the most of `use_up` first, then the most of both lists, and list `uses`,
   `uses_up` (when `use_up` is given), `missing` (with the recipe section, e.g.
   "To serve") and `missing_basics`.
4. `list_tags()`: every tag with its recipe count. Tags are a fixed lowercase
   vocabulary covering courses (`main dish`, `desserts`), diets (`vegetarian`,
   `vegan`), seasons, occasions and cuisines (`swiss cuisine`). Call it before
   filtering by `tag`; don't guess labels. `category` is the recipe's primary tag
   and filters the same way as `tag`.
5. `get_recipe(recipe_id)`: the full recipe with quantities, steps, nutrition and
   the fooby URL. Call it only for recipes the user wants to see in detail.

## Workflow

- "What can I make with what I have?" goes to `search_by_pantry`: "eggs,
  spinach, feta and some pasta" becomes `have=["egg", "spinach", "feta",
  "pasta"]`. Say what each suggestion is missing, and mention basics only as
  "you probably have". Raise `max_missing` if nothing comes back.
- Food the user wants gone first ("the spinach needs using up", "the cream
  expires tomorrow") goes in `use_up` instead of `have`. Say which suggestions
  use it.
- "Something with spinach and feta", where every ingredient must be in the
  dish, goes to `search_recipes(include=[...])`.
- If a name isn't obviously canonical, confirm it with `find_ingredients`
  ("peppers" may be `bell pepper` or `chilli`). This matters most for `exclude`.
- Use `exclude` for allergies and dislikes. Use `max_total_minutes` for
  "quick"; it is more reliable than the `quick recipes` tag. Use
  `max_prep_minutes` for "little effort", since total time includes resting
  and baking.
- For "high protein" or "light", combine `min_protein_g` or `max_calories` with
  the matching `sort`, and mention the serving size when quoting numbers.
- For courses, cuisines, seasons and occasions ("something Italian",
  "something for Christmas"), call `list_tags` and filter by the matching tag.
  Use `text` for dish names ("curry", "pasta") and for anything without a tag.
- `find_ingredients`, `search_recipes` and `search_by_pantry` page: pass the
  returned `next_offset` as `offset` to get more, and stop when it is null.
  Prefer narrowing the filters to paging deep.
- Present a shortlist of 3 to 5 titles with total time, then fetch details for
  the one the user picks. Always give the fooby URL when showing a recipe.
- Quantities in `get_recipe` are for the stated yield; scale them if asked.

## Limits

- English recipes only; canonical names are English.
- Diet labels (vegetarian, vegan) exist only as tags on some recipes. Prefer
  filtering by ingredients over trusting tags.
- The index is a snapshot; a recipe missing here may still exist on fooby.ch.
