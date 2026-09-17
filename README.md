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

## Usage

Requires [uv](https://docs.astral.sh/uv/) 0.12.15; it installs the pinned Python interpreter on first `uv sync`.

```sh
uv sync
```

All commands accept `--data-dir` (default `./data`).

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
