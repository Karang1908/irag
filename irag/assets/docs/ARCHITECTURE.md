# irag — Architecture

## Lineage

irag generalizes a pattern first built as a relational re-implementation of
Karpathy's "LLM Wiki" (April 2026): where the original stored LLM-written
wiki pages as markdown files, the relational version stored them as rows —
and every integrity flaw of the file-based approach (link rot, no
structured query, no provenance, no access control, unobservable
hallucination) turned out to be a **storage problem** the database solves.
irag applies the same substrate to the memory files AI coding agents rely
on.

## Principles

1. **The model never does bookkeeping.** Locating, counting, dating,
   diffing, scheduling, verifying — SQL. The model writes prose, and
   optionally compares prose to facts. Nothing else.
2. **Memory is data; prose is on demand — not a file dump.** The memory
   lives in `.irag/memory.db`. `CLAUDE.md` (irag's operator manual,
   installed by `irag init`) tells the agent to read it via
   `irag context` / `recap` / `search`.

## The three layers

```
Layer 1  GROUND TRUTH   the repository: files, git, manifests, tests
            │  post-commit hook / irag sync
            ▼
Layer 3  AUDIT          events (queue)  ──►  contradictions (linter output)
            │  irag synthesize                       ▲
            ▼                                       │ irag lint
Layer 2  SYNTHESIS      pages ──► revisions (append-only, versioned)
            │
            ▼
         irag context / irag export / irag obsidian
```

## Schema (SQLite, `.irag/memory.db`, WAL)

Core relations plus an FTS5 inverted index:

- **meta** — key/value (e.g. `last_synced` commit hash,
  `last_scanned_head`, `snap_seq`)
- **pages** — one row per subject (file, folder, or log page):
  `subject_id` (e.g. `src/auth/login.py`, `src/auth`, `.`),
  `current_revision_id`, `staleness_score`, `pinned`, `confidence`
- **revisions** — append-only page bodies: `version_number`,
  `body_markdown`, `change_summary`, `triggered_by_event_id`,
  `llm_model_used`, `tokens_used`
- **links** — page-to-page edges; `imports` rows rebuilt wholesale by
  the structural scan, `related` rows are manual/permanent
- **events** — the audit queue: `event_type`
  (commit/snapshot/decision/session/rollback/…), `source_ref`
  (commit hash), `subject_id`, JSON `payload`, `status`
  (queued→processing→completed/failed)
- **contradictions** — linter output: `claim`, `truth`, `ctype`
  (missing_path / version_mismatch / missing_symbol / llm_flagged),
  `severity`, `detected_by`, `resolved_at`
- **sessions** — the conversation diary: one row per agent conversation
  with ID high-water marks (`start_event_id`/`start_revision_id` — not
  timestamps, so same-second sessions can't steal each other's rows),
  summary, and `changes_detail` (the full per-file
  `{subject_id, version_number, change_summary}` list, deliberately
  redundant with `revisions` so reading a session never needs a join), plus
  task, branch, worktree, and starting commit identity
- **session_messages** — the opt-in verbatim transcript
  (`[sessions].capture_transcript`): user/assistant messages per session,
  so the diary can hold the actual conversation, not only its file
  changes. Off by default (transcripts can carry secrets)
- **symbols / deps** — the deterministic structural map, rebuilt by the
  scanner, gated on a working-tree content fingerprint (not git HEAD,
  so uncommitted edits are seen)
- **scan_state** — per-source parser fingerprints. Content-only refreshes
  replace symbols and outgoing edges only for changed files
- **tree_state** — path→sha1 fingerprints for snapshot-mode ingestion
  and scan gating
- **schema_migrations** — ordered applied-version history. An existing
  database is backed up before upgrade and a newer schema is refused on
  downgrade
- **jobs** — durable dashboard operations with ID, state, progress, ordered
  log, timestamps, and terminal error; streamed to the browser through SSE
- **llm_runs** — provider/model, token estimates, latency, retry count,
  status, and optional user-rate-based cost for every model invocation
