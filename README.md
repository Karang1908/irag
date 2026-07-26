<p align="center">
  <img src="docs/assets/irag-banner.svg" alt="irag" width="100%">
</p>

<h3 align="center">Your agent can search your codebase for <i>zero</i> tokens.<br>Not fewer. Zero — there is no model on the read path.</h3>

<p align="center">
  <sub>No embeddings. No vector database. No model call when your agent asks what something does.<br>And <b>every claim is mechanically fact-checked against your real code</b> — so memory can't quietly lie to you.</sub>
</p>

<p align="center">
  <img src="https://github.com/Karang1908/irag/actions/workflows/ci.yml/badge.svg" alt="CI">
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/dependencies-0-brightgreen" alt="Zero dependencies">
  <img src="https://img.shields.io/badge/install-one%20command-informational" alt="One command">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="MIT">
</p>

<p align="center">
  <a href="https://karang1908.github.io/irag/"><b>Docs</b></a> ·
  <a href="https://karang1908.github.io/irag/docs/quickstart/">Quickstart</a> ·
  <a href="https://karang1908.github.io/irag/docs/architecture/">How it works</a> ·
  <a href="https://karang1908.github.io/irag/docs/comparison/">vs. alternatives</a>
</p>

```bash
pip install -e ./irag && irag init      # that's the whole setup
```

<p align="center">
  <img src="docs/assets/pages-memory.jpg" alt="irag dashboard: one page showing a file's summary, structure, and history" width="100%">
</p>

<p align="center">
  <sub><b>0 tokens</b> to search, map, or trace blast radius &nbsp;·&nbsp; <b>0</b> runtime dependencies &nbsp;·&nbsp; <b>0</b> cloud services<br>one SQLite file in your repo &nbsp;·&nbsp; writes run on whatever cheap model you point at it</sub>
</p>

---

## The problem

Your agent opens a fresh chat and knows nothing. So it greps. It opens
twelve files. It re-derives what it already worked out yesterday, and you
pay for every token of it — again tomorrow.

The usual fix is a markdown file the agent re-reads at startup. It goes
stale in a week, nobody notices, and the agent keeps confidently quoting
it.

**irag pays that cost once, writes it down, checks it against your code,
and hands it back for free.**

|  | without irag | with irag |
|---|---|---|
| Start a session | re-read the tree | **~650 tokens**, pre-ranked |
| "What does this file do?" | open and skim it | **0 tokens** — it's a row |
| "What breaks if I change it?" | trace imports by hand | **0 tokens**, 0.08s, transitive |
| "What did we decide last week?" | scroll back, or guess | **~87 tokens** |
| Memory is wrong | you find out the hard way | **CI fails** |

---

## Why it's different

**🔒 It can't quietly lie.**
Every claim is checked against your actual code. Hallucinated paths, wrong
version pins and phantom symbols become queryable rows; contradicted pages
are never served without a warning; `irag check` fails CI while memory and
code disagree. *No other memory tool does this.*

<p align="center">
  <img src="docs/assets/health-contradiction.jpg" alt="Health view: memory claimed argon2-cffi 21.1.0, the manifest declares 23.1.0 — caught mechanically, with a one-click resolve" width="100%">
</p>

<p align="center">
  <sub>A real catch: the summary claimed <code>argon2-cffi 21.1.0</code>, the manifest says <code>23.1.0</code>.<br>Nobody read the page to notice — a query did.</sub>
</p>

**⚡ Reads are free — structurally, not "cheaply".**
`search`, `map`, `impact`, `context`, `recap`, `why` are SQL and parsing.
Point irag at a model that bills per call, run all six, and its own meter
still reads `est. LLM tokens spent : 0` — because nothing on that path
can reach a model.

**🧭 It knows what breaks.**
`irag impact src/db/store.py` → every module that transitively depends on
it, parsed from the code, in under a tenth of a second.

**🕰 Git for your memory.**
Append-only revisions. `irag why "we use opaque tokens"` traces that
sentence to the commit that caused it. `irag asof 2026-06-01` time-travels.
Rollback never destroys history.

**📦 One file, zero dependencies.**
Stdlib Python. The whole memory is one SQLite file in your repo — no
service, no cloud, no keys. Move it, mount it from another machine, delete
it. It's a file.

**🔌 Works with the agent you already use.**
Claude Code hooks make it fully automatic. The installed `AGENTS.md`
reaches Codex, Antigravity and Cursor. Git is optional.

---

