# irag — Handoff Document

**For: an AI coding agent picking up development on irag.** Read this
fully before touching code. It tells you what irag is, how to work on it,
which account to commit as, what is actually verified versus assumed, and
which traps in this specific environment will waste your time if nobody
warns you.

This document is about **building irag**. If you want to know how to *use*
irag inside a project, read `/CLAUDE.md` at the repo root — that is the
operator manual for agents consuming irag's memory, not developing it.

Accurate as of **v4.47.0**, 2026-09-06. Every fact below was verified by
running it on this machine by that date. Where something is inferred
rather than observed, it says so.

---

## Current working tree — reliability audit (uncommitted)

The broad CLI/backend/dashboard audit completed on 2026-09-06 is present
in the working tree but has **not** been committed or pushed. Preserve it.
It fixes the failure modes reported from real use, rather than changing the
append-only memory model:

- CLI updates, direct sync/scan/synthesize/lint commands, live CLI reads,
  page-control writes, dashboard updates, live-map refreshes, and dashboard
  maintenance operations now share one cross-platform repository lock. Live
  CLI reads pin a SQLite snapshot before releasing it, so a read can never
  combine pre-update and post-update state or spend model tokens on a partial
  sweep.
- Page creation and structural-map replacement participate in their caller's
  transaction. The scanner records the same working-tree fingerprint that
  `doctor` compares, eliminating both partial map swaps and the false
  "structural map stale vs HEAD" warning after a fresh scan.
- Deleted files and newly empty folders are withdrawn from every live read
  surface while their revisions remain queryable as history. Delete events
  complete without an LLM tombstone; revival and topic-member changes force
  the affected derived pages due. `impact` now fails closed for unknown or
  deleted subjects.
- Concurrent lesson/decision appends use an immediate transaction, and all
  session recap inputs (decisions, lessons, commits, events, revisions) are
  filtered by the owning session key. Overlapping agents no longer lose log
  entries or inherit each other's diary. Transcript retention is bounded while
  parsing, and corrupt legacy JSON is skipped instead of crashing SessionEnd or
  the Sessions dashboard.
- Configuration is validated semantically at load time and by `doctor` through
  one shared validator. Syntactically valid but unusable values can no longer
  poison a running dashboard and crash a later update. Schema upgrades are one
  serialized transaction: an interrupted or concurrent upgrade cannot leave a
  half-migrated database.
- The dashboard reloads config safely, reports an invalid live config instead
  of continuing with stale assumptions, serializes mutating operations,
  returns clean 4xx JSON for bad requests, marks API responses `no-store`,
  and exposes the same drift/mode/status facts as the CLI. Context, impact,
  map, and AI retrieval now refresh through the same live path, and dashboard
  context uses the complete CLI briefing including recaps and dirty-tree state.
  Browser-cancelled polls/navigation are treated as normal disconnects instead
  of recursively writing to a dead socket and printing backend tracebacks.
- The frontend distinguishes HTTP application errors from an offline server,
  prevents overlapping poll responses from rewinding the UI, stops idle graph
  animation, safely renders toast text, and has accessible controls. Its
  responsive grids were corrected so all nine tabs fit without body overflow
  at 390 px; only the tab strip scrolls and the brand remains visible.
- Request sizes, list limits, and context budgets are bounded. Backup names
  include microseconds, date inputs are validated consistently, packaged docs
  match the root command list, and package metadata uses current
  SPDX/data-discovery syntax.
- `init` is now a locked reconciliation operation, so rerunning it repairs an
  interrupted first run instead of treating a lone `.irag/` directory as
  success. Setup writes are atomic, malformed/non-UTF-8 model process output
  is handled cleanly, `doctor` checks logical database relations in addition
  to storage integrity, and the site builder refuses to recursively replace
  directories it does not own.
- `irag mcp` is a dependency-free stdio Model Context Protocol server with 13
  generic memory, provenance, update, contradiction, and session tools. It
  speaks JSON-RPC on stdin/stdout and is not coupled to Claude Code; Codex,
  Claude, Cursor, Windsurf, VS Code, and any other stdio MCP client can use the
  same server command.
- Model invocation now goes through explicit Claude, Codex, agy, Ollama, and
  custom adapters with bounded retries, timeout/error normalization, optional
  model selection, and local run/cost telemetry. Database evolution is an
  ordered v1→v6 migration history with an automatic SQLite backup before an
  upgrade and a hard refusal to open a newer schema with an older binary.
- Large-repository updates reuse the structural index when topology is stable,
  reparse only changed source files, bound synthesis inputs around imports and
  symbol windows, and preserve page/revision/contradiction identity across Git
  and snapshot renames. Replayed Git rename events are idempotent, and merge
  commits are diffed explicitly against their first parent instead of skipped.
- Dashboard updates are durable database jobs. A 202 response returns the job
  ID, progress streams over SSE, the browser reconnects or polls as needed,
  and a reload resumes the same job. Startup only abandons stale jobs after it
  acquires the repository lock, so a second dashboard cannot kill live work.