- **audit_runs** — immutable defensive-scan snapshots: source fingerprint,
  coverage, counts, duration, and the complete redacted JSON report
- **audit_triage** — stable finding ID → disposition, rationale, optional
  expiry, and reviewer timestamp; expired acceptances reopen automatically
- **studio_messages** — durable user/assistant Thinking Studio messages with
  mode and the exact source records attached to each answer
- **studio_ideas** — persistent recommendation cards with mode, markdown body,
  source records, and active/archived state
- **experiments** — hypothesis, metric, target, status, observed outcome, and
  decision for turning recommendations into measurable learning
- **watchlists / watchlist_snapshots** — explicit live-search questions plus
  immutable provider/URL/retrieval-time snapshots and added/removed sources
- **memory_imports** — stable logical record IDs and content hashes used to
  make team-memory imports idempotent while allowing later outcomes to merge
- **proof_profiles** — the single bounded, loopback-only App Proof profile for
  a project; it stores an authorization environment-variable name, never its
  value
- **proof_runs** — durable Quick/Full/Stress status, profile snapshot, source
  fingerprint, bounded JSON evidence, failure, timing, and optional dashboard
  job linkage
- **facts** — executable memory: a `claim` plus the `cmd` that
  demonstrates it, an expected substring and/or exit code, and the last
  run's status. The only relation whose contents irag can *re-establish*
  rather than trust. Because these are shell commands in a database that
  travels with the repo, nothing executes them unless
  `[check].fail_on_facts` is explicitly enabled.
- **topic_members** — `(topic, subject_id)` for concept pages. Knowledge
  is feature-shaped while every other page is file- or folder-shaped;
  membership is curated by hand, the one page type irag will not infer.
- **revisions_fts** — FTS5 external-content table over `body_markdown`,
  kept in sync by triggers

`pages` also carries **content_fingerprint**, a hash of the file with
comments and blank lines stripped, written at synthesis. Comparing against
it lets a comment-only edit skip the model entirely.
`contradictions.resolution_kind` distinguishes a human dismissal (which
suppresses that claim permanently) from an auto-resolution (which must be
free to re-raise, since a recurrence is a real regression).

### The signature trigger

```sql
CREATE TRIGGER revisions_advance AFTER INSERT ON revisions BEGIN
  UPDATE pages
     SET current_revision_id = new.revision_id,
         last_updated_at     = datetime('now'),
         staleness_score     = 0
   WHERE page_id = new.page_id;
END;
```

Inserting a revision *is* publishing it. No application code moves
pointers or resets staleness — the invariant lives in the database.

### The structural map (Layer 1½)

`irag.structure.scan` parses source into two relations — ``symbols`` (name,
kind, file:line per module) and ``deps`` (module→module import edges,
counted) — Python via `ast`, and JS/TS, Go, Rust, Java, C#, Ruby, PHP,
and C/C++ via regex, rebuilt only when the working-tree fingerprint changes
(falling back to Git HEAD before fingerprints exist). Import edges resolve
for file-relative imports (Ruby `require_relative`, C `#include "..."`,
relative JS/PHP); Java and C# yield symbols but not edges (package-path
imports need source roots). It is pure parsing: deterministic, local,
free. The map is
load-bearing everywhere: `map`/`impact` answer structural questions
without token spend; synthesis prompts embed the facts as ground truth
(fewer hallucinations at the source); the linter verifies symbol claims
exactly against it; retrieval boosts dependency neighbors (+20) and
attaches a map block to every FULL page; dep edges project into `links`
(retrieval link-hop + Obsidian graph edges).

The initial scan and any add/delete/rename topology change parse the complete
source set. A content-only edit hashes the same set, reparses only changed
files, replaces only their symbols and outgoing dependency edges, then
reprojects import links transactionally. Snapshot and Git rename detection
move the existing page identity to the new subject, preserving its revision
and contradiction lineage.

### The agent protocol layer