## Quickstart

```bash
pip install -e .            # zero dependencies, Python 3.11+
cd your-project
irag init                   # works with or without git
irag doctor --probe-llm     # verify your summariser before spending anything
irag update                 # build the memory: one call per file and folder
irag claude-setup           # wire the hooks — after this it runs itself
```

After `claude-setup` the loop is mechanical: **SessionStart** injects
ranked context plus a recap of previous sessions, **Stop** runs `irag
update` when your agent finishes a turn, **SessionEnd** writes the diary.
You never think about it again.

Then, day to day:

```bash
irag search "session expiry"     # instant, free
irag ask "how does login work?"  # AI answer, with citations
irag impact src/db/store.py      # blast radius, free
irag recap                       # "previously on this project"
irag dashboard                   # the whole thing, in a browser
```

### Point the writer at a cheap model

Reads are free; only writing summaries costs anything. Put that on a
different model from your coding agent:

```toml
# .irag/config.toml
[llm]
command = "agy --dangerously-skip-permissions -p {prompt}"   # or: claude -p
model_label = "antigravity"
```

Any CLI that takes a prompt and prints markdown works. The fact-checker is
model-agnostic, so a cheaper writer never weakens the trust layer.

---

## The dashboard

`irag dashboard` — local, zero-dependency, and a **full peer of the CLI**.
Anything you can type, you can click.

<p align="center">
  <img src="docs/assets/dashboard-overview.jpg" alt="Overview: what needs attention, token burn, live activity" width="100%">
</p>

**`⌘K` searches everything** — pages, views, actions, and the *text inside*
your summaries. Instant, zero tokens.

<p align="center">
  <img src="docs/assets/command-palette.jpg" alt="Command palette searching pages and page text" width="100%">
</p>

- **Pages** — one page is one object: summary, version history and diffs,
  the contradictions on it, and what it defines / imports / is imported by
  (clickable, so you walk the graph from where you are).
- **Overview** — what needs attention, each with the button that fixes it.
- **Health** — contradictions with one-click resolve, staleness.
- **Visualize** — your codebase in 3D: orbit it, zoom it, click a file to
  see what irag knows about it.
- **Map** — an Obsidian-style dependency graph: pan, zoom, trace imports.
- **Sessions** — every conversation with its per-file changes, and the
  verbatim transcript when capture is on.
- **Tools** — preview the exact briefing your agent gets, time-travel, and
  run every operation without a terminal.
- **Chat** — routes lookups to instant SQL and questions to AI answers.

<p align="center">
  <img src="docs/assets/dependency-graph.jpg" alt="Interactive dependency graph" width="49%">
  <img src="docs/assets/sessions.jpg" alt="Session diary with per-file changes and transcript" width="49%">
</p>

**Or see the whole thing in three dimensions.** Every node is a file, sized
by how many symbols it defines and coloured by top-level folder; every edge
is a real import. It's built from the same symbol and dependency tables the
CLI queries — your codebase, not a diagram of one — and it re-reads the map
every few seconds, so new files show up without a refresh.

<p align="center">
  <img src="docs/assets/visualize-3d.jpg" alt="Visualize: the codebase as a 3D force-directed graph, orbitable and clickable" width="100%">
</p>

---

## Measured, not claimed

Run on irag's own source — 20 modules, 5,144 lines — on a laptop:

| | |
|---|---|
| Full structural scan | **155 symbols, 70 import edges, 0.08s, 0 tokens** |
| `irag impact irag/db.py` | **13 dependent modules, 0.08s, 0 tokens** |
| Resume a past conversation | **~87 tokens** |
| Brief a fresh session | **~650 tokens** effective, budget-capped |
| Runtime dependencies | **0** |
| Static analysis | **pyflakes clean** |

The read path costs nothing because nothing on it calls a model. That's a
property of the architecture, not a benchmark you have to take on trust.

```bash
sh tests/test_smoke.sh    # the whole lifecycle, against a mock model — costs nothing
```

One command exercises init → ingest → synthesize → lint → contradiction
detection → CI gate → rollback → sessions → search/ask/recap/asof →
dashboard API → Obsidian export → directory-wise scoping → multi-language
graph → transcript capture. The mock model in the suite **deliberately
hallucinates a missing file**, so the fact-checker is proven to catch it
rather than assumed to. CI runs it on Python 3.11–3.13 on every push.

---

## Why a database, not a markdown file

