---
name: fooble
description: Find recipes from fooby.ch by ingredient, time, category or free text using the fooble MCP tools. Use when the user asks what to cook, wants recipes with or without certain ingredients, or mentions fooby.
---

# Finding recipes with fooble

The `fooble` MCP server indexes the English recipes of fooby.ch. Three tools:

1. `find_ingredients(query)`: resolve a word to the canonical ingredient names the
   index uses, with recipe counts. Names are lowercase singular nouns such as
   `onion`, `bell pepper`, `chicken`, `stock`. Call it whenever you're unsure how an
   ingredient is named, or when a search returns nothing.
2. `search_recipes(include, exclude, text, category, tag, max_total_minutes, max_calories, limit)`:
   all filters combine with AND. `include` and `exclude` take canonical names.
   Results are compact (id, title, category, total minutes, calories, ingredient
   names), sorted by total time.
3. `get_recipe(recipe_id)`: the full recipe with quantities, steps, nutrition and
   the fooby URL. Call it only for recipes the user wants to see in detail.

## Workflow

- Turn the request into ingredients first. "Something with what's in my fridge:
  eggs, spinach, feta" becomes `include=["egg", "spinach", "feta"]`.
- If a name isn't obviously canonical, confirm it with `find_ingredients`
  ("peppers" may be `bell pepper` or `chilli`; "cheese" has many names).
- Use `exclude` for allergies and dislikes. Use `max_total_minutes` for
  "quick". Use `text` for dish names or cuisines ("curry", "pasta").
- Present a shortlist of 3 to 5 titles with total time, then fetch details for
  the one the user picks. Always give the fooby URL when showing a recipe.
- Quantities in `get_recipe` are for the stated yield; scale them if asked.

## Limits

- English recipes only; canonical names are English.
- Diet labels (vegetarian, vegan) exist only as tags on some recipes. Prefer
  filtering by ingredients over trusting tags.
- The index is a snapshot; a recipe missing here may still exist on fooby.ch.