- Every contradiction has an **Agent brief** action, plus one master action for
  the complete open set. Both open escaped, self-contained HTML in a new tab
  with source facts, current memory, repair instructions, clipboard copy, and
  download. On mobile these render as action-first evidence cards.
- File/folder synthesis prompts now explicitly retain public contracts, state
  and data flow, invariants, security/failure behavior, dependencies, and
  recent-change causality. Session endings store a separate deterministic
  `critical_context` JSON ledger, so a weak or truncated model narrative cannot
  erase decisions, lessons, revisions, commits, or touched files.

Verification on the finished tree:

```text
focused unittest suite on local Python 3.11 and 3.14      13 tests passed
sh tests/test_smoke.sh on local Python 3.14               SMOKE TEST PASSED
Ruff + mypy + compileall + diff --check                   passed (silent)
site: all 11 pages × desktop/mobile Chromium              no JS errors/overflow
dashboard Health + report desktop/mobile Chromium         no JS errors/overflow
live dashboard update: 202 → SSE → reload persistence     passed
sdist + wheel build; isolated wheel install on 3.11       passed
```

The frontend audit used the repository's existing visual system and made
narrow reliability/accessibility corrections; it did not redesign the
product. Its mechanical detector reports no remaining findings.

---

## 0. Orientation — read this before your first command

### 0.1 Where the repository actually is

```
/Users/karangarg/Desktop/iRag/irag-4.1.1/          <- NOT the repo. NOT a git root.
/Users/karangarg/Desktop/iRag/irag-4.1.1/irag/     <- THE REPO. cd here first.
/Users/karangarg/Desktop/iRag/irag-4.1.1/irag/irag/ <- the Python package
```

A session may open with the working directory set to the **parent**
(`irag-4.1.1`). That directory is not the project, and it is not
repo-less either — which is the trap:

```
$ cd /Users/karangarg/Desktop/iRag/irag-4.1.1
$ git rev-parse --show-toplevel
/Users/karangarg/Desktop            <-- the owner's ENTIRE Desktop is a git repo
```

So `git status`, `git log`, `git push` run from the parent operate on the
owner's personal Desktop repository, not on irag. This has already cost
one session seven minutes of a CI-wait loop spinning on
`failed to determine base repo`.

**Rule: every git or `gh` command starts with an explicit
`cd /Users/karangarg/Desktop/iRag/irag-4.1.1/irag`.** Do not rely on the
inherited working directory. `cd` does not persist reliably between tool
calls in this harness.

### 0.2 Which Python

There are two interpreters and they are not interchangeable:

| | path | version | has irag? |
|---|---|---|---|
| Default `python3` | `/opt/homebrew/bin/python3` | 3.14.6 | **no** |
| irag's interpreter | `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3` | 3.13.0 | yes (editable) |

The `irag` console script has that python.org 3.13 path in its shebang.
So:

- `irag ...` — always works.
- `python3 -m irag ...` — works **only** from inside the repo root, where
  the `irag/` package directory shadows the missing install. This is what
  `tests/test_smoke.sh` relies on (it sets `PYTHONPATH="$IRAG_SRC"`).
- `python3 -m pip show irag` — reports **not found**. That is expected,
  not a broken install.
- `ruff check ...` and `mypy irag` are the current static gates. They are
  available in the development environment; CI installs them explicitly.

### 0.3 Verifying which copy of irag you are editing

There are several irag clones on this machine, and which one the `irag`
command serves **has flipped before**. `import irag` from inside the repo
root proves nothing — the package directory shadows the installed one.
Always verify from a neutral directory:

```bash
cd ~ && /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
  -m pip show irag | grep -E "^(Version|Editable project location)"
```

Expect `Editable project location: /Users/karangarg/Desktop/iRag/irag-4.1.1/irag`.
If it points anywhere else, your edits are not what the `irag` command runs.

**Note the version reported there is stale and that is normal.** pip
records the version at the last `pip install -e .`; it currently says
`4.40.1` while the code is `4.47.0`. `irag --version` reads
`irag/__init__.py` and is authoritative. Only a reinstall refreshes pip's
copy, and a reinstall is unnecessary for ordinary source edits — editable
installs pick those up immediately.

### 0.4 The shell is zsh, and it does not word-split

This has produced **three separate false bug reports** in past sessions:

```bash
c="search store"
irag $c            # zsh passes ONE argument: "search store"  -> exit 2
```

zsh has `SH_WORD_SPLIT` off by default. Unquoted parameter expansion is
not split into words the way bash splits it. When looping over commands in
a test harness, run the loop under `sh`, or use a real array. If a command
mysteriously exits 2 in a loop but works when typed, this is why —
irag is fine, the harness is wrong.

Two related traps, both encountered for real:

- **`for path in ...`** — `path` is a special zsh variable tied to `PATH`.
  Assigning it destroys the shell's PATH mid-script and every subsequent
  command fails with `command not found`. Use any other name.
