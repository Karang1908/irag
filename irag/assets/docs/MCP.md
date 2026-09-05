# Connect irag to any coding agent with MCP

irag includes a standard local Model Context Protocol server. It exposes the
same SQLite memory, structural map, provenance, session diary, contradiction
system, and update pipeline to every MCP-capable coding tool. There is no
Claude-specific behavior in the server and no second database per client.

```bash
irag mcp --root /absolute/path/to/your-project
```

The transport is newline-delimited JSON-RPC over stdio. irag negotiates the
stable MCP protocol versions from `2024-11-05` through `2025-11-25`, writes
protocol messages only to stdout, returns JSON Schema tool definitions, and
includes both text and structured results for old and new clients.

## Connect a client

Initialize the project first:

```bash
cd /absolute/path/to/your-project
irag init
```

Then register that exact project path with your coding tool.

### Codex

```bash
codex mcp add irag -- irag mcp --root /absolute/path/to/your-project
```

### Claude Code

```bash
claude mcp add --scope project irag -- irag mcp --root /absolute/path/to/your-project
```

### Cursor, Windsurf, and clients using `mcpServers`

Put this server entry in the client's project MCP configuration:

```json
{
  "mcpServers": {
    "irag": {
      "command": "irag",
      "args": ["mcp", "--root", "/absolute/path/to/your-project"]
    }
  }
}
```

### VS Code-style `servers` configuration

```json
{
  "servers": {
    "irag": {
      "type": "stdio",
      "command": "irag",
      "args": ["mcp", "--root", "/absolute/path/to/your-project"]
    }
  }
}
```

Configuration file names and UI labels differ between clients; the command,
arguments, protocol, data, and tool behavior do not. Use an absolute root so
the client may launch irag from any working directory. If `irag` is not on the
client's PATH, use the absolute path printed by `command -v irag` (Windows:
`where irag`) as `command`.

## The tools every client receives

| MCP tool | What it returns | Model call? |
|---|---|---:|
| `irag_get_context` | live ranked briefing, recap, warnings, selection metadata | no |
| `irag_search` | versioned full-text hits | no |
| `irag_status` | queue, drift, graph, health, and usage facts | no |
| `irag_code_map` | parsed symbols, imports, and importers | no |
| `irag_impact` | transitive blast radius with hop distance | no |
| `irag_trace_claim` | revision and event provenance for a claim | no |
| `irag_list_contradictions` | open rows plus a complete repair prompt | no |
| `irag_learn` | durable lesson | no |
| `irag_record_decision` | durable decision | no |
| `irag_resolve_contradiction` | dismiss/reopen a proven false positive | no |
| `irag_start_session` / `irag_finish_session` | isolated conversation diary | only optional final narration |
| `irag_update` | sync → incremental scan → synthesis → lint | yes, for due pages |

Read tools are deterministic and local. `irag_update` is explicitly the write
path and may invoke the configured summary provider. The server uses irag's
cross-process repository lock, so an MCP update cannot race the dashboard,
CLI, git hook, or another coding agent into a partial state.

## Recommended agent loop

1. Call `irag_start_session` once.
2. Call `irag_get_context` with the task and currently open files before broad
   exploration.
3. Use `irag_code_map`, `irag_impact`, and `irag_trace_claim` before edits.
4. Record decisions and non-obvious lessons as they happen.
5. Call `irag_update` after changing files.
6. Call `irag_finish_session` before the conversation ends.

The session key belongs to that MCP process, so simultaneous Codex, Claude,
Cursor, and Windsurf sessions do not close or claim one another's diary rows.

## Safety and troubleshooting

- The server is local stdio: it opens no port and has no network transport.
- Source and memory values are treated as untrusted data. Tool results are
  encoded as JSON; the contradiction report escapes them before rendering.
- A tool failure is returned as `isError: true`; it does not corrupt the MCP
  stream or terminate the server.
- Run `irag doctor` for storage/configuration health and `irag provider` for
  the configured summary-provider executable.
- To inspect the exact server command independently of a client, send an MCP
  `initialize`, then `tools/list`; stdout will contain one JSON response per
  request and nothing else.
