# Connect irag to any coding agent with MCP

irag includes a standard local Model Context Protocol server. It exposes the
same SQLite memory, structural map, provenance, session diary, contradiction
system, and update pipeline to every MCP-capable coding tool. There is no
Claude-specific behavior in the server and no second database per client.

```bash
irag mcp --root /absolute/path/to/your-project
```

The transport is newline-delimited JSON-RPC over stdio. irag supports both MCP
eras: legacy `initialize` negotiation from `2024-11-05` through `2025-11-25`,
and the stateless `2026-07-28` protocol with per-request metadata,
`server/discover`, typed unsupported-version errors, cacheable deterministic
tool lists, `resultType`, and response identity metadata. It writes protocol
messages only to stdout, returns JSON Schema tool definitions, and includes
both text and structured results for old and new clients.

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
| `irag_get_main_summary` | every current memory page plus health, sessions, contradictions, and audit state | no |
| `irag_audit` | defensive code/API/security/dependency report, optionally with OSV | no |
| `irag_application_proof` | inspect/save a proof profile or run loopback contracts, pages, links, controls, APIs, browser/tests, and confirmed bounded stress | no |
| `irag_web_search` | current web evidence with source URLs and retrieval time | no |
| `irag_delivery_plan` | current diff, blast radius, contract risk, mapped tests, release gates, and agent brief | no |
| `irag_team_memory` | bounded reviewable export or idempotent import of shareable project knowledge | no |
| `irag_audit_triage` | durable finding disposition, rationale, and optional acceptance expiry | no |
| `irag_experiment` | create or update a measured product or engineering experiment | no |
| `irag_watchlist` | list, create, refresh, or archive source-backed trend watchlists | no |
| `irag_learn` | durable lesson | no |
| `irag_record_decision` | durable decision | no |
| `irag_resolve_contradiction` | dismiss/reopen a proven false positive | no |
| `irag_start_session` / `irag_finish_session` | isolated conversation diary | only optional final narration |
| `irag_update` | sync → incremental scan → synthesis → lint | yes, for due pages |

Memory read tools, the local audit, and App Proof's scanners are deterministic.
App Proof may execute explicitly configured local app/test commands and optional
browser interactions, so its MCP annotation is intentionally open-world and
potentially destructive even though automatic API/stress calls remain read-only.
The `run` action accepts Quick/Full/Stress mode plus the same bounded profile as
the dashboard; Stress requires `confirm_stress: true`. `state` returns profile,
history, latest evidence, and detected test-command suggestions. Authorization
values are referenced by environment-variable name and never enter MCP results.
`irag_web_search` and
the optional OSV audit tier are explicit open-world reads; neither calls an
LLM. `irag_update` is explicitly the write path and may invoke the configured
summary provider. Every MCP mutation uses irag's cross-process repository lock,
so an update, session, triage decision, experiment, watchlist, or team import
cannot race the dashboard, CLI, git hook, or another coding agent into a
partial state.

## Recommended agent loop

1. Call `irag_start_session` once and retain its `session_key`.
2. Call `irag_get_context` with the task and currently open files before broad
   exploration.
3. Use `irag_code_map`, `irag_impact`, and `irag_trace_claim` before edits.
4. Call `irag_delivery_plan` before declaring the task ready: inspect contract
   changes, test mapping, and release gates rather than relying on a generic
   test command.
5. For a full-stack change, call `irag_application_proof` with `action: run`
   against the loopback app and hand any failed/blocked/untested packet back to
   the coding agent.
6. Record decisions and non-obvious lessons as they happen. Modern stateless
   clients pass `session_key` to these calls.
7. Call `irag_update` after changing files, with the same `session_key` on a
   modern stateless connection.
8. Call `irag_finish_session` with that key before the conversation ends.

Legacy clients may rely on one MCP process retaining the active key. Modern
`2026-07-28` clients carry the returned key explicitly, so requests may land on
different server instances without closing or claiming another agent's diary
rows.

## Safety and troubleshooting

- The server is local stdio: it opens no port and has no network transport.
- Source and memory values are treated as untrusted data. Tool results are
  encoded as JSON; the contradiction report escapes them before rendering.
- Each newline-delimited request is capped at 2 MB and an oversized frame is
  drained before the next request, so one malformed client message cannot
  desynchronize or grow the local server without bound. Team bundles have
  tighter record, field, and total-size limits inside that envelope.
- A tool failure is returned as `isError: true`; it does not corrupt the MCP
  stream or terminate the server.
- Run `irag doctor` for storage/configuration health and `irag provider` for
  the configured summary-provider executable.
- To inspect the exact server command independently of a client, send an MCP
  `initialize`, then `tools/list`; stdout will contain one JSON response per
  request and nothing else.
