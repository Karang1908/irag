# Quickstart

irag is a verified, shared memory for your codebase: every file and folder
gets an AI-written summary page, versioned on every change, fact-checked
against the code, and served through the same MCP tools to every coding agent.

## 1. Install (once)

```
pip install -e ./irag      # Python 3.11+, zero dependencies
```

## 2. Turn it on in a project

```
cd my-project
irag init                  # works with or without git
```

Then choose the summary writer in `.irag/config.toml`. Built-in adapters support
Claude, Codex, agy, and Ollama; `custom` accepts any prompt-in/text-out CLI.
Verify it with `irag provider` and `irag doctor --probe-llm`.

## 3. Connect any coding agent

Run irag as a standard, dependency-free MCP server:

```
irag mcp --root /absolute/path/to/my-project
```

For Codex: `codex mcp add irag -- irag mcp --root /absolute/path/to/my-project`.
For Claude Code: `claude mcp add --scope project irag -- irag mcp --root /absolute/path/to/my-project`.
Cursor, Windsurf, VS Code, and other MCP clients use the same command. All of
them see the same 21 tools, schemas, results, sessions, audits, delivery plans,
experiments, watchlists, team-memory exchange, live-web search, and SQLite
memory.

## 4. The one command to remember

```
irag update
```

Detects every change → writes a new version of each affected file page →
rolls up folder pages bottom-up → fact-checks everything. It updates the
database only; the `AGENTS.md`/`CLAUDE.md` agent guide is installed once
by `irag init` and tells agents to run it after edits.

## 5. Ask things

- **SQL search** (instant, free): `irag search "auth token"` or type
  keywords / a file path in the dashboard chat.
- **AI search** (reasoned, cited): `irag ask "how does login work?"` or
  ask a question in the dashboard chat — routing is automatic.

## 6. See it

- `irag dashboard` — this UI: live token burn and activity, health,
  the complete Main Summary, deep code/API/security audit, live-trend Thinking
  Studio, current-diff Delivery and release gates, provider controls,
  interactive dependency graph (pan / zoom / drag),
  session diary with per-file changes, chat, and these docs.
- Every contradiction has an **Agent brief** button. It opens a self-contained,
  source-grounded HTML repair packet in a new tab; the master button bundles
  every open contradiction for one coding-agent handoff.
- Audit findings keep explicit triage/rationale and export as SARIF; source-backed
  watchlists show trend changes, while experiments keep hypotheses tied to a
  metric and outcome. Delivery exports only deliberately shareable team memory.
- `irag obsidian` — the knowledge graph as an Obsidian vault
  (files green, folders blue, contradictions red, history gray).

## Coming back after a break

```
irag recap        # "previously on this project" — the conversation diary
irag sessions     # every logged conversation with what it changed
```

Fresh chats get the recap injected automatically, so resuming costs ~zero
tokens.

## Everyday commands

`irag status` · `irag stale` · `irag map <path>` · `irag impact <path>` ·
`irag why "<claim>"` · `irag diff <file>` · `irag contradictions` ·
`irag contradiction-report [id]` · `irag mcp` · `irag provider` ·
`irag audit` · `irag web-search "current developer trends"` ·
`irag learn "<gotcha>"` · `irag record-decision "<choice>"` ·
`irag claude-setup` · `irag doctor` · `irag backup`

Not sure what to do next? **`irag suggest`** prints what needs attention
right now with the command that fixes it.

## Writing down what you learn

Summaries are written for you. These four are the things only a person or
an agent in the moment can know:

```bash
irag learn "argon2 rehash happens in place on verify" --module auth.py
irag tried "position:sticky" --because "only catches after you scroll past"
irag topic "root privilege" --files scanner.py,net.py,ui.html
irag verify "scan aborts without root" \
  --cmd "python3 -m scanner --probe" --expect QUITTING --module scanner.py
```

- **`learn`** — a gotcha worth keeping.
- **`tried`** — a dead end, so nobody re-derives it. What you ruled out is
  worth as much as what worked.
- **`topic`** — a page for a *concept* that spans several files, when the
  thing you need to know doesn't live in one folder.
- **`verify`** — a claim that carries its own proof. Everything else
  describes what the code *says*; this records what it *does*, and the
  command can be re-run to check the claim still holds. (It is not re-run
  automatically — see `fail_on_facts` in the Setup guide.)

**`irag brief <file>`** prints everything known about one file — disputed
claims, dead ends, gotchas, who imports it. After `irag claude-setup` it
fires automatically just before your agent edits a file.