`irag.mcp` is a newline-delimited JSON-RPC stdio server that maps standard MCP
tool calls onto the same library functions used by the CLI and dashboard. It
supports the legacy 2024–2025 handshake era and stateless `2026-07-28`
discovery/per-request metadata era, JSON Schema tool discovery, annotations,
and both text and structured results. There is no client-specific branch:
every MCP client receives the same 22 tool names and operates on the same SQLite
file. Every mutation uses the repository lock as well as its database
transaction. Legacy processes retain a diary key for
convenience; modern clients carry the explicit key returned by
`irag_start_session`, so attribution does not depend on process affinity.

The server publishes the same complete-project summary, defensive audit,
delivery plan, team-memory exchange, experiments, watchlists, and
source-preserving live web search used by the dashboard. Web search and OSV are
annotated open-world reads; the memory tools remain local and deterministic.

`irag_application_proof` exposes the same proof profile, runner, histories, and
agent brief as the CLI/dashboard. MCP calls serialize through a dedicated
cross-process proof lock; repository refresh/audit briefly takes the normal
update lock, while the longer loopback/browser/test phase does not freeze memory
reads or unrelated work.

### Developer intelligence surfaces

The **Main Summary** is a deterministic join over all live current revisions,
status, contradictions, recent sessions, and the latest audit. Its explained
Memory Trust signal scores coverage, currentness, traceability, session ledgers,
contradictions, and effective open audit severity/freshness without claiming
prose is proven. Structural
fingerprint and drift refresh at read time; stale prose is labeled rather than
silently rewritten through an LLM.

The **Code Audit** walks a bounded, ignore-aware source set. It performs AST
checks where available, conservative text checks elsewhere, route discovery,
dependency-cycle analysis, lockfile parsing, and optional batched OSV queries.
Evidence is redacted before report persistence. Its live API checker accepts
only loopback hosts, discovered parameter-free read routes, bounded concurrency,
short timeouts, and no redirects. Finding IDs deliberately exclude volatile
line numbers, retaining review decisions through harmless line shifts;
rationales are required for non-open states, accepted risks may expire, and the
effective open set exports as SARIF 2.1.0. SARIF and live-probe endpoints first
refresh structural drift and reject stale or missing audit inventories.

The **App Proof** layer composes Code Audit's API route inventory with a separate
frontend call/control scanner, a no-redirect loopback crawler, link/form/control
inventory, parameter-free GET/HEAD probes, an optional packaged Node runner that
loads the project's Playwright (with a Python Playwright fallback), explicit project test commands, and bounded
read-only stress. All shell-like text is parsed to an argument vector and run
without a shell. Started applications and timed-out commands own process groups
so children are terminated too. Reports prioritize failures and unknowns under
storage bounds and mark any dropped discovery as blocked. Static wiring,
protected routes, ambiguous controls, forms, and omitted suites never become
pass evidence.

The **Thinking Studio** retrieves project memory and latest audit state, adds
the current Git diff for code-review mode, and optionally adds live search
results. All evidence blocks are explicitly treated as untrusted data in the
model prompt. Repo paths and `[W#]` web citations use separate namespaces;
failed research is surfaced rather than replaced with a claim of recency.
Its experiment ledger attaches metrics and decisions to recommendations;
watchlists retain immutable live-search snapshots and compute source changes.

The **Delivery** workspace is a deterministic projection of a validated Git
base (or snapshot-mode drift): changed paths, transitive impact, removed public
symbols/routes, path risk, related tests, repository-native verification
commands, release gates, and a copyable agent brief. It plans commands but never
executes them. Repository-controlled paths and extracted contracts are marked as
untrusted evidence and single-line escaped in the agent handoff. Reviewable
team-memory JSON moves explicit decisions, lessons,
topics, experiments, watchlists, and audit triage across worktrees while
excluding code, transcripts, prompts, narratives, and executable facts.

## Data flows

### Granularity: files and folders