- **`$?` after a pipe** reports the *last* command's status, not the
  interesting one. `cmd | tail -3; echo $?` gives you `tail`'s exit code.
  zsh's array is `$pipestatus`, not bash's `$PIPESTATUS`.

---

## 1. Git, GitHub, and how to ship a change

### 1.1 The account

| | |
|---|---|
| GitHub account | **`Karang1908`** (the owner's only account here) |
| Repo | `https://github.com/Karang1908/irag` |
| Default branch | `main` — work happens directly on it |
| Local `user.name` | `Karang1908` |
| Local `user.email` | `70532241+Karang1908@users.noreply.github.com` |
| Auth | `gh` CLI, token in the macOS keyring, HTTPS protocol |
| Token scopes | `gist`, `read:org`, `repo`, `workflow` |

The email is GitHub's privacy-preserving noreply address. **Do not
"correct" it to a real address** — that is deliberate, and it is what
attributes commits to the account.

Verify auth before a push:

```bash
gh auth status          # expect: Logged in to github.com account Karang1908
```

There is a `plugin:github:github` MCP server configured that fails to
connect (`Authorization header is badly formatted`). Ignore it — the `gh`
CLI works and is what everything here uses. Do not conclude GitHub access
is unavailable because that server is red.

### 1.2 Committing

The owner's global instructions say never to commit or push unless asked,
and that being asked to commit on a default branch means branching first.
**In this repository the established, repeatedly-confirmed practice is to
commit straight to `main` and push** — the owner has asked for exactly
that dozens of times, CI runs on `main`, and the docs site deploys from
`main`. Follow that. Do not open branches or PRs unless asked.

Commit style, matched from history:

```
<type>(<scope>): <imperative summary, lowercase, ~70 chars>

Prose body explaining WHY, and what evidence supports it. Name the
mechanism, not just the symptom. If a report's diagnosis was wrong,
say so and give the real cause. Wrap at ~72 columns.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_018PXCsrYEFBpt4vyFEAGAph
```

Types in use: `fix`, `feat`, `docs`, `perf`, `site`, `copy`. Scopes are
module names (`structure`, `linter`, `facts`, `cli`, `synthesis`,
`update`, `impact`, `resolve`) or omitted for cross-cutting changes.

Bodies here are long and explanatory by house style — several paragraphs
naming the defect, the mechanism, the fix and the verification. Match
that; do not write one-line bodies.

Use a heredoc so the body survives intact:

```bash
cd /Users/karangarg/Desktop/iRag/irag-4.1.1/irag
git add -A && git commit -q -F - <<'EOF'
fix(linter): one-line summary

Body.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git push origin main
```

### 1.3 CI — what must be green

`.github/workflows/ci.yml` runs on every push to `main` and every PR:

1. **Static analysis** — `ruff check irag site tests` and
   `mypy irag`, both silent except their success summaries.
2. **Focused tests** — the unittest suite on native Ubuntu, macOS, and Windows
   runners with Python 3.11 and 3.14.
3. **Smoke test** — `sh tests/test_smoke.sh` on Ubuntu with Python 3.11,
   3.12, 3.13, and 3.14; each must print `SMOKE TEST PASSED`.
4. **Package build** — `python -m build` on Python 3.14.

The matrix has caught real defects that passed locally. Run the focused tests,
smoke suite, and both static analyzers locally before pushing (§3.3), not only
a syntax compile.

`.github/workflows/pages.yml` ("Docs") builds and deploys the site. It has
`workflow_dispatch`, so you can trigger it manually:

```bash
gh workflow run pages.yml --repo Karang1908/irag --ref main
```

### 1.4 The docs site and repository visibility

**Live URL: https://karang1908.github.io/irag/** — verified HTTP 200 on
2026-09-06. `gh repo view Karang1908/irag` reports the repository is public.

The repository has moved between public and private before, so keep the failure
mechanism documented rather than re-diagnosing it:

- On the free plan, GitHub Pages only serves **public** repos.
- Making the repo private does not merely pause Pages — it **destroys the
  Pages resource** (`has_pages: false`, `GET /repos/.../pages` → 404).
- Making it public again does **not** bring it back, and the Pages
  settings also revert to `build_type: legacy`, which would serve the repo
  root instead of the built site.
- The Docs workflow keeps *building* successfully throughout; only the
  deploy step fails with `Ensure GitHub Pages has been enabled`.

So if a future visibility change makes the Docs deployment red, that can be
expected infrastructure behavior rather than a code defect. Check visibility
and the Pages resource before changing `site/build.py`.

Recovery after the owner has made the repo public again (only they can change
visibility; do not do that on their behalf):

```bash
gh api -X POST repos/Karang1908/irag/pages -f build_type=workflow
gh workflow run pages.yml --repo Karang1908/irag --ref main
# then verify every page returns 200, not just the root
```

---

## 2. What irag is

irag is a local, dependency-free knowledge base that gives AI coding
agents persistent, verified memory of a codebase. Every file and folder
gets an LLM-written summary page, versioned append-only on every change,
mechanically fact-checked against the actual code (wrong claims become
queryable *contradiction* rows rather than silent lies), and served to
agents through a universal MCP server, CLI, generated `CLAUDE.md`/`AGENTS.md`,
live dashboard, and Obsidian graph. A conversation logger records each coding
session as a row — what changed, what was decided, a readable narrative, and a
lossless critical-context ledger — so a fresh chat resumes a project for a few
hundred tokens instead of thousands of tokens of re-exploration. Everything
lives in one SQLite file
(`.irag/memory.db`) that any agent on any machine can mount.

**v4.47.0. 10,414 lines of Python. Zero runtime dependencies** — stdlib
only (`sqlite3`, `http.server`, `ast`, `tomllib`, `subprocess`). Package
`irag`, console command `irag`, config dir `.irag/`, **48 commands**.

### 2.1 The one sentence that explains every design decision

**"The model never does bookkeeping."** Locating, counting, dating,
diffing, scheduling, verifying — all SQL. The LLM's only job is writing
prose (page synthesis) and optionally comparing prose to fact (the
`lint --llm` tier). Whenever you are tempted to have the LLM decide
something structural — is this file important, has it changed enough,
what should I summarise next — stop: that belongs in a query or a parser.
This is why the linter, staleness scoring, retrieval ranking, session
windowing and the dependency graph are deterministic and free.

The second principle: **memory is data with a prose projection, not
prose.** `CLAUDE.md`, the dashboard and the Obsidian vault are read-only
views generated from the database. Nothing is ever the source of truth
except `.irag/memory.db`. Never let a generated artifact become
authoritative, and never let the LLM write into a table that is not
`revisions` or the explicitly non-authoritative session narrative.

The third, learned the hard way this year: **a confident wrong answer is
worse than no answer.** Three separate bugs were all the same shape —
`impact` saying "change is contained" about a file that was depended on,
`check` reporting "2/2 verified" with a disproved claim, `update` printing
"update done" while a page was permanently stuck. Each *looked* like
success. When a code path cannot know, it must say so and exit non-zero.

### 2.2 Module map

| Module | LOC | Owns |
|---|---:|---|
| `cli.py` | 1833 | every command, argument parsing, repository coordination |
| `synthesis.py` | 1000 | critical-context prompts, bounded page writing, injection scrubbing |
| `dashboard.py` | 981 | stdlib HTTP server, durable jobs/SSE, JSON API, CSRF guard |
| `ingest.py` | 892 | change detection, merge/rename continuity, ignoring, secrets, event queue |
| `structure.py` | 812 | incremental symbol + dependency extraction for 10 languages |
| `linter.py` | 658 | race-safe fact-checking pages against code; contradictions |
| `db.py` | 645 | v1→v6 migrations/backups, schema, triggers, safe JSON helpers |
| `sessions.py` | 485 | attributed diary, narrative, deterministic critical-context ledger |
| `retrieval.py` | 360 | ranking, tiering, budget, FTS search |
| `obsidian.py` | 353 | vault projection |
| `mcp.py` | 319 | stdio JSON-RPC MCP protocol and 13 universal tool contracts |
| `doctor.py` | 301 | self-diagnosis |
| `config.py` | 272 | defaults, TOML merge, provider and semantic validation |
| `provenance.py` | 243 | `why` / `asof` / `diff` |
| `providers.py` | 250 | Claude/Codex/agy/Ollama/custom adapters, retries, telemetry |
| `facts.py` | 204 | executable memory |
| `reports.py` | 183 | safe self-contained contradiction repair briefs |
| `check.py` | 133 | the CI gate |
| `hooks.py` | 132 | git + optional Claude Code hook wiring |
| `stats.py` `tokens.py` `export.py` | 280 | shared metrics, token estimation, guide install |
| `locking.py` | 72 | cross-platform repository operation lock |

### 2.3 Subsystems added in the 4.41–4.47 line

These are recent and less battle-tested than the core:

- **Executable memory** (`facts.py`, `irag verify`) — a claim stored with
  the shell command that proves it. `irag check` can re-run them. This is
  the only memory irag can *re-establish* rather than trust.
- **Concept pages** (`irag topic`) — a page for something that spans files
  sharing no folder. The one page type irag will not infer; membership is
  curated by hand.
- **Anti-facts** (`irag tried`) — dead ends, so the next session does not
  re-derive them.
- **Just-in-time context** (`irag brief`) — everything known about one
  file, wired to the `PreToolUse` hook so it lands immediately before an
  edit rather than at session start.
- **Auto-capture** (`irag capture`, `irag candidates`) — drafts a
  candidate lesson when a Bash command fails, via `PostToolUse`.
- **Withdrawal** (`irag forget`) — drops a page from live serving while
  keeping its history.
- **Universal MCP** (`mcp.py`, `irag mcp`) — thirteen stdio tools expose the
  same deterministic memory operations to any MCP-capable coding agent.
- **Provider adapters** (`providers.py`) — one normalized invocation boundary
  for Claude, Codex, agy, Ollama, and user-defined commands.
- **Contradiction packets** (`reports.py`) — one-click per-item or master HTML
  handoffs containing verified source structure and explicit repair gates.
- **Durable update jobs** (`dashboard.py`) — database-backed progress with SSE,
  reconnect/poll fallback, restart-safe history, and repository-lock ownership.

### 2.4 Hooks that `irag claude-setup` installs

| Event | Command | Purpose |
|---|---|---|
| `SessionStart` | `irag session-begin` + `irag context --budget 3000` | memory in |
| `Stop` | `irag update --limit 50` | memory out, after every turn |
| `SessionEnd` | `irag session-end` | diary |
| `PreToolUse` (Edit\|Write\|NotebookEdit) | `irag brief -` | just-in-time |
| `PostToolUse` (Bash) | `irag capture --quiet` | draft a lesson on failure |

All are `|| true` and silent — a hook must never block the user's tool
call. That is also why hook-path failures are invisible, which is why
`doctor` now self-tests the payload parser.

---

## 3. Development workflow

### 3.1 The loop

1. Read the module(s) fully before editing. Most are 100–700 lines and
   self-contained.
2. **Reproduce the bug before fixing it.** Non-negotiable here — several
   reported bugs in this project did not reproduce, and the real cause was
   elsewhere. Fixing an unreproduced report means changing working code.
3. Make the smallest correct change.
4. Lint, test, falsify (below).
5. Update docs **and** their shipped copies (§3.5).
6. Bump the version in **both** `irag/__init__.py` and `pyproject.toml`.
7. Commit and push (§1.2).

### 3.2 Testing against the mock LLM — never a real key

`tests/mock_llm.py` reads a prompt on stdin and returns fixed-shape fake
pages. It deliberately hallucinates a nonexistent `src/ghost.py`, so the
fact-checker is *proven* to catch it rather than assumed to. Point a
scratch project at it:

```bash
python3 - "$IRAG_SRC" <<'EOF'
import sys, pathlib
cfg = pathlib.Path(".irag/config.toml"); t = cfg.read_text()
cfg.write_text(t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"'))
EOF
```

**The mock ignores instructions.** It returns the same template whatever
you ask, so it cannot verify prompt-following. A test that depends on the
model obeying (for example "writes a tombstone when the file is gone")
will appear to fail when the product is fine. One session nearly
"fixed" a non-bug this way. Assert on database state and exit codes, not
on model prose.

### 3.3 The four checks before any commit

```bash
cd /Users/karangarg/Desktop/iRag/irag-4.1.1/irag
python3 -m compileall -q irag site
ruff check irag site tests                 # rule set pinned in pyproject
mypy irag
python3 -m unittest discover -s tests -p 'test_*.py' -v
sh tests/test_smoke.sh                     # must print SMOKE TEST PASSED
```

The focused unittest suite owns protocol framing, provider contracts,
migrations/backups/downgrade refusal, contradiction race safety, rename
continuity, durable jobs, safe reports, and session critical-context
retention. CI runs it on native Ubuntu, macOS, and Windows runners.

The integration suite is `tests/test_smoke.sh`: ~2,422 lines and **40 guard
blocks**. It runs entirely on the mock — costs nothing, takes a couple of
minutes. It uses `set -e`, which has two consequences worth knowing:

- A block that fails **silently aborts the whole run** with no `FAIL:`
  line. If the suite exits non-zero but printed no failure, that is why.
  Always check `$?`, not just the tail of the output.
- A command substitution that exits non-zero aborts the script. Anything
  expected to fail (`irag check` on a fresh project exits 1) needs
  `|| true` inside the capture.

If the dashboard JS was touched, syntax-check it too — a broken script
silently kills the whole SPA:

```bash
python3 - <<'PY' > /tmp/d.js
import pathlib, re
h = pathlib.Path("irag/assets/dashboard.html").read_text()
print("\n".join(re.findall(r"<script[^>]*>(.*?)</script>", h, re.S)))
PY
node --check /tmp/d.js
```

### 3.3b The lint gate is pinned, and that is deliberate

CI installs `ruff==0.16.6` and `mypy==2.3.1`, and `pyproject.toml` selects
`["E4", "E7", "E9", "F"]` explicitly. Both pins exist because the gate was
briefly unpinned and it broke immediately: a local ruff 0.11.9 reported
`All checks passed` while CI's freshly-installed 0.16.6 found **109
errors** in the same tree, from rule families nobody had chosen — bandit,
refurb, pylint, isort. Nothing in the code had changed; a newer ruff had
simply widened its defaults.

So: an unpinned linter is not a gate, it is a subscription to whatever
upstream decides this month. If you upgrade the pin, do it as its own
commit, read what the new findings actually are, and fix or explicitly
ignore them — do not widen `select` and leave the tree red. Verify against
the pinned version, not whatever is on your PATH, or you will reproduce
exactly this.

### 3.4 Falsify every guard you add — this is the house rule

**A regression test that passes against the broken code is worse than no
test**, because it certifies a fix that isn't there. This has happened
**four times** in this project. The discipline:

1. Add the guard; watch it pass.
2. **Revert the fix** (edit the source, don't just imagine it).
3. Re-run the suite and watch the guard **fail, for the stated reason**.
4. Restore the fix; confirm green.

If the guard still passes with the fix removed, it is testing nothing.
Two real examples of how subtly this goes wrong:

- A guard asserted on the *open contradiction set*, which a connect-time
  cleanup repaired — so it passed with the suppression logic deleted.
- A guard for the facts-execution vulnerability flipped the `DEFAULTS`
  dict, but the fixture's `config.toml` set the value explicitly and
  overrode it. The vulnerable default was never exercised. The fix was to
  strip the key entirely so the built-in default decides — which is also
  the real-world case, since a project created before that release has no
  such key.

When you neuter a multi-part fix, neuter **each part separately**; the
first assertion to fire masks the rest.

### 3.5 Documentation has three copies and nothing syncs them

| Location | Consumed by |
|---|---|
| `docs/*.md` | the website (`site/build.py`) |
| `irag/assets/docs/*.md` | the dashboard's **Docs** tab |
| `README.md` | GitHub |

They drift silently — a whole "Visualize" section once existed in one copy
and not the other. After any doc change:

```bash
cp docs/{ARCHITECTURE,CLI_REFERENCE,COMPARISON,MCP,SETUP,STORY}.md irag/assets/docs/
python3 site/build.py
```

Guards now enforce three things and will fail CI if you forget: the two
copies must be byte-identical, **every** CLI command must appear in
`docs/CLI_REFERENCE.md`, and `README.md`'s command list and its
`## Commands (N)` count must match the CLI. Add a command, and the suite
fails until it is documented in both places — deliberately.

`docs/HANDOFF.md` (this file) and `irag/assets/docs/{README,QUICKSTART}.md`
are **not** part of that sync set. HANDOFF is deliberately excluded from
the published site (see the comment in `site/build.py`) because it is
internal.

---

## 4. The security model — understand before you relax anything

irag reads source files, sends them to an LLM, and stores the result in a
database **that is meant to be committed and shared**. That combination
produced four real vulnerabilities in 2026. Each fix is load-bearing; none
is paranoia.

### 4.1 Executable facts are opt-in (`[check].fail_on_facts = false`)

`irag verify` stores a shell command in `.irag/memory.db`. When that file
is committed — which the design encourages — `git clone && irag check`
would execute whatever a contributor registered. This was demonstrated
end-to-end: a committed fact wrote a file on a fresh clone. So the default
is **off**, and the opt-in path prints every command before running it.

Do not "improve the ergonomics" by defaulting it on. The guard strips the
key entirely so the built-in default decides, which is the case that
matters for projects created before the fix.

### 4.2 Three classes of file are never read

Enforced in `ingest.is_ignored()`, regardless of any ignore file:

- **Dotfiles and dot-directories.**
- **Credential-shaped names** — `id_rsa`, `*.pem`, `*.key`, `*.p12`,
  `secrets.*`, `credentials*`, `service-account*.json`, `*.env`.
- **Symlinks whose target escapes the project.** A link to `~/.ssh/id_rsa`
  was otherwise read as ordinary source and its bytes went into the prompt
  and the database. `update` is the automatic path, so one symlink in a PR
  was enough.

`.gitignore` is honoured **in both git and snapshot mode**. Snapshot mode
walks the tree directly and is the mode for *every project that is not its
own git root*, so without this an ignored `id_rsa` was ingested. Negated
patterns (`!foo`) are skipped, which can only over-ignore.

### 4.3 Source files are untrusted input

File content is fenced with explicit `BEGIN/END UNTRUSTED FILE CONTENT`
markers, labelled as data rather than instructions, and the output
contract is **restated after** it so the last word is irag's. Any
instruction-shaped line surviving into a page (`ignore previous
instructions`, `curl … | sh`, a fake `<system>` block) is stripped before
the page is stored.

This matters because pages are injected into agents automatically — at
session start and by `PreToolUse` right before the file is edited. It is
memory poisoning with a delivery mechanism, and the linter cannot see it
because the payload is prose, not a path or a symbol.

`scrub_injection()` is a regex, not a semantic filter. It catches crude
shapes; the fencing and restated contract are the real defence.

### 4.4 The dashboard refuses cross-origin writes

Every mutating endpoint requires `Content-Type: application/json` — not a
CORS "simple" content type, so the browser must preflight and the
same-origin policy blocks it — and rejects a non-localhost `Origin`.
Before this, any page open in the same browser could POST to
`127.0.0.1` and force an `update` (real spend), a `rollback` (silent
memory corruption an agent then reads as truth), or unbounded backups.

**If you add a POST endpoint or a UI fetch, it must send that header.**
One existing UI call did not and had to be fixed alongside the guard.

### 4.5 Note for a public repo

`.irag/memory.db` is committable by design. When the repo is public, the
memory database is public with it — page summaries and any registered
facts included. Nothing sensitive is in this one, but it is worth a
conscious decision rather than a surprise.

---

## 5. Known gotchas — read before you "fix" these

1. **`sync` detects, `update` writes.** `irag sync` only queues events; it
   never writes a page version. `sync` prints a pointer to `update`
   whenever it queues anything. Recommend `update` everywhere.
2. **The structural scan gates on a tree fingerprint, not git HEAD.**
   Gating on HEAD meant uncommitted edits — a coding agent's normal state
   — never refreshed `symbols`/`deps`, feeding stale "ground truth" into
   prompts and making the linter flag real new symbols as missing.
3. **`sync()` is hybrid even inside git repos** — commits *and* a
   fingerprint diff every call, so uncommitted edits are never invisible.
   If that becomes slow on huge repos, make `_tree_hashes` cheaper; do not
   remove the fingerprint pass.
4. **Root resolution is directory-wise, never git-toplevel-first.** The
   owner's entire `~/Desktop` is a git repo; the old resolution made
   `irag init` in any Desktop subfolder adopt the whole Desktop — 1,376
   pages, hooks installed into the personal repo. `repo_root()` = nearest
   ancestor with `.irag`, else cwd. Git ingestion, hook install and the
   dirty count all gate on `ingest.git_rooted(root)`.
5. **No compaction or pruning, by explicit decision.** Unbounded history
   is the product, not a liability. Do not add automatic pruning. A manual
   `irag compact`, if ever requested, must never delete v1, the current
   revision, or anything a rollback or resolved contradiction references.
6. **`irag resolve` dismisses a FLAG; it never edits the page.** And since
   a manual dismissal now suppresses that claim permanently, dismissing a
   *genuine* error silences it forever with the wrong text still live.
   That is why `--undo` exists and why the dashboard button says "dismiss
   flag" with an inline Undo. Auto-resolutions stay re-raisable — a
   recurrence there is a real regression.
7. **`synthesize --subject` is an explicit force.** The trivial-change
   skip must not short-circuit it, or irag recommends an escape hatch that
   then refuses to act. That regression happened once already.
8. **Deleted pages are withdrawn, not destroyed.** `deleted_at` excludes a
   page from search, context and folder rollups while `asof`/`why` keep
   the history. A rename is a delete plus an add, which is why this
   matters more than it sounds.
9. **`[staleness].dependency` propagates to importers** through the `deps`
   table, one hop — not just to manifest files, which is all it used to
   do. Page bodies deliberately record cross-file claims, so a
   neighbour's interface change invalidates them.
10. **Symbol extraction under-reports rather than invents.** The table is
    handed to the model as "STRUCTURAL FACTS (ground truth)", so a missing
    symbol weakens a check while an invented one causes a false
    contradiction. When in doubt, over-capture. Python and JS both emit
    `Owner.method`; the linter matches `name = ? OR name LIKE '%.sym'`.
11. **`irag check` runs `--skip-facts`-able shell commands only when
    opted in** — see §4.1. Of the three fact outcomes only `fail` gates;
    `error` (a tool missing on this machine) says nothing about the claim
    and must never break CI.
12. **A `touch` with no content change is not drift.** Nothing rewrites an
    unchanged file, so its page timestamp never advances and the warning
    could never be cleared. Drift compares the comment-stripped
    fingerprint, and `sync` backfills a baseline for pages predating that
    column.

---

## 6. Non-negotiables — do not regress these under any refactor

- **Revisions are append-only during normal operation.** Never
  `UPDATE`/`DELETE` a `revisions` row except for the user's explicit,
  destructive `irag forget --purge`. Rollback inserts a *new* row with the
  old content.
- `pages.current_revision_id` moves only via the `revisions_advance`
  trigger.
- **The database is the only source of truth.** `CLAUDE.md`, the
  dashboard and the vault are regeneratable from it, never the reverse.
- **Zero runtime dependencies** for the core package. This is load-bearing
  for the "any agent, any machine" story. Anything else goes behind an
  optional extra with a guarded import.
- **Nothing on the read path calls a model.** `search`, `map`, `impact`,
  `context`, `recap`, `why`, `asof`, `brief`, `suggest` are SQL and
  parsing. This was verified with a sentinel LLM command that touches a
  file when invoked: the six read commands never fired it, `irag ask`
  did, and irag's own meter read `est. LLM tokens spent : 0`. That
  property is the product's headline claim — do not put a model call on
  that path.
- Every LLM call needs a non-LLM fallback where the product still works.
- **`irag doctor` must stay accurate.** It is what `/CLAUDE.md` tells
  agents to run first. New failure mode anywhere ⇒ new check in
  `doctor.py`.
- **A path irag does not track gets an error, not a verdict.** `impact`
  exits 2 and says so rather than answering "change is contained".

---

## 7. What is verified, and what is not

**Verified by running it**, repeatedly, on this machine:

- The full smoke suite: 40 end-to-end guard blocks, including explicit
  concurrency, migration interruption, malformed input, and stale-live-view
  regressions.
- Thirteen focused unittests covering MCP JSON-RPC framing/tool calls, provider
  argument construction/retries, ordered storage migration and backup,
  contradiction uniqueness, merge commits, snapshot and replayed-Git rename
  continuity, durable job persistence, escaped HTML handoffs, and lossless
  session context.
- Zero-token read path (sentinel test, §6).
- Parallel synthesis produces byte-identical results to sequential — same
  page count, revision count, no duplicate versions — and a failing model
  marks every event failed and self-heals on the next run.
- Concurrency: `update` under ten simultaneous dashboard requests, zero
  lock errors, zero tracebacks.
- Schema migration through ordered v1→v6 history, including an automatic
  pre-upgrade backup, a pre-upgrade human resolution correctly backfilled,
  duplicate contradiction cleanup, and refusal of an unsafe downgrade.
- All 48 commands respond; the dashboard routes used by every one of its nine
  views and interactive controls are exercised.
- The rendered static site builds all eleven pages, and every route was checked
  at desktop/mobile widths in Chromium. All nine dashboard views had prior
  desktop/mobile coverage, and the changed Health surface plus single/master
  reports were rechecked at 1440 px and 390 px with no console errors or body
  overflow. Mobile contradiction actions are directly visible.
- A live dashboard update returned 202, streamed to completion over SSE, and
  remained queryable after a full page reload. Report clipboard copy worked in
  Chromium, and individual/master scope stayed distinct with one or many rows.
- MCP initialize, tools/list, tools/call, notifications, batches, malformed
  requests, and session attribution run against the real stdio server. The
  built wheel includes the MCP/provider/report modules and documentation.

**Not verified, and you should not claim otherwise:**

- **Summary quality with a real LLM.** Everything automated runs on the
  mock. The pipeline, the fact-checker and the gates are proven; whether
  the prose is *good* on a real codebase is untested here. This remains
  the highest-value next step.
- **`[llm].parallel > 1` against a real LLM CLI.** Correct against the
  mock; left defaulting to 1 because whether a given CLI tolerates
  concurrent invocations is unknown.
- **Live calls through every provider adapter.** Local CLI help was inspected
  and argument construction is covered with subprocess fakes, but this audit
  did not spend tokens against Claude, Codex, agy, or Ollama.
- **The new native OS CI matrix.** Its workflow is configured for Ubuntu,
  macOS, and Windows, but this working tree is uncommitted, so those hosted
  jobs have not run yet. Local macOS checks passed on Python 3.11 and 3.14.

---

## 8. Deferred — do not build unless asked

- **Authenticated Streamable HTTP MCP transport.** The shipped stdio server is
  the portable local integration. Remote/multi-user transport needs an auth,
  origin, session, and deployment threat model before it is exposed.
- **Compaction** — see gotcha #5. Opt-in only, if ever.
- **A real-LLM evaluation of summary quality** — arguably higher priority
  than any new feature.
- **Destructive or renaming schema migrations.** Additive upgrades are
  serialized, transactional, exercised under interruption, and safe; there is
  deliberately no generic mechanism for destructive transformations.
- **Confidence scoring from lint history** (`pages.confidence` exists and
  is unused).
- **CI webhooks** posting contradictions onto a PR.

---

## 9. Open items handed to you

1. **One candidate lesson** is parked in the owner's
   `ultimatenetworkscan` project: `nmap -O 127.0.0.1 exited 1 —
   QUITTING!` on `scanner.py`. That is a genuine finding, not debris —
   promote it with `irag learn` or discard it with
   `irag candidates --discard 37`. The owner's call; leave it alone
   otherwise.
2. **Java bodiless-declaration extraction** covers interface, `abstract`
   and `native` methods, but was written against synthetic fixtures. A
   real Java project would be a better test.

---

## 10. If you read nothing else

```bash
# 1. get into the actual repo — the parent is the owner's Desktop repo
cd /Users/karangarg/Desktop/iRag/irag-4.1.1/irag

# 2. before any commit
python3 -m compileall -q irag site
ruff check irag site tests/mock_llm.py
mypy irag
sh tests/test_smoke.sh        # must print SMOKE TEST PASSED

# 3. commit to main as Karang1908, long explanatory body
git add -A && git commit -q -F - <<'EOF'
fix(scope): what changed

Why, and the mechanism.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
EOF
git push origin main
```

And the three habits that matter more than any of the above:

1. **Reproduce before you fix.** Several reported bugs here did not
   reproduce, and the real cause was somewhere else entirely.
2. **Falsify every guard you write** by reverting the fix and watching it
   fail. Four guards in this project passed against broken code.
3. **When a path cannot know, make it say so.** Most of the worst defects
   in this codebase were confident wrong answers, not crashes.
