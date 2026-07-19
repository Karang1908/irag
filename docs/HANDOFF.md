# irag — Handoff Document

**For: an AI coding agent (Claude Code or otherwise) picking up development
on irag.** Read this fully before touching code. It tells you what irag
is, why it's built the way it is, what's actually verified vs. assumed,
and how to not break the things that make it work.

This document is about **building irag**. If you're looking for how to
*use* irag in a project, read `/CLAUDE.md` at the repo root instead —
that's the operator manual for agents consuming irag's memory, not
developing it.

---

## 1. What this is, in one paragraph

irag is a local, dependency-free knowledge base that gives AI coding
agents persistent, verified memory of a codebase. Every file and folder
gets an LLM-written summary page, versioned on every change (append-only,
v1/v2/v3...), mechanically fact-checked against the actual code (wrong
claims become queryable "contradiction" rows, not silent lies), and
served to agents through a CLI, a generated CLAUDE.md, a live dashboard,
and an Obsidian graph. A conversation logger records every coding session
as a database row — what changed, what was decided, an LLM narrative —
so a brand-new chat can resume a project for ~150 tokens instead of
thousands of tokens of re-exploration. Everything lives in one SQLite
file (`.irag/memory.db`) that any agent, on any machine, can mount.

Current version: **4.1.2**. ~4,200 lines of Python, zero runtime
dependencies (stdlib only — `sqlite3`, `http.server`, `ast`, `tomllib`).
Package name `irag`, console command `irag`, config dir `.irag/`.

## 2. The one sentence that explains every design decision

**"The model never does bookkeeping."** Locating, counting, dating,
diffing, scheduling, verifying — all SQL. The LLM's only job is writing
prose (page synthesis) and, optionally, comparing prose to fact (the
`lint --llm` tier). Every time you're tempted to have the LLM decide
something structural (is this file important? has this changed enough?
what should I summarize next?), stop — that decision almost certainly
belongs in a SQL query or a parser, not a prompt. This is why the linter,
the staleness scoring, the retrieval ranking, the session windowing, and
the dependency graph are all deterministic code with zero LLM calls.

The second principle: **memory is data with a prose projection, not
prose**. CLAUDE.md, the dashboard, the Obsidian vault — all of these are
*read-only views* generated from the database. Nothing is ever the
source of truth except `.irag/memory.db`. Never let a generated artifact
become authoritative, and never let the LLM write directly into a table
that isn't `revisions` (i.e., never skip the queue/versioning machinery
"just this once" for convenience).

## 3. Architecture — the four layers

```
Layer 1  GROUND TRUTH     the repo: files, git (or working-tree fingerprints), manifests
            │  ingest.py: sync() → events, one per changed FILE
            ▼
Layer 3  AUDIT            events (queue) ──► contradictions (linter output)
            │  synthesis.py: sweep()              ▲
            ▼                                     │  linter.py: lint()
Layer 2  SYNTHESIS        pages ──► revisions (append-only, v1/v2/v3...)
            │  files first, then folders bottom-up (fixpoint loop)
            ▼
Layer 4  EPISODIC         sessions (conversation diary) ──► recap
            │  sessions.py: begin()/end(), narrated by the LLM
            ▼
         irag context / irag ask / CLAUDE.md / dashboard / Obsidian
```

Layer 1→3: `irag sync` (or the git hooks) turns file changes into
`events` rows. Every tracked **file** is its own subject; every
**folder** (including root `.`) is a subject whose page rolls up its
children. Ignoring is `[modules].ignore` + `.iragignore` (gitignore-style
patterns) + hidden paths + binary extensions — see `ingest.is_ignored`.

