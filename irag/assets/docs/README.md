# irag

**Verified relational memory for AI coding agents.** One SQLite file that
gives any agent persistent, fact-checked knowledge of your codebase —
instead of a flat `CLAUDE.md` that silently rots.

**Why you'll want this:**

- 🔌 **Pluggable memory** — one `pip install`, one `irag init`. The
  entire memory is a single SQLite file in your repo: no service, no
  cloud, no API keys, any agent on any machine can mount it.
- 🚫 **Your agent never greps again** — stop paying tokens for the same
  re-exploration every session. Search, code map, blast radius, and
  context come from SQL, not from the model reading your tree.
- 🔍 **Memory that can't quietly lie** — the only memory tool that
  fact-checks its own claims against your code, flags what's wrong, and
  fails CI while memory and code disagree.
- 💸 **Costs ~nothing to run** — reads are zero-token; writes go on
  whatever cheap or free model you point it at. Your expensive coding
  agent just reads a ~3k-token briefing.
- ⏮ **Every chat resumes where the last one ended** — a ~150-token
  recap of what previous sessions did and changed, injected
  automatically. No more "let me look around the codebase first."
- 🤝 **Works with every agent** — Claude Code hooks make it fully
  automatic; generated `AGENTS.md` reaches Codex, Antigravity, and
  Cursor; git is optional.

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

## Where this came from

I had a Database Systems assignment coming up and honestly had no idea
what to build. Around then, Andrej Karpathy put out his LLM Wiki, so I
started tinkering with it and found the issues fast: it was all just
files — nothing versioned or checked, nothing stopping it from quietly
going stale while people kept trusting it. I figured I'd try fixing
that for the assignment.

I picked a law firm as the domain because it sounded fun to build, and
it turned out to be the right kind of hard too. Law firms don't forgive
mistakes — a wrong court date or a missed conflict of interest is a
real failure, not a bad chatbot answer. Building under that pressure
got me to the actual idea: **it should be a database system with an AI
layer, not an AI system with a database attached.** Five tables did the
real work underneath — pages, revisions, links, events, contradictions
— while the database handled the bookkeeping and the AI only ever wrote
prose. I tested it by imagining the AI ripped out completely: a full
working law-firm system was still standing there.

That's when it clicked that those five tables were never about law
firms at all. They were an answer to something bigger: **how do you let
an AI write things you can actually trust?** So I stripped the law-firm
parts out and pointed the same core at coding agents — the things that
forget everything the second you close the terminal. Tools like
Graphify and claude-mem circling the same problem from different angles
felt like confirmation it was real.

The full version: [docs/STORY.md](docs/STORY.md).

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

Scoping is **directory-wise**: the folder you run `init` in *is* the
project — an enclosing git repo (a versioned home dir, a monorepo) is
never silently adopted. Projects nest and multiply, each with its own
`.irag`, its own generated `CLAUDE.md`/`AGENTS.md` (composing
hierarchically in Claude Code: global → parent dir → project), and its
own dashboard.

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

- **Map** — an interactive, Obsidian-style dependency graph: drag to
  pan, scroll to zoom, drag nodes, hover to trace imports.
- **Sessions** — the conversation log with per-file change detail.
- **Overview** — live token burn, metric cards, activity feed.
- **Health** — open contradictions with one-click resolve, staleness.
- **Chat** — auto-routes lookups to instant SQL search and questions to
  AI answers with citations.
- **Docs** — this documentation, rendered in-app.

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

**📖 Full docs site: [karang1908.github.io/iRag](https://karang1908.github.io/iRag/)**
— searchable, with a quickstart, architecture deep-dive, and full CLI
reference. Built by `site/build.py` (stdlib, zero deps) and deployed by
CI on every push. The same content lives in this repo:

- [docs/SETUP.md](docs/SETUP.md) — install, LLM configuration (incl.
  the agy/Antigravity split), hooks, CI gate
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — schema, data flows,
  retrieval scoring, design principles
- [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) — every command & flag
- [docs/COMPARISON.md](docs/COMPARISON.md) — vs Graphify & claude-mem
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) — contributing /
  non-negotiables
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
