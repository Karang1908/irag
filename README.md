# irag

**Verified relational memory for AI coding agents.** One SQLite file that
gives any agent persistent, fact-checked knowledge of your codebase —
instead of a flat `CLAUDE.md` that silently rots.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![Zero dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)
![Version](https://img.shields.io/badge/version-4.1.2-purple)

```
Claude Code   →     irag      →   agy / any LLM CLI
the operator      the memory        the scribe
(reads free)    (never thinks)   (writes on a cheap quota)
```

Your expensive coding agent never spends tokens exploring or summarizing.
It reads memory through zero-LLM SQL queries; a *separate, cheap* model
writes the summaries; and a deterministic linter fact-checks every claim
against the actual code — so what the agent reads is **verified, not just
remembered**.

![Dashboard overview](docs/assets/dashboard-overview.jpg)

## Why

Flat context files (`CLAUDE.md`, `.cursorrules`, memory files) rot:
stale claims nobody notices, links to files that no longer exist, no way
to ask *when* something became true or *why* the file says it. These are
not prose problems — they are **storage problems**, and a relational
substrate solves all of them:

| Memory | Question it answers | How irag does it |
|---|---|---|
| Structural | "how is the code shaped?" | deterministic AST/regex scan → `irag map`, `irag impact` — **zero LLM tokens** |
| Semantic | "what is true here, and why?" | LLM-written page per file & folder, append-only versioned, **lint-verified against the code** |
| Episodic | "what happened in past sessions?" | every agent conversation logged with the exact per-file changes it made |

Two principles drive every design decision:

1. **The model never does bookkeeping.** Locating, counting, dating,
   diffing, scheduling, verifying — all SQL. The LLM only writes prose.
2. **Memory is data with a prose projection.** `CLAUDE.md` and
   `AGENTS.md` are generated build artifacts (`irag export`), never
   hand-edited sources of truth. The database is the only truth.

## The token economics (read this)

irag's costs are asymmetric by design:

- **Read path — free.** `context`, `search`, `map`, `impact`, `recap`,
  `why` are pure SQL. A fresh session starts with a ~3k-token injected
  briefing instead of 20–100k tokens of grep-and-read exploration.
- **Write path — pluggable.** Summaries are written by whatever CLI you
  configure in `.irag/config.toml`. Point it at a **cheap or free
  model** and the bookkeeping stops competing with your coding agent's
  quota entirely:

```toml
# .irag/config.toml — the write path runs on Gemini's free quota
[llm]
command = "agy --dangerously-skip-permissions -p {prompt}"
model_label = "antigravity"
timeout = 600
```

Any CLI with the same contract works — `claude -p` (default), a local
model behind a script, anything that takes a prompt and prints markdown
(`{prompt}` argv / `{promptfile}` / stdin delivery all supported). The
linter that fact-checks the output is model-agnostic, so a cheaper
scribe never weakens the trust layer. Verify your command once with
`irag doctor --probe-llm` before the first big run.

## Quickstart

```bash
pip install -e .            # zero dependencies, Python 3.11+
cd your-project
irag init                   # works with or without git
irag doctor --probe-llm     # verify the LLM command works
irag update                 # first full synthesis (one call per file+folder)
irag claude-setup           # wire Claude Code hooks (optional, recommended)
```

After `claude-setup` the loop is fully automatic: **SessionStart** opens
the conversation log and injects ranked context + a recap of previous
sessions, **Stop** runs `irag update` after every turn, **SessionEnd**
writes the diary entry. Other agents (Cursor, Antigravity IDE, Codex)
get the same instructions through the generated `AGENTS.md` and log
sessions manually with `irag session-begin` / `session-end`.

Daily driver commands:

```bash
irag update                     # sync → synthesize → fact-check → export
irag search "auth token"        # SQL full-text search (instant, free)
irag ask "how does login work?" # AI answer with page citations
irag recap                      # "previously on this project"
irag check                      # CI gate: exit 1 if memory disagrees with code
```

## What makes it different

- **Contradiction linting against ground truth** — hallucinated paths,
  wrong version pins, phantom symbols become queryable rows with
  severity; contradicted pages are never served without a warning;
  fixed claims auto-resolve; `irag check` fails CI while memory and
  code disagree. *No other memory tool has this.*
- **Full provenance** — `irag why "we use JWT"` traces any claim to the
  revision that introduced it and the commit that triggered it.
  `irag asof 2026-06-01` time-travels; `irag rollback` is
  non-destructive (history is never rewritten).
- **A conversation diary with receipts** — every session is a row:
  which files changed *and the exact per-file change summary for each*,
  decisions, lessons, plus an LLM narrative. A brand-new chat resumes
  for ~150 tokens.
- **A deterministic code map** — symbols, imports, transitive blast
  radius (`irag impact`), parsed from the code, always current, zero
  tokens.
- **Everything is one SQLite file** — `.irag/memory.db`. Query it, back
  it up (`irag backup`), mount it from any machine, audit it
  (`irag doctor`).

## The dashboard

`irag dashboard` — a local, zero-dependency web UI:

![Interactive dependency graph](docs/assets/dependency-graph.jpg)

- **Map** — an interactive, Obsidian-style dependency graph: drag to
  pan, scroll to zoom, drag nodes, hover to trace imports.
- **Sessions** — the conversation log with per-file change detail.
- **Overview** — live token burn, metric cards, activity feed.
- **Health** — open contradictions with one-click resolve, staleness.
- **Chat** — auto-routes lookups to instant SQL search and questions to
  AI answers with citations.
- **Docs** — this documentation, rendered in-app.

![Session diary](docs/assets/sessions.jpg)

## How it compares

| | Graphify | claude-mem | irag |
|---|---|---|---|
| Kind of memory | structural | episodic | structural + semantic + episodic |
| Can it be wrong? | rarely (a parse) | yes, silently, forever | yes — **and it detects, records, and gates on it** |
| Cost scales with | commits (free) | conversation volume | repo churn — on whatever cheap model you configure |

Full honest comparison (including where the others win):
[docs/COMPARISON.md](docs/COMPARISON.md).

## Commands (34)

`init` · `claude-setup` · `doctor` — setup ·
`sync` · `ingest-commit` · `scan` — detect ·
`synthesize` · `update` · `learn` · `record-decision` · `resolve` ·
`rollback` — write ·
`search` · `map` · `impact` · `stale` · `status` · `diff` ·
`contradictions` · `asof` — read (SQL, zero tokens) ·
`ask` · `context` — read (AI) ·
`session-begin` · `session-end` · `sessions` · `recap` — diary ·
`pin` · `unpin` · `export` · `check` · `backup` — admin ·
`dashboard` · `obsidian` · `why` — views & provenance

Full reference: [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md).

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — install, LLM configuration (incl.
  the agy/Antigravity split), hooks, CI gate
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — schema, data flows,
  retrieval scoring, design principles
- [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) — every command & flag
- [docs/COMPARISON.md](docs/COMPARISON.md) — vs Graphify & claude-mem
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — contributing /
  non-negotiables
- [docs/HANDOFF.md](docs/HANDOFF.md) — for an AI agent picking up
  *development on irag itself*
- [CHANGELOG.md](CHANGELOG.md)

## Testing

```bash
sh tests/test_smoke.sh    # end-to-end against a deterministic mock LLM — no tokens
```

CI runs the same suite plus pyflakes and a package build on every push
(Python 3.11–3.13).

## Roadmap

- MCP server (context / map / why / learn as tools)
- Confidence scoring driven by lint history
- CI webhooks (post contradictions to PRs)
- Schema migration system (today: additive column guards only)
- Real-LLM evaluation of summary quality

## License

[MIT](LICENSE)
