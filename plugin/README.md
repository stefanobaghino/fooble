# fooble plugin

Claude Code plugin that connects to a running fooble MCP server and teaches
Claude how to search it. Set `FOOBLE_MCP_URL` to your server's endpoint
(default `http://localhost:8000/mcp`) in the environment Claude Code runs in.

Install from the marketplace:

```
/plugin marketplace add stefanobaghino/claude-plugins
/plugin install fooble
```

Or load it straight from this checkout: `claude --plugin-dir plugin`.
