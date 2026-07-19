# irag vs Graphify vs claude-mem

Three tools, three different definitions of "memory." This is an honest
comparison, including where the others win.

## What each tool fundamentally is

| | **Graphify** | **claude-mem** | **irag** |
|---|---|---|---|
| Kind of memory | Structural — how the code is shaped | Episodic — what happened in past sessions | All three: structural + semantic + episodic, with verification |
| Source of truth | AST (tree-sitter parse of the code) | Session transcripts / tool observations | Git commits + code, checked against each other |
| Written by | Deterministic parser | LLM compression of conversations | Parser (structure) + LLM (pages) + human/agent (logs) |
| Can it be wrong? | Rarely (it's a parse) | Yes, silently, forever | Yes — and that's the point: wrongness is detected, recorded, and gated |

## Feature matrix

| Capability | Graphify | claude-mem | irag |
|---|---|---|---|
| Code map (symbols, imports) | ✅ deep (tree-sitter, communities, multi-modal) | ❌ | ✅ (`map`, ast/regex, lighter than Graphify) |
| Impact / blast-radius query | ✅ via graph queries | ❌ | ✅ `impact` (transitive, hop-ranked) |
| LLM-written semantic pages ("what/why") | ❌ | partially (compressed observations) | ✅ versioned, append-only |
| **Verification against the code** | n/a (no claims to verify) | ❌ | ✅ linter → contradictions table |
| **CI gate on memory correctness** | ❌ | ❌ | ✅ `check` exits 1 |
| Staleness tracking / scheduled refresh | rebuild on commit | ❌ | ✅ scored per commit, threshold-driven |
| Session lessons / decisions | ❌ | ✅ automatic capture | ✅ explicit (`learn`, `record-decision`) — see trade-off below |
| Provenance ("why does memory say X?") | graph paths | observation citations | ✅ claim → revision → commit (`why`) |
| Time travel (`asof`) / diff / rollback | ❌ | ❌ | ✅ |
| Token-budgeted, ranked serving | agent queries graph | injects recent context | ✅ tiered FULL/DIGEST/INDEX under budget |
| Claude Code session hook | via setups | ✅ | ✅ `claude-setup` (one command) |
| Obsidian projection | ✅ (in popular setups) | viewer app | ✅ built-in, with health colors + version chains |
| Multi-IDE / ecosystem maturity | ✅ large community, many integrations | ✅ 46K+ stars, Cursor/Gemini/Windsurf, cross-machine sync | ❌ single-user, new |
| Dependencies / infra | tree-sitter toolchain | npm install, worker service, hooks | ✅ zero deps, stdlib Python, one SQLite file |
| Benchmarkable output | graph exports | web viewer / API | ✅ `--json` on context/map/status/contradictions |

## Where each one wins

**Graphify wins on structural depth.** Tree-sitter parsing across many
languages, community detection, multi-modal inputs (docs, PDFs, images).
irag's `map` covers the 80% case (symbols, imports, impact) in four
languages with zero dependencies, and — unlike a standalone graph — feeds
the same facts into its linter, prompts, and ranking. If you need deep
graph queries over a 20-language monorepo, run Graphify alongside; they
don't conflict.

**claude-mem wins on automatic capture.** It records session knowledge
without anyone asking — the debugging journey, the dead ends. irag's
episodic layer is deliberate instead: the agent or human runs `irag learn`
/ `record-decision` (the generated CLAUDE.md instructs agents to do so).
Deliberate capture means less noise and full provenance, but things nobody
thought to record are lost. Automatic transcript capture is on irag's
roadmap; claude-mem has it today, polished, across many IDEs.

**irag wins on truth.** It is the only one of the three where memory is
*falsifiable*: semantic claims are mechanically checked against the
filesystem, manifests, and the symbol table; failures become queryable
contradiction rows with severity; contradicted pages are never served
without a warning; fixed claims auto-resolve; and `irag check` fails the
build while memory and code disagree. It is also the only one with
history as a first-class object — `why`, `asof`, `diff`, non-destructive
`rollback` — and the only one whose entire state is a single SQLite file
you can query, back up (`irag backup`), and audit (`irag doctor`).

## The two-sentence summary

Graphify tells you how the code is shaped; claude-mem tells you what
happened; neither can tell you whether what the agent *believes* is still
true. irag's bet is that for a coding agent, **verified beats remembered**
— and it bundles a good-enough map and a deliberate session log around
that guarantee, in one dependency-free binary.

## Benchmark suggestions (irag vs the field)

1. **Factual-error rate over churn** — let a repo evolve for 2 weeks;
   count agent references to nonexistent files/symbols/versions with each
   memory system. (`irag contradictions --json` gives you the ground-truth
   detector for free.)
2. **Tokens per task** — SessionStart hook + `map` queries vs. agent
   exploration vs. full-file context dumps (`irag status --json` tracks
   `est_tokens_spent`).
3. **Decision persistence** — ask the agent about a decision made N
   sessions ago; measure recall with/without the boosted logs.