Flat context files rot: stale claims nobody notices, links to files that
no longer exist, no way to ask *when* something became true or *why* the
file says it. Those aren't prose problems, they're **storage problems**.

| Memory | Question it answers | How irag does it |
|---|---|---|
| Structural | "how is the code shaped?" | AST/regex scan → `map`, `impact` — **zero tokens** |
| Semantic | "what is true here, and why?" | a versioned page per file & folder, **lint-verified** |
| Episodic | "what happened last session?" | every conversation logged with its exact per-file changes |

Two rules drive every design decision:

1. **The model never does bookkeeping.** Locating, counting, dating,
   diffing, scheduling, verifying — all SQL. The LLM only writes prose.
2. **Memory is data, read on demand.** `.irag/memory.db` is the only
   source of truth. `CLAUDE.md` is a static operator manual, not the
   memory.

## How it compares

Graphify tells you how the code is shaped. claude-mem tells you what
happened. **Neither can tell you whether what the agent believes is still
true.**

| | Graphify | claude-mem | irag |
|---|---|---|---|
| Kind of memory | structural | episodic | structural + semantic + episodic |
| Cost to read memory | free (graph query) | embedding call per query | **0 tokens — SQL, no model** |
| Can it be wrong? | rarely (a parse) | yes, silently, forever | yes — **and it detects, records and gates CI on it** |
| Verified against your code | n/a — no claims to verify | ❌ | ✅ **linter → contradictions → CI gate** |
| Storage | — | vector DB | **one SQLite file, zero deps** |
| History (`why` / `asof` / rollback) | ❌ | ❌ | ✅ |
| Cost scales with | commits (free) | conversation volume | repo churn, on whatever model you choose |

Honest full comparison, including where the others win:
[docs/COMPARISON.md](docs/COMPARISON.md).

## Where this came from

It started as a Database Systems assignment. Karpathy's LLM Wiki had just
landed and the failure mode was obvious: it was all just files — nothing
versioned, nothing checked, nothing stopping it going quietly stale while
people kept trusting it.

I picked a law firm as the domain, which turned out to be the right kind
of hard: a wrong court date is a real failure, not a bad chatbot answer.
That pressure produced the actual idea — **a database system with an AI
layer, not an AI system with a database attached**. Five tables did the
real work; the AI only ever wrote prose. I tested it by imagining the AI
ripped out entirely, and a working system was still standing.

Those five tables were never about law firms. They were an answer to
*how do you let an AI write something you can actually trust?* — so I
pointed them at coding agents, the things that forget everything the
moment you close the terminal.

The full version: [docs/STORY.md](docs/STORY.md).

## Commands (36)

`init` · `claude-setup` · `doctor` — setup ·
`sync` · `ingest-commit` · `scan` — detect ·
`synthesize` · `update` · `lint` · `learn` · `record-decision` · `resolve` ·
`rollback` — write ·
`search` · `map` · `impact` · `stale` · `status` · `diff` ·
`contradictions` · `asof` — read (SQL, zero tokens) ·
`ask` · `context` — read (AI) ·
`session-begin` · `session-end` · `sessions` · `transcript` · `recap` — diary ·
`pin` · `unpin` · `export` · `check` · `backup` — admin ·
`dashboard` · `obsidian` · `why` — views & provenance

Full reference: [docs/CLI_REFERENCE.md](docs/CLI_REFERENCE.md).

## Documentation

**📖 [karang1908.github.io/irag](https://karang1908.github.io/irag/)** —
searchable, with a quickstart, architecture deep-dive and full CLI
reference. Built by `site/build.py` (stdlib, zero deps). The same content
lives here:

- [SETUP.md](docs/SETUP.md) — install, LLM configuration, hooks, CI gate
- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — schema, data flows, retrieval scoring
- [CLI_REFERENCE.md](docs/CLI_REFERENCE.md) — every command and flag
- [COMPARISON.md](docs/COMPARISON.md) — vs Graphify & claude-mem
- [DEVELOPMENT.md](docs/DEVELOPMENT.md) — contributing
- [HANDOFF.md](docs/HANDOFF.md) — for an agent developing irag itself
- [CHANGELOG.md](CHANGELOG.md)

## Roadmap

- MCP server (context / map / why / learn as tools)
- Confidence scoring driven by lint history
- CI webhooks (post contradictions to PRs)
- Schema migration system (today: additive column guards only)
- Real-LLM evaluation of summary quality

## License

[MIT](LICENSE) · built by [Karan Garg](https://github.com/Karang1908)
