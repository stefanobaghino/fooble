# fooble

Unofficial Claude plugin to discover recipes on [fooby.ch](https://fooby.ch).

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

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

All commands accept `--data-dir` (default `./data`).

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
```
