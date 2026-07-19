# irag — Project Knowledge Base

**Relational memory for AI coding agents.** irag replaces the flat context
file (`CLAUDE.md`, `.cursorrules`, agent memory files) with a SQLite
database that lives in your repo, is fed by git, audited by a linter, and
projected back into prose on demand.

```
pip install -e ./irag     # stdlib-only core, Python >= 3.11
cd your-project && irag init   # works with or without git
```

## Why

Flat context files are Karpathy's "LLM Wiki" in miniature — and they rot
the same way: stale claims nobody notices, links to files that no longer
exist, no way to ask *when* something became true or *why* the file says
it. These are not prose problems. They are **storage problems**, and a
relational substrate solves all of them.

irag unifies the three kinds of memory a coding agent needs — and is the
only layer where memory is *verified*:

| Memory | Question it answers | How |
|---|---|---|
| Structural | "how is the code shaped?" | deterministic AST/regex scan → `irag map`, `irag impact` (zero LLM tokens) |
| Semantic | "what is true here, and why?" | LLM-synthesized module pages, versioned, **lint-verified against the code** |
| Episodic | "what did we decide and learn?" | `irag record-decision`, `irag learn` — served in every context |

irag is built on two principles:

1. **The model never does bookkeeping.** Locating, counting, dating,
   diffing, scheduling, and verifying are SQL. The model only writes prose
   and (optionally) compares prose to facts.
2. **Memory is data with a prose projection — not prose.** `CLAUDE.md`
   and `AGENTS.md` (the cross-tool convention read by Codex, Antigravity,
   and others) become generated build artifacts (`irag export`), never a
   hand-edited source of truth.

## The three layers

| Layer | What | Where |
|---|---|---|
| 1 — Ground truth | The repository itself: files, git history, manifests, tests | your repo |
| 2 — Synthesis | Per-module context pages, LLM-written, append-only versioned | `pages`, `revisions` |
| 3 — Audit | Event queue (commits → events) + contradictions table (linter output) | `events`, `contradictions` |

Memory is per-file and per-folder: every file gets its own verified
page, every folder a bottom-up rollup, the root a summary of summaries
(exclude anything with a `.iragignore`). Changes become events two ways — git commits (via hooks, when a repo
exists) or working-tree snapshots (content fingerprints, no git needed;
`[ingest].mode = auto|git|snapshot`). Either way, `irag synthesize` sends a
diff-aware prompt to your LLM (default: `claude -p`, fully pluggable) and
stores the returned page as a new revision — a SQLite trigger advances the
current-revision pointer and resets staleness, no application code
involved. `irag lint` then checks every claim it can verify (paths,
versions, symbols) against the code; a failure is not a mystery, it is **a
queryable row**.

## Quickstart

```bash
irag init                       # db + config + git hook + history sync
irag update                    # one shot: sync + new page versions + lint + export
irag ask "how does auth work"  # AI search (answers with page citations)
irag lint                       # verify claims against ground truth
irag context --open src/auth/login.py --query "auth flow"
irag export                     # regenerate CLAUDE.md + AGENTS.md
irag check                      # CI gate: exit 1 on contradictions / staleness
irag obsidian                   # render the db as an Obsidian vault (graph view)
```

## What irag has that memory layers don't

- **Contradiction linting against ground truth** — hallucinated paths,
  wrong version pins, and phantom symbols become rows in a
  `contradictions` table, with severity and provenance.
- **Full provenance** — `irag why "we use JWT"` traces any claim to the
  revision that introduced it and the commit that triggered that revision.
- **Zero-token structural queries** — staleness, search, relevance
  scoring, and time travel (`irag asof`) cost no LLM tokens.
- **Non-destructive rollback** — `irag rollback src/auth 3` re-issues the
  old body as a *new* revision; history is never rewritten.
- **A CI gate** — `irag check` fails the build when memory disagrees with
  code.
- **A deterministic code map** — `irag map` (symbols, imports, importers)
  and `irag impact` (transitive blast radius) replace agent grepping; the
  same facts ground synthesis prompts and give the linter exact symbol
  verification.
- **One-command Claude Code integration** — `irag claude-setup` installs a
  SessionStart hook so every session begins with ranked, budgeted context
  automatically, and teaches the agent (via generated CLAUDE.md) to use
  `irag map`/`impact`/`why` and to log lessons back with `irag learn`.
- **A conversation diary** — every coding session is logged as a row:
  which files changed *and the exact per-file change summary for each*
  (redundantly with `revisions`, so no join is ever needed), decisions
  made, plus an LLM narrative. `irag recap` (and every fresh session's
  injected context) answers "what was I doing?" so a new chat resumes
  instantly instead of re-exploring the codebase.
- **A live dashboard** — `irag dashboard`: real-time token burn and
  activity, health with one-click contradiction resolution, an
  interactive Obsidian-style dependency graph (pan, zoom, drag nodes,
  hover to trace imports), the session diary with per-file change detail,
  in-app docs, and a knowledge-base chat that auto-routes each message to
  SQL search or AI search.
- **Production operability** — `irag doctor` (full install diagnosis,
  CI-friendly), `irag status --json` (metrics for dashboards/benchmarks),
  `irag diff` (review what synthesis changed), `irag backup` (online
  snapshots), and `--json` on context/map/contradictions.
- **An Obsidian projection** — `irag obsidian` renders the database as a
  vault: module notes, revision chains as graph nodes, contradictions as
  warning callouts. Same data, visual view.

## Command overview (34 commands)

`init` · `claude-setup` · `doctor` — setup ·
`sync` · `ingest-commit` · `scan` — detect ·
`synthesize` · `update` · `learn` · `record-decision` · `resolve` ·
`rollback` — write ·
`search` · `map` · `impact` · `stale` · `status` · `diff` ·
`contradictions` · `asof` — read (SQL, zero tokens) ·
`ask` · `context` — read (AI) ·
`session-begin` · `session-end` · `sessions` · `recap` — conversation log ·
`pin` · `unpin` · `export` · `check` · `backup` — admin ·
`dashboard` · `obsidian` · `why` — views & provenance.
Full reference in [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md);
`irag --version` prints the installed version.

## Documentation

- [docs/SETUP.md](docs/SETUP.md) — installation, initialization, daily
  workflow, CI gate examples
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — schema, data flows,
  retrieval scoring, design principles
- [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md) — every command and flag
- [docs/COMPARISON.md](docs/COMPARISON.md) — honest comparison vs
  Graphify and claude-mem, and benchmark designs

## Roadmap

- MCP server (context/map/why/learn as tools)
- Confidence scoring updates driven by lint history
- CI webhooks (post contradictions to PRs)
- Schema migration system (today: additive column guards only)
- Real-LLM evaluation of summary quality (all automated tests use a
  deterministic mock)

## License

MIT
