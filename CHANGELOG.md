# Changelog

All notable changes to irag. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver
in spirit (no public API contract yet beyond the CLI).

## 4.3.0 — 2026-07-24

### Added — pluggable memory: verbatim transcripts + a wider code graph
- **Opt-in conversation transcripts.** `[sessions].capture_transcript`
  (default **off**) makes `session-end` store the verbatim conversation —
  your prompts and the agent's replies, tool calls noted as `[tool: X]` —
  in a new `session_messages` table, read back with the new `irag
  transcript <id>` command (36 commands total). Claude Code pipes its
  transcript path in on stdin automatically; other agents pass
  `--transcript <file.jsonl>`. Internal reasoning and raw tool output are
  never stored, and capture is off by default because transcripts can
  carry secrets — enabling it is one config line. `session-end` never
  blocks on stdin (guarded read).
- **The structural graph now covers Java, C#, Ruby, PHP, and C/C++** on
  top of Python/JS/TS/Go/Rust. `irag map`, `irag impact`, and the
  dashboard graph now show symbols for all of them; import edges resolve
  for file-relative imports (Ruby `require_relative`, C/C++ `#include
  "..."`, relative JS/PHP includes). Java/C# contribute symbols but not
  edges (package-path imports need source roots irag doesn't track). This
  also closes a latent false-positive: in a mixed-language repo, a symbol
  claim on a previously-unparsed language's page could be wrongly flagged
  as a phantom symbol.

### Changed — trustworthy token estimates
- Token counting is centralized in `irag.tokens` with a code-aware
  heuristic (closer than the old `chars // 4`, which undercounts
  punctuation-dense source) and sharpened by `tiktoken` when it happens
  to be installed. Still reported as an **estimate** — irag stays
  dependency-free and its default summarizer is Claude, whose exact
  tokenizer isn't public, so an honest estimate beats a false "exact".

### Changed — **CLAUDE.md is now a static agent guide installed by `irag init`**
- `irag init` installs `CLAUDE.md` + `AGENTS.md` — irag's operator manual
  (how to use irag), bundled with the package — so a coding agent knows
  irag exists and how to drive it from the moment of init, before spending
  a token. A `CLAUDE.md` you wrote yourself is never clobbered (irag only
  overwrites files it installed).
- `irag update` no longer touches `CLAUDE.md`/`AGENTS.md`. The memory lives
  in `.irag/memory.db` and the agent reads it on demand via `irag context`
  / `recap` / `search`; the guide stays static. `irag export` re-installs
  the guide if it's deleted.


### Fixed — audit pass (correctness, data-integrity, concurrency)
- **Append-only history is now race-safe.** `revisions(page_id,
  version_number)` is a UNIQUE index (existing databases are upgraded on
  open), the next version number is computed *inside* the INSERT at every
  writer (synthesis, `learn`/`record-decision`, `rollback`), and
  connections use `PRAGMA busy_timeout` — so two overlapping writers on
  the same page (a Stop-hook `irag update` racing another session, a
  rollback racing a synthesis pass) can no longer collide and silently
  orphan a revision.
- **One failing page no longer starves synthesis.** `sweep()` isolates
  each page: a `SystemExit` from the LLM on one page is recorded and
  reported, but the rest of the sweep (and the lint step at the end of
  `irag update`) still runs. Failed pages stay queued and
  retry next time.
- **`irag update` no longer wipes `irag lint --llm` findings.**
  LLM-flagged contradictions carry a distinct `llm:` prefix, so the
  static tier's auto-resolve (scoped to `static:`) can't clear them; they
  auto-resolve only when a re-run of `lint --llm` stops flagging them.
- **`irag context` honors its token budget.** The DIGEST tier and the
  INDEX tail are now charged against / capped by `budget_tokens`, so the
  injected briefing can't balloon far past the requested size.
- **Token spend counts the prompt, not just the output** — the
  dashboard's "est tokens" and burn chart now reflect real cost.
- **FTS search keeps non-ASCII terms whole** (`\w` instead of
  `[A-Za-z0-9_]`), so accented/CJK queries aren't fragmented.
- **Phantom-symbol linting is scoped** to a page's own module *and its
  imports* (catches real cross-module hallucinations without falsely
  flagging legitimate imported-symbol references), and now recognizes
  Java method/constructor declarations.
- **Dashboard chat: explicit "SQL" mode no longer falls through to AI on
  a miss** (instant "No matches.", zero tokens); the in-chat markdown
  renderer only linkifies safe `http(s)`/`mailto` URLs.
- Smaller fixes: `get_or_create_page` tolerates a concurrent first-insert;
  `run_llm` never leaks its temp prompt file; `budget_tokens=0` is
  respected; snapshot change-detection hashes whole files (not just the
  first 1 MB); LIKE patterns escape `_`/`%` in paths; CLAUDE.md/AGENTS.md
  are written atomically; the dashboard's `/api/update` guard is atomic
  and 500s no longer echo internals to the client.

### Added
- `[check].fail_on_staleness` config toggle (default `true`) — set it
  `false` to let `irag check` pass despite stale pages.
- Smoke-test coverage for `search`, `ask`, `recap`, `asof`, `pin`/`unpin`,
  and the dashboard chat's forced-SQL routing.

### Changed
- Dashboard restyled to match the documentation site's editorial design
  system (Clash Display + Satoshi, near-black surface, hairline stat
  ledger, single-accent token-burn chart). Presentation only.
- README command index corrected (35 commands; `lint` was missing).

## 4.2.0 — 2026-07-19

### Changed — **directory-wise project scoping** (behavior change)
- **Root resolution no longer consults the enclosing git repository.**
  The project root is the nearest ancestor (including cwd) containing
  `.irag`, else cwd. Previously, `git rev-parse --show-toplevel` won —
  so running any irag command inside a versioned home/Desktop directory
  silently adopted the *entire* enclosing repo as the project (a real
  incident: 1,376 pages of a user's whole Desktop). That is now
  structurally impossible.
- **`irag init` initializes the directory you run it in. Period.** If
  that directory sits inside a larger git repo, init prints a scoping
  note, uses snapshot mode (content fingerprints), and does **not**
  install hooks into the enclosing repo. `git init` the project itself
  any time to upgrade it to commit-based ingestion.
- Git-based ingestion (and hook installation, doctor's hook checks,
  the dirty-file count) now require the project root to *be* a git
  toplevel (`ingest.git_rooted`); forcing `[ingest].mode = "git"` on a
  nested project fails with a clear error instead of mis-ingesting
  toplevel-relative paths.
- Projects nest and multiply: each keeps its own `.irag`; whichever is
  nearest to where you run a command is the one you operate on. The
  generated per-project `CLAUDE.md`/`AGENTS.md` compose hierarchically
  with global/parent context files in Claude Code.

### Added
- The dashboard is per-project and says so: the project name appears in
  the top bar, sidebar, and tab title (`/api/status` gains `project` +
  `root`). Running dashboards for several projects at once works — if
  the port is taken, the next free one (up to +20) is used
  automatically.
- Smoke-test coverage for nested scoping (init inside a larger repo
  must not leak `.irag` or hooks to the parent).

## 4.1.2 — 2026-07-19

### Added
- **Per-file change detail on sessions.** `sessions.changes_detail`
  stores the exact `{subject_id, version_number, change_summary}` list
  for every page version written in a conversation's window — redundant
  with `revisions` by design, so `irag sessions --json` and the
  dashboard never need a join. Additive column; existing databases are
  upgraded in place by a guarded `ALTER TABLE` on open.
- **AGENTS.md export.** `irag export` (and therefore `update` and the
  hooks) now writes `AGENTS.md` alongside `CLAUDE.md` with identical
  content, so non-Claude agents (Codex, Antigravity IDE, Cursor) that
  read the cross-tool convention file get the same instructions. Both
  files are excluded from irag's own ingestion.
- **Dashboard: Sessions tab** — the conversation log as a master-detail
  view: every logged session with status, agent, counts, full summary,
  and the per-file change list.
- **Dashboard: interactive dependency graph** on the Map tab —
  Obsidian-style canvas force simulation: drag to pan, scroll to zoom
  (cursor-centered), drag nodes, hover to trace imports with
  glow/dimming, folder-colored nodes with legend, zoom/fit controls.
  Zero dependencies (hand-rolled physics + canvas renderer).
- **Dashboard: full visual redesign** — sidebar navigation, top status
  bar with live/offline indicator, iconned metric cards with count-up
  animation, gridded token-burn chart, skeleton loading states, empty
  states, reduced-motion support throughout.
- **Chat loading states** — typing indicator with mode-aware hint,
  disabled input while a request is in flight, error bubbles on network
  failure (previously a dropped connection hung forever).
- `GET /api/sessions` dashboard endpoint (JSON-parsed `files_changed`
  and `changes_detail`).
- `irag --version`.
- `LICENSE` (MIT), `.gitignore`, this changelog, PyPI classifiers.

### Changed
- `irag sync` now prints a hint to run `irag update` whenever it queued
  events — closing the long-standing "I ran sync, why didn't the page
  update" trap. Silent when idle.
- `irag sessions --json` parses `files_changed`/`changes_detail` into
  real JSON arrays instead of double-encoded strings (shared
  `sessions.row_to_dict` used by both CLI and dashboard).
- The generated CLAUDE.md/AGENTS.md footer now leads with an explicit
  "use irag before you grep" directive and documents the
  session-begin/session-end obligation for agents without hooks.
- Dashboard HTML responses send `Cache-Control: no-store` — a
  redeployed dashboard is never masked by a stale browser cache.

### Fixed
- Chat requests that failed at the network layer left a permanent "…"
  bubble; they now render an error message and re-enable the input.

## 4.1.1

Baseline for this changelog: 34-command CLI, file+folder synthesis with
fixpoint folder rollups, static contradiction linter with auto-resolve,
zero-token retrieval/serving, conversation sessions with LLM narrative,
git-hook + snapshot ingestion, Claude Code hook integration, dashboard
(overview/chat/health/map/docs), Obsidian vault projection, smoke-test
suite with deterministic mock LLM.
