# irag — Handoff Document

**For: an AI coding agent picking up development on irag.** Read this
fully before touching code. It tells you what irag is, how to work on it,
which account to commit as, what is actually verified versus assumed, and
which traps in this specific environment will waste your time if nobody
warns you.

This document is about **building irag**. If you want to know how to *use*
irag inside a project, read `/CLAUDE.md` at the repo root — that is the
operator manual for agents consuming irag's memory, not developing it.

Accurate as of **v4.45.0**, 2026-09-04. Every fact below was verified by
running it on this machine on that date. Where something is inferred
rather than observed, it says so.

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
- `python3 -m pyflakes irag/*.py` — fails; pyflakes is installed only
  under 3.13. Use the full path (§3.3).

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
`4.40.1` while the code is `4.45.0`. `irag --version` reads
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

1. **Lint** — `pyflakes irag/*.py`, must be silent.
2. **Smoke test** — `sh tests/test_smoke.sh`, must print `SMOKE TEST PASSED`.
3. **Package build** — `python -m build`.

Matrix: Python **3.11, 3.12, 3.13**. It has caught real defects that
passed locally — most recently an f-string with no placeholders, which
lint rejects. **Run pyflakes locally before pushing** (§3.3); it is
installed only under 3.13, so the bare `python3 -m pyflakes` will mislead
you into thinking it is unavailable.

`.github/workflows/pages.yml` ("Docs") builds and deploys the site. It has
`workflow_dispatch`, so you can trigger it manually:

```bash
gh workflow run pages.yml --repo Karang1908/irag --ref main
```

### 1.4 The docs site, and the recurring private-repo cycle

**Live URL: https://karang1908.github.io/irag/** — but as of 2026-09-04 it
is **down (404), because the repo is private again.**

This has now happened three times and will happen again, so understand the
mechanism rather than re-diagnosing it:

- On the free plan, GitHub Pages only serves **public** repos.
- Making the repo private does not merely pause Pages — it **destroys the
  Pages resource** (`has_pages: false`, `GET /repos/.../pages` → 404).
- Making it public again does **not** bring it back, and the Pages
  settings also revert to `build_type: legacy`, which would serve the repo
  root instead of the built site.
- The Docs workflow keeps *building* successfully throughout; only the
  deploy step fails with `Ensure GitHub Pages has been enabled`.

So a red "Docs" workflow while the repo is private is expected and is not
a code defect. **Do not go looking for a bug in `site/build.py`.**

Recovery, once the owner has made the repo public again (only they can do
that — do not change repository visibility yourself):

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
agents through a CLI, a generated `CLAUDE.md`, a live dashboard, and an
Obsidian graph. A conversation logger records each coding session as a
row — what changed, what was decided, an LLM narrative — so a fresh chat
resumes a project for a few hundred tokens instead of thousands of tokens
of re-exploration. Everything lives in one SQLite file
(`.irag/memory.db`) that any agent on any machine can mount.

**v4.45.0. ~8,300 lines of Python. Zero runtime dependencies** — stdlib
only (`sqlite3`, `http.server`, `ast`, `tomllib`, `subprocess`). Package
`irag`, console command `irag`, config dir `.irag/`, **45 commands**.

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
`revisions`.

The third, learned the hard way this year: **a confident wrong answer is
worse than no answer.** Three separate bugs were all the same shape —
`impact` saying "change is contained" about a file that was depended on,
`check` reporting "2/2 verified" with a disproved claim, `update` printing
"update done" while a page was permanently stuck. Each *looked* like
success. When a code path cannot know, it must say so and exit non-zero.

### 2.2 Module map

| Module | LOC | Owns |
|---|---:|---|
| `cli.py` | 1740 | every command, argument parsing, the update lock |
| `synthesis.py` | 984 | prompts, LLM invocation, page writing, injection scrubbing |
| `structure.py` | 720 | symbol + dependency extraction for 10 languages |
| `ingest.py` | 700 | change detection, ignoring, secrets, event queue |
| `dashboard.py` | 652 | stdlib HTTP server, JSON API, CSRF guard |
| `linter.py` | 651 | fact-checking pages against code; contradictions |
| `sessions.py` | 458 | the conversation diary and attribution |
| `db.py` | 422 | schema, triggers, additive migrations |
| `obsidian.py` | 352 | vault projection |
| `retrieval.py` | 322 | ranking, tiering, budget, FTS search |
| `doctor.py` | 285 | self-diagnosis |
| `provenance.py` | 222 | `why` / `asof` / `diff` |
| `facts.py` | 204 | executable memory |
| `config.py` | 138 | defaults, TOML merge |
| `check.py` | 129 | the CI gate |
| `hooks.py` | 108 | git + Claude Code hook wiring |
| `stats.py` `tokens.py` `export.py` | 228 | shared metrics, token estimation, guide install |