Layer 3→2: `irag synthesize` (or `irag update`, which also runs sync +
lint + export) processes queued events into new page revisions. Two
prompt templates in `synthesis.py`: `FILE_INSTRUCTION` (summarizes one
file from its capped content + parsed structural facts + the **real**
blast-radius list of dependents) and `FOLDER_INSTRUCTION` (rolls up
direct children's summaries). Folders synthesize in a **fixpoint loop**
— deepest-eligible-first, repeated until nothing changes — so a whole
tree can go from root to leaves in one `update` call. `_unready_child_folder`
prevents a folder from rolling up before its child folders have revisions.

Layer 2 vs Layer 1: `irag lint` extracts checkable claims from page
bodies (backticked paths, version pins, backticked `symbol()` calls) and
verifies them against the filesystem, dependency manifests, and the
`symbols` table. Failures become `contradictions` rows with severity.
Static contradictions that stop reproducing on a later lint pass
**auto-resolve** (see `linter.lint`, the `failing` set logic) — this was
a deliberate fix for a dead-end where fixing a page could never clear
`irag check`.

Layer 4: `sessions.py`. A session is opened (`begin`), tracks its
"ownership" of events/revisions by **database ID high-water marks** (not
timestamps — same-second sessions must not steal each other's rows), and
is closed (`end`) with a deterministic digest, optionally re-narrated by
the LLM into 2-5 sentences. `recap_block()` renders the last N sessions
as markdown and is injected at the top of every `irag context` call.

## 4. Schema (SQLite, WAL mode, foreign keys on)

Defined in `db.py::SCHEMA`. Do not modify without a migration story —
there is currently **no migration system**; schema changes require users
to delete `.irag/memory.db` and rebuild. If you add a migration system,
it should live in `db.py::ensure_db` and be additive/idempotent
(`CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN` guarded by a
`PRAGMA table_info` check).

- `meta` — key/value (`last_synced` commit hash, `last_scanned_head`,
  `snap_seq`)
- `pages` — one row per subject. `subject_type` ∈ {file, folder, log}.
  `page_type` ∈ {file, folder, decisions, lessons}. `UNIQUE(subject_type,
  subject_id)`. `current_revision_id` is moved **only** by the
  `revisions_advance` trigger — never in Python.
- `revisions` — append-only. Never UPDATE or DELETE a row here except in
  the (currently unbuilt) compaction path — see §8.
- `links` — page-to-page edges; `link_type='imports'` rows are rebuilt
  wholesale by `structure.scan()` on every run, `'related'` rows are
  manual/permanent.
- `events` — the queue. `status` ∈ {queued, processing, completed,
  failed, skipped}. `event_type` ∈ {commit, snapshot, decision, session,
  rollback, sweep, manual}.
- `contradictions` — linter output. `resolved_at IS NULL` = open.
- `symbols`, `deps` — the deterministic structural map, rebuilt by
  `structure.scan()`, gated on a **working-tree content fingerprint**
  (not git HEAD — this was a bug fix; git-HEAD gating missed uncommitted
  edits, which is how coding agents actually work).
- `tree_state` — path→sha1 fingerprints for snapshot-mode ingestion and
  for gating `structure.scan()`.
- `sessions` — the conversation diary. `start_event_id` /
  `start_revision_id` are the ownership high-water marks. `changes_detail`
  (added 4.1.2, additive column — see the `ensure_db` guard) holds the
  full per-file `{subject_id, version_number, change_summary}` list for
  every revision written in the session's window, deliberately redundant
  with `revisions` so `irag sessions --json` / the dashboard's Sessions
  tab never need a join to show exactly what a past conversation changed.

Three triggers do all pointer/index bookkeeping:
`revisions_advance` (moves `current_revision_id`, resets staleness),
`revisions_ai`/`revisions_ad` (keep `revisions_fts` — an FTS5
external-content table — in sync). **Never** move `current_revision_id`
or write to `revisions_fts` directly in Python; always go through
`INSERT INTO revisions`.

## 5. Module map (what owns what)

| File | Responsibility |
|---|---|
| `db.py` | schema, triggers, `ensure_db`, small shared helpers (`get_or_create_page`, `current_body`, `fts_sanitize`) |
| `config.py` | defaults + `.irag/config.toml` deep-merge (stdlib `tomllib`) |
| `ingest.py` | change detection: git commits OR working-tree snapshots → per-**file** events; ancestor folders bumped at half weight; `.iragignore`/ignore-list handling; `purge_ignored` (removes pages for paths newly added to ignore); hybrid sync (git commits *and* fingerprint diff, always — see §8 gotcha #1) |
| `structure.py` | deterministic code map: `ast` for Python, regex for JS/TS/Go/Rust; `symbols`/`deps` tables; `impact()` (BFS blast radius); `facts_block()` (compact prompt-ready summary) |
| `synthesis.py` | prompt building (file + folder templates), `run_llm()` (stdin / `{prompt}` / `{promptfile}` delivery, ANSI stripping, empty-output guard), the fixpoint sweep, auto-requeue of failed/stuck events |
| `linter.py` | static contradiction checks (path/version/symbol) + optional LLM audit tier; auto-resolve logic |
| `retrieval.py` | zero-token relevance scoring (open-file match, dependency neighbors, FTS, recency, staleness/contradiction penalties, project-log boost, root-overview boost) + tiered FULL/DIGEST/INDEX serving under a token budget |
| `provenance.py` | `why` (claim → revision → event), `asof` (time travel), `rollback` (non-destructive — inserts old body as new revision), `pin`/`unpin` |
| `sessions.py` | the conversation logger described in §3 |
| `export.py` | db → generated `CLAUDE.md` **and** `AGENTS.md` (identical content; the latter is the cross-tool convention read by Codex/Antigravity — folder/log pages only, file-level detail stays in `map`/`context`) with an agent-instruction footer; both are in `ingest.SELF_ARTIFACTS` so the export never feeds irag's own queue |
| `stats.py` | shared metric builders (`status_dict`, `token_series`, `activity`) used by both the CLI and the dashboard — **don't duplicate this logic in cli.py or dashboard.py, always route through here** |
| `check.py` | the CI gate: exit 1 on open contradictions or staleness over max |
| `doctor.py` | full install diagnosis (env, db/FTS integrity, config types, LLM probe, hooks, queue health) |
| `hooks.py` | git hook installer (post-commit/post-merge/post-checkout); never clobbers a foreign hook |
| `obsidian.py` | db → Obsidian vault projection (wipe-and-rebuild behind a `.irag-vault` marker guard) |
| `dashboard.py` + `assets/dashboard.html` | stdlib `http.server` + a single hand-written SPA; `/api/chat` implements the SQL-vs-AI auto-router (`route_query`) |
| `cli.py` | argparse wiring; every command resolves the project root then opens `.irag/memory.db` |

## 6. Command inventory (34 commands, current as of 4.1.1)

```
Setup:      init  claude-setup  doctor
Detect:     sync  ingest-commit  scan
Write:      synthesize  update  learn  record-decision  resolve  rollback
Read (SQL): search  map  impact  stale  status  diff  contradictions  asof
Read (AI):  ask  context
Sessions:   session-begin  session-end  sessions  recap
Admin:      pin  unpin  export  check  backup
Views:      dashboard  obsidian
```

`irag update` is the composite command (sync → synthesize → lint →
export) and is what the Claude Code Stop hook runs. `irag synthesize` is
the lower-level primitive if you need finer control (`--dry-run`,
`--subject`, `--limit`).

## 7. What is and isn't verified

**Heavily tested** (see `tests/test_smoke.sh` and the ad-hoc batteries
run during development — there is no permanent pytest suite, tests were
written inline during each feature pass and are not preserved as files
except the smoke test): the full file/folder synthesis cascade including
fixpoint ordering; the sync dedupe matrix (first/idle/edit/commit-same —
must be 3/0/3/0/3 versions written respectively); snapshot mode with no
git; `.iragignore` including late-added patterns and purge; the linter's
symbol/path/version checks and auto-resolve; session ownership boundaries
including same-second and crash-recovery cases; the dashboard's REST API
and SQL/AI chat routing; agy-style `{prompt}` argv delivery and ANSI
stripping; the three git hooks; ID-based (not timestamp-based) session
windows.

**Not verified — assumed correct, needs real-world testing:**
- **Summary quality.** Every test used a deterministic mock LLM
  (`tests/mock_llm.py`) that emits fixed-shape fake pages. Nobody has
  evaluated whether a real LLM's summaries are actually *useful*, only
  that the plumbing around them works. This is the single biggest
  unknown in the whole project — see the rating discussion in the chat
  history if available, or just: the linter catches *factual* lies, not
  *low-value-but-true* summaries.
- **Cost at real scale.** Threshold defaults to 1 (every change gets a
  new version), which was an explicit user choice, but nobody has run
  `irag update` on a 1,000+ file repo and measured wall-clock or dollar
  cost.
- **The `agy` CLI integration** was tested against a hand-written fake
  binary that mimics the documented `-p` argument behavior, not the real
  `agy` tool. If real `agy` behaves differently (different flag names,
  different empty-output conditions), `docs/SETUP.md`'s Antigravity
  section may need correction.
- **Multi-user / concurrent-agent scenarios.** SQLite WAL + a 5s busy
  timeout should handle two `irag` processes writing at once, but this
  was never stress-tested with genuine concurrency (e.g., two Claude Code
  sessions on the same repo simultaneously).

## 8. Known gotchas — read before you "fix" these

1. **`sync` vs `update` is a real UX trap.** `irag sync` only *detects*
   changes (queues events); it never writes a new page version. Users
   naturally expect "I ran sync, why didn't the page update" — this has
   already confused a real user. `irag update` is sync+synthesize+lint+
   export in one call and is what should be recommended everywhere.
   Consider: (a) renaming `sync` to something less version-implying, (b)
   having `sync` print "N event(s) queued — run `irag update` to
   synthesize them" instead of just a count, or (c) accepting the
   two-step model but auditing every doc/dashboard surface for clarity.
   **Fixed in 4.1.2** via option (b): `cmd_sync` now prints a second
   line pointing at `irag update` whenever events were queued (silent
   when idle). The command wasn't renamed and the two-step model itself
   is unchanged — this only closes the "ran sync, nothing happened,
   why" confusion at the point it occurs.
2. **Structural scan must gate on the tree fingerprint, not git HEAD.**
   This was a real bug: gating on `git rev-parse HEAD` meant uncommitted
   edits (the normal state of a coding agent's working tree) never
   refreshed `symbols`/`deps`, which fed stale "ground truth" into
   synthesis prompts and made the linter flag real new symbols as
   missing. Fixed in `structure.scan()` by hashing `tree_state` instead.
   If you touch scan-gating logic again, re-verify with: edit a file
   without committing, run `irag map <file>`, confirm new symbols appear
   *without* running `update` first (map/context/impact/ask all call
   `ingest.sync()` before reading).
3. **`sync()` is now hybrid even inside git repos**: it processes git
   commits (for provenance/commit-message richness) *and* runs the
   fingerprint diff on every call, so uncommitted edits are never
   invisible. This doubles the walk-the-tree cost of every sync — if
   that becomes a performance problem on huge repos, the fix is NOT to
   remove the fingerprint pass (see gotcha #2) but to make `_tree_hashes`
   cheaper (e.g., mtime pre-filter before hashing).
4. **Commit-content dedupe requires `repo` to be passed through.** There
   was a bug where `irag ingest-commit` (what the post-commit git hook
   actually calls) didn't pass the repo root to `ingest_commit`, so
   `_content_already_known` silently no-opped and commits re-triggered
   synthesis even when a snapshot had already captured identical
   content. Fixed — but this is the kind of bug that hides for a long
   time because both paths "work," one is just wasteful. If you add a
   new call site for `ingest_commit`, make sure `repo` is threaded
   through.
5. **BrokenPipeError on `irag search ... | head`.** Fixed with a
   top-level catch in `cli.main()`. Any new command that can produce
   long stdout should be assumed to get piped into `head`/`less`.
6. **The root folder page (`.`) must always clear the retrieval floor.**
   Without an explicit boost, a project with no open files could get a
   near-empty SessionStart injection (a real measured bug: 309 chars of
   nothing). Fixed with a `+40` "project overview" boost in
   `retrieval.score()`. Don't remove this without re-measuring what a
   cold SessionStart injection looks like.
7. **`.iragignore` additions after `init` don't retroactively remove
   pages unless `purge_ignored()` runs**, which it now does on every
   `sync()`. If you refactor `sync()`, keep the purge call — otherwise
   secrets/generated files that get ignored *after* first synthesis stay
   searchable forever.
8. **No compaction/pruning exists**, and per explicit user decision this
   is **not wanted** — unbounded version history is treated as the
   product, not a liability. Do not add automatic pruning. If a manual
   `irag compact` is ever requested, it must never delete v1, the current
   revision, or any revision referenced by a rollback/resolved
   contradiction (provenance endpoints), and it must be opt-in only.

## 9. Development workflow

1. Read the relevant module(s) fully before editing — most files are
   short (100–400 lines) and self-contained.
2. Make your change.
3. `python3 -m pyflakes irag/*.py` — must be clean (no unused
   imports/vars) before every commit-equivalent.
4. Test against the mock LLM, never a real API key, for anything
   automated: `tests/mock_llm.py` reads a prompt on stdin and returns
   fixed-shape fake pages; point `.irag/config.toml`'s `[llm].command`
   at `python3 <path>/tests/mock_llm.py` in a scratch project.
5. Run `sh tests/test_smoke.sh` — must print `SMOKE TEST PASSED`. This
   is the only persisted regression test; it exercises init, sync,
   synthesize, lint, check (both fail and pass paths), context, why,
   export, resolve, rollback, record-decision, sync-after-commit, scan,
   map, impact, learn, claude-setup, obsidian, status, diff, backup,
   doctor, and a live dashboard boot. If you add a feature, add a line
   to this script.
6. For anything involving hooks, sessions, or the dashboard, write a
   throwaway Python battery (see the pattern used throughout
   development: spin up a `tempfile.mkdtemp()` project, drive it via
   `subprocess.run([sys.executable, "-m", "irag", ...])`, assert on
   stdout/exit codes/sqlite state). These were never preserved as files;
   consider promoting the best of them into `tests/` as real pytest
   files if you have the ability to add a test dependency (currently
   zero deps, so this would need to stay stdlib `unittest` or be an
   optional dev-only dependency).
7. Keep `irag/assets/docs/*.md` in sync with `docs/*.md` and `README.md`
   — the dashboard's Docs tab serves the `irag/assets/docs/` copies, and
   they drift silently if you edit only one side. There's no build step
   that copies them automatically; do it manually (`cp docs/*.md
   irag/assets/docs/`) as part of any doc change.
8. Bump `__version__` in `irag/__init__.py` **and** `version` in
   `pyproject.toml` together — nothing enforces they match, but the
   `doctor`/`status` output implicitly assumes they do.
9. Never introduce a runtime dependency without a strong reason — the
   zero-dependency property is load-bearing for the "any agent on any
   machine" pluggability story. If you must (e.g., a real MCP server
   needs the `mcp` package), gate it behind an optional extra exactly
   like the historical `pkb[mcp]` pattern (see git-adjacent history /
   docs/DEVELOPMENT.md) and guard the import so the core stays
   installable with zero deps.

## 10. Explicitly deferred (roadmap, do not build unless asked)

- **MCP server.** Everything is shaped to make this easy — `stats.py`,
  `retrieval.serve()`, `sessions.recap_block()`, etc. are already
  pure-Python functions with no CLI coupling, so an MCP tool layer is
  mostly argument marshaling. Five obvious tools: `get_context`,
  `search`, `ask`, `why`, `record_decision`/`learn`. Gate behind an
  `irag[mcp]` extra.
- **Compaction/pruning** — see gotcha #8. Explicitly not wanted by
  default; build only as an opt-in command if asked, never automatic.
- **A real (non-mock) evaluation of summary quality** — this is
  arguably higher priority than any new feature. If you have the
  ability to run real LLM calls, the highest-value next step is running
  `irag update` on a real multi-week-old project and manually grading
  whether the pages are worth reading, not just factually correct.
- **Migration system for schema changes** — currently none; see §4.
- **Confidence scoring from lint history** (pages that have been
  wrong before get a lower `pages.confidence`, surfaced in retrieval).
- **CI webhooks** (post contradictions to a PR as a comment).

## 11. Non-negotiables — do not regress these under any refactor

- Revisions are append-only. Never `UPDATE`/`DELETE` a `revisions` row
  (rollback inserts a *new* row with old content — it never rewrites).
- `pages.current_revision_id` moves only via the `revisions_advance`
  trigger.
- The database is the only source of truth. CLAUDE.md, the dashboard,
  and the Obsidian vault are always regeneratable from it and never the
  reverse.
- Zero runtime dependencies for the core package.
- Every LLM call must have a non-LLM fallback path where the product
  still functions (e.g., session narration falls back to a deterministic
  digest if the LLM is unavailable; nothing in the read path — search,
  map, impact, why, asof — ever requires an LLM).
- `irag doctor` must stay accurate — it's the self-service diagnostic
  the operator manual (`/CLAUDE.md`) tells agents to run first when
  anything misbehaves. If you add a new failure mode anywhere in the
  system, add a corresponding check to `doctor.py`.
