# fooble

Crawler, index and MCP server for [fooby.ch](https://fooby.ch) recipes, plus an
unofficial Claude plugin that uses them.

fooble crawls the English recipe pages, extracts their structured data into a
local SQLite index, and serves that index to Claude over
[MCP](https://modelcontextprotocol.io) so you can ask for "something with
tomatoes and garlic but no onion, under an hour".

## How it works

| Step      | Command          | Input                | Output                          |
| --------- | ---------------- | -------------------- | ------------------------------- |
| crawl     | `fooble crawl`   | fooby.ch sitemap     | `data/html/<id>.html`           |
| extract   | `fooble extract` | cached HTML          | `data/recipes/<id>.json`        |
| index     | `fooble index`   | recipe JSON          | `data/fooble.db` (SQLite, FTS5) |
| serve     | `fooble serve`   | `data/fooble.db`     | MCP server on `:8000/mcp`       |

The crawler honours fooby's `robots.txt` (`Crawl-delay: 10`), so a full crawl
of ~8,300 recipes takes about a day. It is resumable: cached pages are skipped.

Extraction reads the schema.org `Recipe` JSON-LD block plus fooby's
portion-calculator ingredient data. Ingredient names are normalized to
canonical singular nouns via `src/fooble/aliases.toml`; names that no alias
matches are listed in `data/unmatched.tsv` after each extraction so the map
can grow.

## Usage

Requires [uv](https://docs.astral.sh/uv/) 0.12.15; it installs the pinned Python interpreter on first `uv sync`.

```sh
uv sync
uv run fooble crawl --limit 200   # omit --limit for everything
uv run fooble extract
uv run fooble index
```

All commands accept `--data-dir` (default `./data`).

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Tests run offline against fixtures in `tests/fixtures`.
