# irag — CLI Reference

Project resolution is **directory-wise**: every command operates on the
nearest ancestor directory (including the current one) that contains
`.irag`. An enclosing git repository is never adopted as the project —
a versioned home/Desktop directory can't silently become one. Projects
nest and multiply; whichever `.irag` is nearest to where you run the
command is the one you operate on. All commands except `init` require
prior initialization and print a friendly error otherwise. Exit code is
0 on success; `check` uses 1 to signal a failed gate. `irag --version`
prints the installed version.

---

### `irag init`

Initialize **the directory you run it in** as a project: create
`.irag/memory.db` and `.irag/config.toml` (defaults, only if absent),
and ingest what's there. When the directory *is* a git repo's top
level, git hooks are installed (never clobbering a foreign hook) and
history is ingested. When it sits inside a *larger* git repository,
init prints a scoping note and runs in snapshot mode — the enclosing
repo is not touched and gets no hooks; `git init` the project itself
any time to upgrade it to commit-based ingestion.

### `irag sync`

Catch up on changes. In git mode: all commits since the last synced hash
(idempotent; survives rebases via full re-scan). In snapshot mode (no
git at the project root, or `[ingest].mode = "snapshot"`): diffs content
fingerprints of the working tree — adds, edits, and deletions each
become events, no commit required. `auto` (default) picks git only when
the project root **is** a git repository's top level; a project scoped
to a subdirectory of a larger repo always snapshots (git's paths are
toplevel-relative and would not match the project's subjects).

`sync` only *detects* changes (queues events) — it never writes a new
page version. If it queued anything, it prints a reminder to run `irag
update`, which is the composite command that actually synthesizes.

### `irag ingest-commit REF`

Ingest a single commit (what the git hook runs with `HEAD`). One queued
event per touched module; staleness bumped per config. Idempotent on
`(commit, module)`.

### `irag synthesize [--dry-run] [--limit N] [--subject S]`

Update pending pages via the configured LLM.

- `--dry-run` — print the exact prompt(s) instead of calling the LLM
- `--limit N` — process at most N pages (highest staleness first)
- `--subject S` — force one subject (e.g. `src/auth`), pending or not

Synthesis is hierarchical and two-phase: pending FILE pages first (each
file summarized from its own capped content + structural facts), then
FOLDER pages bottom-up (each folder summarized from its direct children's
summaries), ending at the root. A page is pending when not pinned and
(never synthesized with queued events, or staleness ≥ threshold); file
changes bump their file page fully and every ancestor folder at half
weight, so summaries cascade upward. Before sweeping, previously **failed**
events (e.g. from a misconfigured LLM command) and events stuck in
'processing' for over 10 minutes (interrupted runs) are automatically
requeued — fixing the config and re-running synthesize always recovers.
When nothing is pending, the message explains *why* (no pages, empty
queue, or below-threshold staleness with the current score shown).

### `irag context [--open FILE ...] [--query Q]`

Serve tiered, budget-gated context markdown: warnings, FULL bodies,
DIGEST summaries, INDEX lines. When the working tree has uncommitted
changes (excluding irag's own artifacts) a note is prepended, since pages
and map reflect the last commit. `--json` emits the machine dict. `--open` is repeatable and boosts the matching module (+50), its
relatives (+25), and its dependency neighbors from the structural map
(+20). Each FULL page carries a compact **map** block (files, symbols,
imports/importers). `--budget N` overrides the token budget (used by the
Claude Code hook).

### `irag search QUERY`

Full-text search (FTS5) over all revision bodies; current revisions
surface first, with snippets.

### `irag lint [--subject S] [--llm]`

Static checks of current page bodies against ground truth: backticked
paths must exist on disk (`missing_path`, high), version claims must match
package.json / requirements.txt (`version_mismatch`, medium), backticked
`identifiers()` must be defined in the module directory
(`missing_symbol`, low, heuristic). Identical open claims are deduped, and static contradictions that no
longer reproduce (page revised, file restored, version corrected) are
auto-resolved with a note — so `irag check` goes green again without
manual cleanup. `--llm` additionally runs the LLM audit tier
(`llm_flagged`); LLM-flagged rows are never auto-resolved.

### `irag contradictions [--resolved]`

List open (default) or resolved contradictions with id, subject, type,
severity, claim, and truth.

### `irag resolve ID [--notes TEXT]`

Mark a contradiction resolved, with optional notes.

### `irag stale`

Show every page's staleness score, pinned flag, and whether it is due for
synthesis.

### `irag why CLAIM`

Trace a claim: best-matching revision (page, version, date, model) and the
event chain that produced it (commit hash, message, files) — or
"human/rollback".

### `irag asof DATE [--show SUBJECT]`

Print the wiki state as of an ISO date (each page's latest revision at
that time); a bare `YYYY-MM-DD` is inclusive of that entire day. `--show` prints one subject's full body as of that date.

### `irag rollback SUBJECT VERSION`

Non-destructive rollback: inserts the old body as a **new** revision
(`change_summary = "rollback to vN"`) via a `rollback` event. No history
is rewritten.

### `irag pin SUBJECT` / `irag unpin SUBJECT`

Pinned pages are skipped by synthesis (hand-curated content stays put).

### `irag export`

(Re)install `CLAUDE.md` **and** `AGENTS.md` — irag's static operator
manual (bundled with the package), identical content, at the repo root.
`irag init` already installs them; run this to restore them if deleted.
The latter is the cross-tool convention read by Codex, Antigravity, and
other non-Claude agents. A `CLAUDE.md` you wrote yourself is never
clobbered — irag only overwrites files it installed. These files carry
instructions, not memory: `irag update` never rewrites them.

### `irag obsidian [--out DIR] [--no-versions]`

Project the database into an Obsidian vault (default `<repo>/irag_vault`).
The vault is a disposable build artifact: wiped and rebuilt every run
(guarded by a `.irag-vault` marker — irag refuses to delete a folder it did
not create; close the vault in Obsidian before regenerating).

Contents: `Modules/` — one note per page with YAML frontmatter (version,
staleness, confidence, tags), open contradictions as `[!warning]`
callouts, parent/child + explicit wikilinks, and history links;
`_versions/` — one note per revision, tagged `#version`, chained
prev/next, carrying trigger provenance (commit hash/message, rollback,
decision); `_meta/` — `stats.md`, `contradictions.md`,
`graph_settings.md` (color groups + the `-tag:#version` toggle trick);
`README.md` index. `--no-versions` omits the history nodes.

### `irag update [--limit N]`

The one-shot pipeline and the standard agent trigger: sync (detect
changes) → synthesize (new page versions for everything affected, file
pages then folder rollups) → lint (fact-check). Updates the memory
database only; the CLAUDE.md/AGENTS.md agent guide (installed by
`irag init`) instructs agents to run this after finishing edits, so page
versions advance with every change automatically.

### `irag ask QUESTION [--open FILE] [--budget N]`

AI search. Retrieval assembles ranked, budgeted context for the question
(same engine as `irag context`), then the configured LLM answers using
ONLY that context — citing page paths, flagging contradicted pages as
unreliable, and saying what's missing (with the right irag command to
find it) instead of guessing. Complement of `irag search`, which is the
SQL side: instant FTS5 full-text lookup, zero tokens.

### `irag scan`

Force-rebuild the structural map (symbols + dependency edges). Runs
automatically on `init`, `sync`, `context`, `map`, and `impact` whenever
HEAD has changed, so you rarely need it by hand. Pure parsing — Python
via `ast`; JS/TS, Go, Rust, Java, C#, Ruby, PHP, and C/C++ via regex;
zero LLM tokens; capped at 300KB per file / 4000 files. Import edges
resolve for languages with file-relative imports (JS/TS, Python, Ruby
`require_relative`, C/C++ `#include "..."`, relative PHP includes); Java
and C# contribute symbols but not edges (their imports are package paths,
which need source roots irag doesn't track).

### `irag map [SUBJECT]`

Without a subject: the repo overview — every module with its symbol
count, plus all import edges. With a subject: that module's files, every
symbol with kind and `file:line`, what it imports, and what imports it.
This is the pre-computed map the agent reads instead of grepping.

### `irag impact SUBJECT`

Transitive reverse dependencies with hop distance: everything that could
break if the module changes. "Nothing imports X — change is contained"
when it's a leaf.

### `irag learn TEXT [--module M]`

Log a lesson/gotcha discovered while working (deterministic, no LLM).
Lands on the `lessons` page, which — like `decisions` — is boosted so it
appears in every context serve. This is how session knowledge survives:
the agent (or you) records it explicitly, and it is never lost to a
compaction.

### `irag claude-setup`

Wire irag into Claude Code for this repo (merge-safe, idempotent) with
two hooks in `.claude/settings.json`: **SessionStart** runs `irag context
--budget 3000` — stdout is injected as session context, so every session
starts already knowing the project — and **Stop** runs `irag update
--limit 50` (10-min timeout) when Claude finishes a turn, so changed
files are synthesized into new page versions mechanically, with zero
reliance on the agent remembering to do it; it is a no-op when nothing
changed.
Also (re)installs the `CLAUDE.md`/`AGENTS.md` agent guide if missing —
the operator manual that teaches agents to use `irag map`/`impact`/
`search`/`why` instead of exploring, and to log with `irag learn`/
`irag record-decision`.

### `irag session-begin` / `irag session-end [--no-narrate] [--transcript FILE]`

The conversation logger (called automatically by the Claude Code hooks;
usable by any agent or human). `session-begin` opens a diary entry and
records ID high-water marks; a still-open previous session (crashed
terminal) is closed as `interrupted` first. `session-end` closes the
entry and logs exactly what that conversation did — files changed, page
versions written, decisions and lessons recorded, commit messages — plus
an LLM-written 2-5 sentence narrative (deterministic digest if the LLM
is unavailable or `--no-narrate`). It also records, redundantly with
`revisions` (cheap to duplicate, expensive to join every time), the
exact per-file `change_summary` for every page version written that
session — this is `changes_detail` in `--json` output.

When `[sessions].capture_transcript = true` (off by default — transcripts
can contain secrets), `session-end` also stores the **verbatim
conversation**. Claude Code pipes the transcript path in on stdin
automatically; other agents pass `--transcript <file.jsonl>`. Read it
back with `irag transcript`.

### `irag transcript SESSION_ID [--json]`

Print the stored verbatim messages (your prompts + the agent's replies,
tool calls noted as `[tool: Name]`) for a logged session. Empty unless
`[sessions].capture_transcript` was on when the session closed. Internal
reasoning and raw tool output are never stored.

### `irag sessions [-n N] [--json]`

The project diary: every conversation with when it ran, its status
(closed/interrupted), change counts, and its summary. `--json` includes
`changes_detail` — the full list of `{subject_id, version_number,
change_summary}` for that session, so you can see exactly what changed
without joining `revisions` yourself.

### `irag recap [-n N]`

"Previously on this project" — the last sessions' summaries as markdown.
This is the fresh-start command: close every terminal, come back next
week, open a brand-new chat, and the agent already knows what happened —
it is also injected automatically at the top of `irag context` and the
SessionStart hook, so a new conversation costs ~zero tokens to resume
instead of thousands to re-explore.

### `irag dashboard [--port N] [--no-open]`

Local web dashboard on 127.0.0.1 (stdlib server, zero deps). Tabs:
**Overview** — live cards (token burn, page versions, contradictions,
queue) refreshing every 3s, a cumulative token-burn chart, a live
activity feed of revisions/events, and a "Run irag update" button with
streaming progress. **Chat** — chat with the knowledge base; each
message is auto-routed like the original DBS project: keyword/path/symbol
lookups → instant SQL search (green badge), natural-language questions →
AI answer with citations (purple badge); a keyword miss falls through to
AI, and a selector can force either mode; a typing indicator shows while
a request is in flight. **Pages** — the memory browser: filter every file
and folder page, read its rendered summary, walk its full version history
and diff any version against the previous one, see its blast radius, pin
or unpin it, and roll back to an older version (with an inline
confirmation — the rollback is non-destructive, as always). The same tab
logs knowledge (`irag learn` / `irag record-decision`) and traces a claim
back to the revision and commit that created it (`irag why`).
**Health** — open contradictions with one-click
resolve, staleness table, click any page to read its current body.
**Visualize** — the codebase as a 3D force-directed graph built from the
same symbol and dependency tables `map` queries: nodes are files (sized by
symbol count, coloured by top-level folder), edges are real imports. Drag
to orbit, scroll to zoom, click a file to inspect what it defines, what it
imports, what depends on it, and everything irag has written about it; the
map is re-read every few seconds so new files appear without a refresh.
**Tools** — everything else the CLI does: *What your agent sees* renders
the exact `irag context` briefing with its token count and tier
breakdown; *Time travel* runs `asof` for any date; *Operations* runs
`sync`, `scan`, `lint`, `check`, `export`, `obsidian`, `claude-setup`,
`backup`, and `doctor` (a strict server-side allowlist of Python
callables — the dashboard never builds a shell command from a request).
**Map** — an interactive, Obsidian-style dependency graph (drag to pan,
scroll to zoom, drag nodes, hover to trace imports) above the
files/symbols and import-edge tables.
**Sessions** — the conversation log: click a session to see its full
summary, the exact per-file changes it made (`changes_detail`), and the
verbatim transcript when `[sessions].capture_transcript` is on.
**Docs** — the full documentation rendered in-app (Quickstart, Setup,
Architecture, CLI reference, Comparison).

Everything the dashboard does is also a CLI command, and vice versa —
the GUI is a full peer of the CLI, not a read-only viewer.

### `irag status [--json]`

One-screen dashboard: pages/revisions, symbols/dep edges, queue depth and
failures, open contradictions, pages due, estimated LLM tokens spent,
uncommitted changes, db size, sync/scan heads. `--json` for tooling and
benchmarks.

### `irag diff SUBJECT [V1] [V2]`

Unified diff between two revisions of a page (default: previous vs
current) — review exactly what a synthesis pass changed before trusting
it.

### `irag backup [PATH]`

Online, lock-safe snapshot of the database via SQLite's backup API
(default `.irag/backups/memory-<timestamp>.db`).

### `irag doctor [--probe-llm]`

Full install diagnosis with PASS/WARN/FAIL per check: git presence,
database and FTS integrity, config keys and types, LLM command on PATH
(`--probe-llm` invokes it once), all three git hooks, scan freshness,
stuck/failed queue events, Claude Code hook wiring. Exit 1 on any FAIL —
suitable for CI.

### `irag check`

The CI gate. Prints open contradictions, pages over `max_staleness`, and
never-synthesized pages. Exits 1 if open contradictions exist (when
`fail_on_contradictions = true`) or any page exceeds `max_staleness`.

### `irag record-decision TEXT [--module M]`

Log a decision deterministically (no LLM): inserts a `decision` event and
appends a line to the `decisions` page as a new revision. Like lessons,
decisions are boosted into every context serve.