Subjects exist at two levels. Every tracked file has a `file` page;
every folder (including the root `.`) has a `folder` page that rolls up
its direct children. Change events target file pages and bump ancestor
folders at half weight; synthesis runs file pages first, then folders
deepest-first, so each folder prompt reads fresh child summaries — the
root page is a summary of summaries. Child evidence is section-balanced and
bounded rather than prefix-truncated, so late invariants, failure/security
behavior, connections, and recent changes remain available to the parent.
Ignoring: [modules].ignore segments,
`.iragignore` patterns (names/prefixes/globs), hidden paths, and binary
extensions.

### Ingestion (Layer 1 → Layer 3)

Two triggers, one queue. **Git mode:** `git show --name-only` per commit.
**Snapshot mode** (plain folders, or forced via config): every file is
content-hashed (SHA-1, streamed across the entire file) into `tree_state`; sync diffs current
vs stored fingerprints and emits per-module `snapshot` events (with
add/edit/delete lists) under a monotonic `snap:N` ref. The page revision
chain (v1, v2, ...) is the memory's own version control either way — the
trigger only decides *when* a new version is warranted.

`git show --name-only` on each commit; every changed **file** is its own
subject (no grouping — a file's page is about that file), and each of its
ancestor folders (up to the root `"."`) is bumped at half weight so
rollup pages refresh too. Per touched file: one queued `commit` event
(payload: files + message) and a staleness bump (+10 per commit, +20 extra
when a dependency manifest such as `package.json` or `requirements.txt` is
among the files). irag's own artifacts (`CLAUDE.md`, `AGENTS.md`, `irag_vault/`) never generate events,
preventing an export→commit→staleness feedback loop. Ingestion is
idempotent on `(source_ref, subject_id)`,
so the hook and `irag sync` can overlap safely. `sync` walks
`git rev-list --reverse last..HEAD` and records the new head in `meta`.

### Synthesis (Layer 3 → Layer 2)

A page is *pending* when it is not pinned and either has never been
synthesized but has queued events, or its staleness score has reached the
threshold.

A **file** prompt carries: structural facts (symbols/imports, labelled
ground truth), the parsed blast radius, commit messages, the **git diff
of what actually changed** since the last synthesis (capped 4000 chars,
spanning the queued commits — or the working tree when the edit isn't
committed), the file's current content (capped 8000 chars), and the
current page body ("none — write the first version"). The diff is why
"## Recent changes" can describe the *change* rather than restate the
file. In snapshot mode (no git toplevel) the diff is simply absent and
everything else still works. A **folder** prompt rolls up its direct
children's summaries instead.

Every page ends with a `CHANGE-SUMMARY:` line, which is stripped from the
body and stored as the revision's `change_summary` — so `irag sessions`
and the dashboard show what a conversation really changed. If the model
omits it, a generic fallback is used.

The configured command receives the prompt on stdin and must return the
full updated markdown on stdout. Queued events move to `processing`, then
`completed` (or `failed`); the new revision records which event triggered
it and which model wrote it.

### Linting (Layer 2 vs Layer 1)

The static tier is deterministic and free:

| Check | Extraction | Verified against | ctype / severity |
|---|---|---|---|
| Paths | backticked path-like tokens with code/doc extensions | filesystem | `missing_path` / high |
| Versions | `name@X.Y.Z` and "name version X.Y.Z" | package.json (deps+dev), requirements.txt (`==`) | `version_mismatch` / medium |
| Symbols | backticked `identifier()` | exact lookup in the `symbols` table (repo-wide); grep fallback when no scan data | `missing_symbol` / low |

Identical open claims are deduped; static claims that stop failing are
auto-resolved on the next lint. The optional LLM tier
(`irag lint --llm`) audits each page against a file listing and records
`llm_flagged` rows. Resolution is explicit: `irag resolve <id> --notes`.

### Retrieval scoring (zero LLM tokens)

Per page:

| Signal | Weight |
|---|---|
| module matches an open file's module | +50 (parent/child +25) |
| FTS hit for the query | up to +30 (rank-decayed) |
| one link-hop from a top-3 seed | +15 |
| updated within 7 days | +10 |
| staleness over threshold | −20 |
| open contradictions | −40 + warning banner |

### Serving (tiered, budget-gated)

Pages under `min_score` are relegated to the index. Within the token
budget (`token_budget` ≈ chars/4): **FULL** — top pages (max `full_max`),
complete bodies, each headed `### title (vN, updated date)`; **DIGEST** —
change summary or first 200 chars; **INDEX** — one line each with score
and staleness. A page with open contradictions is *never* served without a
warning banner. `serve()` also returns a machine-readable dict for
programmatic consumers.

### Session knowledge (episodic layer)

`irag learn` and `irag record-decision` are deterministic appends (event +
new revision on the `lessons`/`decisions` pages) — zero tokens, full
provenance like everything else. Both page types carry a +25 relevance
boost so they surface in every context serve: decisions stop being
re-litigated, gotchas stop being re-discovered. The `irag claude-setup`
integration teaches agents to call these themselves.

The conversation diary sits on top: `session-begin` opens a row and
records ID high-water marks; `session-end` closes it with a
deterministic digest of everything the window produced (files, page
versions with their per-file change summaries, decisions, lessons,
commit messages), optionally re-narrated by the LLM into 2–5 sentences
(deterministic fallback if the LLM is unavailable). `recap_block()`
renders the last N sessions as markdown and is injected at the top of
every `irag context` — this is why a brand-new chat resumes for ~150
tokens instead of re-exploring.

### Provenance & time travel

- `why CLAIM` — FTS-locate the best revision, print page/version/author
  and the triggering event chain (commit hash, message, files) or
  "human/rollback".
- `asof DATE` — per page, the latest revision at that date; `--show`
  prints a full body. Append-only storage makes this a single query.
- `rollback SUBJECT N` — inserts the v*N* body as a **new** revision via a
  `rollback` event. History is immutable.
- `record-decision` — deterministic append to a `decisions` page (no
  LLM), backed by a `decision` event.

### The Obsidian projection

`irag obsidian` is a prose projection of the memory database:
one markdown note per page, one per revision, wikilinked so Obsidian's
graph renders the knowledge structure — module map when versions are
filtered out (`-tag:#version`), full history chains when not. Health is
visible at a glance: `#contradicted` pages carry warning callouts,
`#stale` pages are flagged in frontmatter tags. The vault is wiped and
rebuilt each run behind a marker-file guard; the database remains the
sole source of truth.

### The CI gate

`irag check` prints open-contradiction count, over-max-staleness pages, and
never-synthesized pages; exits 1 per config. Memory that disagrees with
code fails the build.

## Package layout

```
irag/
├── irag/
│   ├── db.py           schema, triggers, shared helpers
│   ├── structure.py    deterministic code map (symbols, deps, impact)
│   ├── config.py       defaults + TOML deep-merge
│   ├── ingest.py       git commits / snapshots → events
│   ├── synthesis.py    events → revisions (LLM, fixpoint folder sweep)
│   ├── linter.py       revisions vs ground truth → contradictions
│   ├── retrieval.py    scoring + tiered serving + FTS search
│   ├── provenance.py   why / asof / rollback / pin
│   ├── sessions.py     conversation diary (begin/end/recap)
│   ├── export.py       static operator guide → CLAUDE.md + AGENTS.md
│   ├── stats.py        shared metrics for CLI + dashboard
│   ├── check.py        CI gate
│   ├── doctor.py       install diagnostics
│   ├── hooks.py        git hook installers
│   ├── obsidian.py     db → Obsidian vault
│   ├── proof.py        full-stack contract/runtime/browser/test evidence
│   ├── proof_browser.py optional Python Playwright evidence collector
│   ├── dashboard.py    stdlib HTTP server + /api/* JSON
│   ├── assets/         dashboard SPA, Playwright runner + in-app docs copies
│   └── cli.py          argparse entry point
├── hooks/post-commit   installed template
├── docs/               this documentation (source of truth)
└── tests/              mock LLM + end-to-end smoke test
```