### 2.3 Subsystems added in the 4.41–4.45 line

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

### 3.3 The three checks before any commit

```bash
cd /Users/karangarg/Desktop/iRag/irag-4.1.1/irag
PY=/Library/Frameworks/Python.framework/Versions/3.13/bin/python3

python3 -m compileall -q irag/          # syntax
$PY -m pyflakes irag/*.py               # lint — MUST use the 3.13 path
sh tests/test_smoke.sh                  # must print SMOKE TEST PASSED
```

The suite is `tests/test_smoke.sh`: ~1,980 lines, **40 guard blocks**, the
only persisted regression test. It runs entirely on the mock — costs
nothing, takes a couple of minutes. It uses `set -e`, which has two
consequences worth knowing:

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
cp docs/{ARCHITECTURE,CLI_REFERENCE,COMPARISON,SETUP,STORY}.md irag/assets/docs/
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

- **Revisions are append-only.** Never `UPDATE`/`DELETE` a `revisions`
  row. Rollback inserts a *new* row with the old content.
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

- The full smoke suite: 40 guard blocks, every one falsified against a
  neutered build before being kept.
- Zero-token read path (sentinel test, §6).
- Parallel synthesis produces byte-identical results to sequential — same
  page count, revision count, no duplicate versions — and a failing model
  marks every event failed and self-heals on the next run.
- Concurrency: `update` under ten simultaneous dashboard requests, zero
  lock errors, zero tracebacks.
- Schema migration from a database with every added column and table
  stripped out, including a pre-upgrade human resolution correctly
  backfilled.
- All 45 commands respond; the dashboard serves all ten endpoints the UI
  calls.

**Not verified, and you should not claim otherwise:**

- **Summary quality with a real LLM.** Everything automated runs on the
  mock. The pipeline, the fact-checker and the gates are proven; whether
  the prose is *good* on a real codebase is untested here. This remains
  the highest-value next step.
- **The dashboard's appearance.** The Chrome extension was disconnected
  for every session that touched it. HTTP status, JSON payloads, HTML
  content and JS syntax are checked; nobody has looked at it rendering.
  The toast and the "dismiss flag" buttons in particular are verified as
  correct markup, not correct pixels.
- **`[llm].parallel > 1` against a real LLM CLI.** Correct against the
  mock; left defaulting to 1 because whether a given CLI tolerates
  concurrent invocations is unknown.

---

## 8. Deferred — do not build unless asked

- **MCP server.** The shape is ready: `stats.py`, `retrieval.serve()`,
  `sessions.recap_block()` are pure functions with no CLI coupling.
  Obvious tools: `get_context`, `search`, `ask`, `why`, `learn`. Gate
  behind an `irag[mcp]` extra.
- **Compaction** — see gotcha #5. Opt-in only, if ever.
- **A real-LLM evaluation of summary quality** — arguably higher priority
  than any new feature.
- **A schema migration system.** Currently additive column guards only,
  in `db.ensure_db()`; they are exercised and work, but there is no
  mechanism for a destructive or renaming migration.
- **Confidence scoring from lint history** (`pages.confidence` exists and
  is unused).
- **CI webhooks** posting contradictions onto a PR.

---

## 9. Open items handed to you

1. **The docs site is down** because the repo is private (§1.4). Recovery
   is two commands once the owner makes it public. Only they can flip
   visibility.
2. **`irag/assets/docs/README.md`** is a third copy of the README, stale
   relative to the root one. It is the in-app landing blurb rather than a
   mirror, so it is deliberately outside the sync guard — but somebody
   should decide whether it tracks the root README or stays its own thing.
3. **One candidate lesson** is parked in the owner's
   `ultimatenetworkscan` project: `nmap -O 127.0.0.1 exited 1 —
   QUITTING!` on `scanner.py`. That is a genuine finding, not debris —
   promote it with `irag learn` or discard it with
   `irag candidates --discard 37`. The owner's call; leave it alone
   otherwise.
4. **Java bodiless-declaration extraction** covers interface, `abstract`
   and `native` methods, but was written against synthetic fixtures. A
   real Java project would be a better test.

---

## 10. If you read nothing else

```bash
# 1. get into the actual repo — the parent is the owner's Desktop repo
cd /Users/karangarg/Desktop/iRag/irag-4.1.1/irag

# 2. before any commit
python3 -m compileall -q irag/
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pyflakes irag/*.py
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
