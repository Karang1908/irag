# Changelog

All notable changes to irag. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver
in spirit (no public API contract yet beyond the CLI).

## 4.8.0 — 2026-07-25

### Added — the dashboard behaves like a tool you use every day
- **Every view is linkable.** The active tab lives in the URL, so a reload
  keeps your place, the back button works, and `#pages` / `#health` opens
  straight to a view. The document title follows the tab, which makes
  several dashboards distinguishable in a row of browser tabs.
- **Sortable tables** on Health staleness and both Map tables, with the
  conventional directions (text A→Z first, numbers high→low first) and
  `aria-sort` for screen readers. Sorting re-orders the *data*, not the DOM
  rows, so the 3-second refresh no longer snaps your sort back.
- **The Map tables have column headers at all.** They previously shipped
  two and four unlabeled columns.
- **A keyboard reference** (`?`, or the footer button) in a native
  `<dialog>` — focus trap and Esc for free. Added `/` to focus the current
  tab's filter/question box and Esc to clear it.

### Fixed
- The shortcuts dialog rendered in the top-left corner: the design system's
  global `*{margin:0}` reset silently kills the UA `margin:auto` that
  centers a modal `<dialog>`.
- Numbers are tabular everywhere data lives, so metric values stop jittering
  as they count up and columns actually line up.
- `.mini` buttons were ~21px tall (imprecise even with a mouse); raised to
  26px, with a `pointer:coarse` block that expands every target to 36–44px
  on touch without bloating the dense desktop layout.
- Empty states in the Map and staleness tables now say what to do
  ("run Scan map from the Tools tab") rather than "no scan data".
- Copy: removed the em-dash cadence flagged by the design detector.

## 4.7.0 — 2026-07-25

### Fixed — **the dashboard claimed a clean bill of health on an empty memory**
- With zero revisions written, Health reported *"none — memory agrees with
  the code ✓"*. There was no memory to agree with anything; the green tick
  was actively misleading on exactly the projects least able to spot it.
  It now distinguishes "nothing to check yet" from "every claim still
  matches the code", keyed on whether any revision exists.

### Added — first-run guidance
- **A three-step setup panel on Overview**, shown only while the memory is
  actually unusable, with each step reflecting live state rather than a
  canned checklist: is a summariser reachable (from `doctor`), has anything
  been synthesized (from `status`), are the hooks wired (from `doctor`).
  Steps that are done collapse to a tick; the ones that aren't carry the
  button that fixes them (*Build memory now*, *Wire hooks*). It self-clears
  once the project works, and is dismissible — the dismissal is keyed by
  **project root**, since the dashboard reuses ports across projects.
- **Plain-language captions** on the panels whose vocabulary is opaque to
  a newcomer: what a contradiction actually is, what a staleness score
  means and that it resolves itself, and that Operations are rarely needed
  because Update already runs sync + synthesis + lint.
- **Tooltips on all nine operation buttons** — "Sync", "Lint" and "CI
  check" are meaningless verbs until you know the model.

### Changed
- Pages shows "not written yet" instead of a cryptic `v–`, and the empty
  list distinguishes "no pages yet" from "no page matches that filter".
- Fixed "1 modules" pluralization in the dependency-graph caption.

## 4.6.0 — 2026-07-24

### Added — **the UI now does everything the CLI does**
- **New Tools tab.** *What your agent sees* renders the exact `irag
  context` briefing with its token count and FULL/DIGEST/INDEX breakdown —
  so you can look at precisely what SessionStart injects before spending
  it. *Time travel* runs `asof` for any date. *Operations* runs `sync`,
  `scan`, `lint`, `check`, `export`, `obsidian`, `claude-setup`, `backup`,
  and `doctor` without a terminal.
- `POST /api/op` is a **strict allowlist of Python callables** — the
  dashboard never builds a shell command from request data, so an
  unexpected op can only 400.
- New endpoints: `/api/context`, `/api/asof`, `/api/op`.
- Maintenance moved out of Health into Tools, so Health stays "what's
  wrong with the memory" and Tools is "what you can run".

With this, every command that is meaningful in a GUI is in the GUI. The
remainder are bootstrap/agent-internal by nature (`init`, `dashboard`,
`ingest-commit`, `session-begin`/`session-end`) or already covered
(`search`/`ask` are Chat, `recap` is Sessions, `synthesize` is Update).

### Fixed
- The Tools budget field used `min="200" step="500"`, so the default
  `3000` was `stepMismatch`-invalid and silently blocked form submission —
  clicking "Preview briefing" did nothing. Caught by driving the real
  browser, not by static checks.

### Changed
- `hooks.claude_setup()` extracted from the CLI so the dashboard and
  `irag claude-setup` wire byte-identical hooks.
- `provenance.asof_data()` added (same pattern as `why_data`) so CLI and
  GUI time-travel share one query.
- README and the docs site describe the dashboard as a full CLI peer.

## 4.5.0 — 2026-07-24

### Changed — **synthesis now sees the actual code change, and says what it was**
- **The git diff is fed to the synthesizer.** A file prompt now carries the
  unified diff of what changed since the last synthesis (capped 4000
  chars, spanning the queued commits — or the working tree for an
  uncommitted edit) alongside the file's current content. Previously the
  model saw only the *resulting* file and had to infer the change from the
  commit message, so "## Recent changes" tended to restate the file.
  Snapshot mode (no git toplevel) simply omits the diff and works as
  before.
- **`change_summary` is a real description now.** Every page ends with a
  `CHANGE-SUMMARY:` line that is stripped from the body and stored on the
  revision, replacing the old boilerplate `"file synthesis from 1
  event(s)"`. This is what `irag sessions --json` (`changes_detail`) and
  the dashboard's Sessions tab display, so the conversation log finally
  says *what* each session changed. A generic fallback is used if the
  model omits the line.
- Docs: the ARCHITECTURE synthesis section was stale (it described a
  5-file/6000-char prompt that no longer existed); it now matches the
  implementation.

## 4.4.0 — 2026-07-24

### Added — **the dashboard is now a full peer of the CLI**
- **New Pages tab — browse and manage the memory itself.** Filter every
  file/folder page, read its rendered summary, walk the full version
  history and diff any version against the previous one, see its blast
  radius (`impact`), pin/unpin it, and roll back to an older version
  behind an inline confirmation (still non-destructive). The same tab
  logs knowledge (`learn` / `record-decision`) and traces a claim back to
  the revision and commit that created it (`why`) — all of which
  previously required dropping to the terminal.
- **Transcripts in the Sessions tab** — a session's verbatim conversation
  renders alongside its per-file changes when
  `[sessions].capture_transcript` is on.
- **Maintenance in the Health tab** — back up the database and run
  `doctor` in-browser, with PASS/WARN/FAIL rows.
- **A favicon**, drawn from the brand mark as an inline SVG (no extra
  request, no external asset).
- New dashboard endpoints backing the above: `/api/pages`,
  `/api/transcript`, `/api/why`, `/api/impact`, `/api/diff`,
  `/api/doctor`, and POST `/api/learn`, `/api/record-decision`,
  `/api/pin`, `/api/rollback`, `/api/backup`.

### Changed
- `provenance.why_data()` and `doctor.collect()` extracted as structured
  helpers so the CLI and the dashboard trace claims and run diagnostics
  through exactly the same code (the CLI's printed output is unchanged).

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
