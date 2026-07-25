# Changelog

All notable changes to irag. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver
in spirit (no public API contract yet beyond the CLI).

## 4.38.0 — 2026-07-25

### Fixed — `irag why` presented superseded claims as current
`why_data` searched every revision and returned the best-ranked match with no
check against the page's current revision. Add a claim in v2, remove it in v3,
then ask:

```
$ irag why flibbertigibbet
claim matches page  : src/store.ts (src/store.ts)
revision            : v2 (2026-07-25 15:44:40, by test)
change summary      : added flibbertigibbet
```

Current memory says the opposite, and nothing in the output said so.

Searching all revisions is correct — provenance means tracing a claim to the
change that *created* it. But `why` is also the command an agent reaches for
to check whether memory asserts something, and "yes, here it is" from a
superseded revision is a wrong answer to that question. This is the one
command whose job is verifying claims, so the conflation matters more here
than anywhere else.

The result now carries `is_current`, `still_holds` and `current_version`, and
the CLI says which it is:

```
revision : v2 (…)  [superseded; current is v3, claim NO LONGER present
                    — memory does not assert this today]
revision : v1 (…)  [superseded; current is v3, claim still present]
```

The dashboard passes `why_data` straight through, so it gains the same fields.

### Changed
`why` printed `triggered by: human/rollback (no event recorded)` for any
revision without a triggering event, which reads oddly for revisions that were
neither. It now says `no event recorded (hand-written, rollback, or pre-dates
event logging)`.

## 4.37.0 — 2026-07-25

### Fixed — one agent's work was filed under another's name
The "exactly one session open means the write is that session's" rule made
keyless attribution work at all, but failed whenever the writer wasn't the
session's owner: with a Claude Code session open, an agy commit was recorded
in **Claude Code's** diary. Still false history in the record `irag recap`
feeds forward.

There is a sound inference available rather than a better guess. A session
opened with a real id belongs to an agent whose every call carries that id —
Claude Code's hooks supply `session_id` on each invocation. So a write
arriving with **no** key, while such a session is open, provably did not come
from that owner. Adoption is now limited to sessions that are themselves
unidentified (`auto-` keys), which is exactly the case where an unkeyed write
plausibly is the session's own.

A lone keyless agent is still credited with its work; a keyed session no
longer absorbs a stranger's.

### Fixed — `--stale` closed sessions that weren't stale
`begin()` and `_resolve_key` both treat "older than 12 hours" as the crash
threshold, but `session-end --stale` closed **every** open session regardless
of age — and the refusal message framed it as an age filter ("if none is
yours, they are stale"). A user following that advice could end a colleague
agent's live conversation seconds after it started.

`--stale` now applies the same 12-hour rule as everything else. `--all` is the
blunt instrument, named for what it does, with help text saying it can end a
live conversation. The threshold is a single `sessions.STALE_AFTER` constant
instead of being duplicated across two modules.

### Fixed — crashed keyed sessions were never reaped
`begin()` only closed rows carrying the same key, and a hook `session_id`
never recurs, so a crashed Claude Code session stayed open forever. Harmless
— nothing depended on those rows — but they accumulated in `irag sessions`.
Any session past the staleness threshold is now reaped regardless of key.

## 4.36.0 — 2026-07-25

4.35.0 made `session-end` refuse to guess between unidentified sessions, which
was right — but a refusal left the repo stranded, and the recovery instruction
it printed could not be followed.

### Fixed — the printed recovery step did nothing
The refusal listed session **ids** (`2, 1`) while `--id` matched only session
**keys**, and `irag sessions` never showed keys at all. Following the advice
exactly:

```
$ irag session-end --id 2
no open session          [exit 0]
```

Silent, successful, and wrong. Three changes: `--id` now accepts a session id
or a key, `irag sessions` shows the key of every open session, and an `--id`
that matches nothing exits **1** with a message instead of reporting "no open
session". The refusal itself now prints a copy-pasteable `--id` line per
session together with its key.

### Fixed — one refusal poisoned the repo permanently
Nothing reaped dangling sessions: `begin()` only closes rows carrying the same
key, so a session that refused to close (or crashed) stayed open forever. Once
two rows were open, `_resolve_key` could never again see "exactly one open",
so **every later agent silently lost attribution** — a solo agent working the
same repo afterwards reported "No file changes recorded this session." The
crash tolerance the original design had was lost when keying was introduced,
and only same-key recovery replaced it.

`_resolve_key` now ignores open rows older than 12 hours, which are crashes
rather than live conversations, and `irag session-end --stale` closes every
open session as interrupted — the deliberate escape hatch, named in the
refusal message. Verified: after two stranded sessions and a `--stale`, a solo
keyless agent is credited with its own work again.

### Known limitation
With two sessions genuinely open at once and neither passing `--id`, a write
still belongs to nobody. That is not a heuristic that can be improved — no
rule over "who is open" can identify the caller. `session-begin` prints the id
to use, the write commands accept it, and the agent guide instructs it; short
of that, no-history remains preferable to false history.

## 4.35.0 — 2026-07-25

Ends the session-attribution thread by removing the guesswork instead of
improving it. No rule over "which sessions are open" can identify the caller
when two agents are genuinely open at once — so irag now tells each agent its
id, and refuses to guess when it doesn't have one.

### Fixed — a second unidentified agent killed the first one's live session
`begin()` force-closed the whole keyless lineage as `interrupted`, on the
assumption that an open session must be a crashed predecessor. It isn't:
starting agy and then Cursor marked agy's *running* session `interrupted`,
and a later bare `session-end` then closed the survivor — the caller's own
session never closed properly and a stranger's was ended out from under it.
This was the original cross-close bug surviving in the two-keyless case.

Only sessions older than 12 hours are now reaped, which still cleans up after
a crash. A lingering stale row is a much smaller harm than ending a live
conversation.

### Fixed — `session-end` guessed between unidentified sessions
The fallback took the newest `auto-` session, which is not necessarily the
caller's. With more than one unidentified session open it now refuses,
naming the fix, rather than closing someone else's:

```
irag: several unidentified sessions are open (2, 1) and irag cannot tell
which is yours — closing one would end another agent's. Re-run with
'irag session-end --id <key>' using the id printed by 'irag session-begin'.
```

A single open session still closes with no id, so the ordinary solo case is
unaffected.

### Fixed — an agent was never told the key minted for it
`session-begin` printed only `session N opened`, so a caller that passed no
`--id` had no way to learn the key it was given and could never identify
itself afterwards. That is why the concurrent case stayed broken even after
`--id` was added to the write commands: the mechanism existed but the
information didn't. It now prints `session N opened (id: <key>)`, plus a hint
to pass it on. Verified end to end: with two sessions open, an agy that adopts
its printed id is credited with its own file while Claude Code correctly
reports none.

### Changed
The agent guide now instructs non-Claude-Code agents to generate one id per
conversation and pass it to `session-begin`, every `update`, and
`session-end`, with a worked example — and says plainly what breaks if they
don't.

## 4.34.0 — 2026-07-25

### Fixed — `cmd_update` threw away the key it had just resolved (regression)
4.33.0 made attribution strict and resolved a key in `_open()`. But
`cmd_update` then called `set_active_key(_session_key(args))`, which is
`None` without a hook payload — and `set_active_key` did
`_ACTIVE_KEY = key or None`, so it *cleared* the key `_open()` had just
resolved. With strict matching, those unstamped rows then matched nothing:

```
open session key:       auto-f415705210364f4b
_open() resolved     -> auto-f415705210364f4b     correct
cmd_update override  -> None                      discarded
session reports:        "No file changes recorded this session."
```

One session open, one file changed, and the diary said nothing happened —
defeating the intent of the previous fix, and hitting exactly the cases
`_resolve_key` was written for: manual `irag update` and payload-less agents.

`set_active_key` is now **set-only** — a falsy key is ignored rather than
clearing a resolved one. That removes the class of mistake instead of guarding
one call site. `sessions.begin()` additionally claims the process for the
session it just opened, including a minted key.

### Fixed — a concurrent writer had no way to identify itself
With two or more sessions open, `_resolve_key` cannot infer the writer and
mints a process key nobody owns, so the work is credited to nobody. `--id`
existed on `session-begin`/`session-end` but not on any *write* command, so
there was no workaround. It is now accepted by `update`, `sync`, `synthesize`
and `ingest-commit`, and an explicit `--id` is applied in `main()` before
anything opens the database, so it always wins over `_open()`'s guess.
Measured with two sessions open: `agy --id conv-AGY` is credited with its own
file, and Claude Code correctly reports none.

### Fixed
`_window_facts`'s docstring still described the `OR session_key IS NULL`
clause three lines above the comment explaining its removal. Text only.

## 4.33.0 — 2026-07-25

### Fixed — attribution was still wrong in the exact case it was built for
4.32.0 stamped events and revisions with a session key, but allowed
`session_key = ? OR session_key IS NULL` so a manual `irag update` wasn't
lost. An agent that supplies no hook payload (agy, Cursor) writes *only*
unstamped rows — so every keyed session still swept up its work. The
motivating scenario, Claude Code alongside agy, was unfixed.

Three changes make a key the normal state rather than the exception:

- **Every session gets one.** `begin()` mints `auto-<random>` when none is
  supplied, so "no key" no longer exists.
- **Every command resolves one.** `_open()` sets it once, covering `sync`,
  `learn`, `record-decision`, `ingest-commit` and `synthesize` — previously
  only `update`, `session-begin` and `session-end` did, so `irag learn` and
  `irag record-decision`, which the agent guide explicitly tells agents to
  run, always produced unattributed revisions. With exactly one session open
  the write is unambiguously that session's; with none or several it gets a
  process-unique key, so it belongs to nobody rather than to whoever else
  happened to be open.
- **Two `INSERT INTO revisions` sites were missing the column entirely** —
  `learn`/`record-decision` and `rollback`. Both now stamp it.

The diary window is correspondingly strict (`session_key = ?`, no `IS NULL`).
Measured: a lone agy still gets `Changed 1 file(s): agy_file.py`; running
keyed Claude Code alongside keyless agy, neither claims the other's work.

Fixing this surfaced two consequences of minting keys, both handled: a keyless
agent could no longer close its own session (the old lookup searched for
`session_key IS NULL`, which now matches nothing), and a crashed keyless
session would never be cleaned up. `end()` without an id now closes the sole
open session when there is exactly one, and `begin()` still clears the
keyless lineage, which minted keys identify by their `auto-` prefix.

### Changed
- The `asof` rejection message claimed unpadded dates are refused; they are
  accepted and normalised. Only the text was wrong.
- The agent guide now tells non-Claude-Code agents to pass `--id` to
  `session-begin`/`session-end` when anything else may be working the same
  repo, and says what happens if they don't.

## 4.32.0 — 2026-07-25

### Fixed — `irag asof` returned the present as history
The date went straight into a lexical SQL comparison with no validation, so
anything sorting above an ISO date returned the *current* wiki state under a
historical banner:

```
2026-06-01     -> no pages had revisions on or before 2026-06-01   (correct)
2026-6-1       -> v2 (2026-07-25 10:10:00)   <- today, labelled June 1
June 1 2026    -> v2 (2026-07-25 10:10:00)
yesterday      -> v2 (2026-07-25 10:10:00)
src/store.ts   -> v2 (2026-07-25 10:10:00)   ('s' > '2')
```

`2026-6-1` was the dangerous one: unpadded but entirely plausible, and its
output indistinguishable from a real snapshot. For a command whose whole job
is auditing what memory said at a point in time, a confidently wrong answer
is worse than an error. Dates are now parsed and normalised (`2026-6-1` →
`2026-06-01`, `YYYY/MM/DD` and `YYYYMMDD` accepted, optional `HH:MM:SS`), and
anything unparseable exits with the expected format.

### Fixed — concurrent sessions claimed each other's changes
4.31.0 fixed *who owns* a session; this fixes *which work was theirs*.
`_window_facts` selected `event_id > start_event_id` with no upper bound and
no owner, so a session that changed nothing still reported another's files —
false history written into the diary `irag recap` feeds to the next session.

Events and revisions now carry a `session_key` (guarded additive columns),
stamped from a process-level active key: `irag update` is spawned by the Stop
hook, which supplies the conversation id, so everything one process writes
belongs to one session — no key threading through eleven `sync()` call sites.
A keyed session counts rows stamped with its key plus unstamped rows in its
window, so a manual `irag update` without an id is still credited rather than
lost. Keyless sessions keep the old behaviour.

### Fixed — `irag doctor` passed with the diary hook missing
The check grepped only for `irag context`, so `Claude Code hook wired` was
reported while `Stop` and `SessionEnd` were absent and the diary silently
never closed. All three hooks are now checked and the missing ones named. The
agent guide tells agents to trust this exact line, so it had to mean what it
says.

## 4.31.0 — 2026-07-25

### Fixed — two agents on one repo corrupted the project diary
`begin()` force-closed whatever session was open as `interrupted`, and
`end()` closed whichever session happened to be open — neither checked whose
it was, and the hooks call bare `session-begin` / `session-end` with no
identity. Running Claude Code and agy against the same repo produced:

```
A opens        -> session 1
B opens        -> session 1 marked 'interrupted' while A is still running
A session-end  -> closes session 2   (A's work narrated into B's entry)
B session-end  -> closes nothing     (B's work never recorded)
```

This is not theoretical on a machine running both tools, and `CLAUDE.md`
leans on the diary for continuity — it is what makes `irag recap` work.

Sessions now carry a `session_key` identifying the conversation that owns
them (guarded additive column). `begin` only force-closes a stale session
with the *same* key; `end` closes only its own. Claude Code hooks supply the
key automatically — its payload already arrives on stdin and now yields both
`session_id` and `transcript_path` through one shared read, since stdin can
only be consumed once. Other agents pass `--id <key>`. Keyless behaviour is
unchanged for the single-agent case.

### Fixed — `irag map` called every constant a function
`structure.py` fell through to `"function"` for the `const`/`let`/`var`
group, so numbers, objects and arrays were all rendered as functions
(`function CLUSTER_RADIUS` for the number `12.5`). Index-wide only two kinds
existed. There is now a `const` kind. This never reached the model —
`facts_block` emits names without kinds — so the damage was confined to
`irag map`, which `CLAUDE.md` tells agents to trust as parsed from the code.
(Introduced in 4.26.0, when the symbol regex was broadened.)

### Fixed — a locked database looked like "no matches"
`search()` caught every `sqlite3.OperationalError` and returned `[]`. The
intended catch is a malformed FTS query — users type quotes, parens and bare
operators — but it also swallowed lock timeouts and missing tables, so a real
failure was indistinguishable from an empty result set. Only syntax errors are
swallowed now; anything else propagates.

## 4.30.0 — 2026-07-25

Closes out the audit series: on a real Vite/React/zustand project the
contradiction count went 38 → 36 → 2 → 0.

### Fixed — the last two false positives were methods, not functions
`SYMBOL_RE` matches `name()` in prose and cannot tell a function from a
method. `_external_names` catches an imported binding (`createRoot`) but not
a method invoked on what it returns — `createRoot(...).render(...)`,
`gl.getExtension(...).loseContext()`. Nothing in a static index can confirm
or deny a method on a runtime object, so a `.name(` call site that isn't
locally defined is now treated as unverifiable rather than hallucinated, on
both the symbols-table and grep paths.

### Fixed — the definition grep read the whole directory
4.29.0 confirmed a symbols-table miss against source before flagging, but
read every file in the page's directory, so a symbol defined only in a
sibling was accepted for a page that never mentions it — `complete()` passed
for `Hud.tsx` while living in `Terminal.tsx`. A file page now greps only its
own file: siblings are already reachable through the symbols table via the
page's imports, and the grep exists solely to find declarations the
top-level index deliberately omits, which are in the same file. This closes
the precision limit 4.29.0 introduced rather than merely documenting it.

### Fixed — `irag check` passed with no memory at all
`never_synth` was computed and printed and gated nothing, and there was no
`fail_on_unsynthesized` default. A never-synthesized page has
`staleness_score` 0, so `fail_on_staleness` cannot see it — meaning a project
whose LLM was misconfigured printed "pages never synthesized: 40" and exited
**0**. For a gate whose stated job is to fail the build when memory disagrees
with the code, no memory at all now fails. Opt out with
`fail_on_unsynthesized = false`.

### Fixed — search excerpts rewrote real text
The FTS5 snippet was told to wrap matches in `[` `]`, which injected brackets
inside identifiers and paths: `src/store.ts` came back as `src/[store].ts`.
This output is read by agents, so the excerpt is now verbatim.

### Fixed — destructured exports were not indexed
`export const { a, b } = obj` and `export const [x, y] = arr` bind several
names in one statement and were skipped entirely. Both are now indexed,
keeping the local alias in `{ a: b }` and dropping defaults after `=`.

## 4.29.0 — 2026-07-25

### Fixed — a contradiction silently deleted the page from the agent's memory
`P_CONTRADICTED` is 40 and the default `min_score` is 20, so flagging a page
pushed it under the eligibility threshold: it fell out of the FULL and DIGEST
tiers into a bare one-line INDEX entry. The warning loop was gated on that
*same* threshold, so the page most in need of "verify this before trusting
it" was exactly the one whose warning got suppressed. A false positive
therefore didn't just add noise — it removed the agent's memory of that file
and sent it back to exploring from scratch, which is the whole cost irag
exists to remove.

Relevance and health are now separate: relevance decides eligibility, the
penalty still orders results, so a flag **demotes** a page instead of erasing
it. The warning section is no longer gated (capped at 12 plus an overflow
line, so it stays bounded). Measured over 24 pages, all flagged:

| | full | digest | warnings |
|---|---|---|---|
| before | 2 | 0 | 2 |
| after | 4 | 20 | 13 |

This also explains why the previously-noted "warnings block is uncapped"
concern never reproduced in synthetic tests — the penalty had already pushed
every flagged page below the gate.

### Fixed — two regressions introduced by 4.27.0
- **Anchoring the symbol regex at column 0 fixed `irag map` but broke the
  linter.** The index deliberately holds top-level declarations only, and the
  linter was treating absence from it as proof of non-existence — so every
  nested helper and every object-literal member (zustand actions, class
  methods) became a phantom-symbol flag. A table miss is now confirmed
  against the module's source before anything is called hallucinated.
- **`PATH_RE` was widened to check `css`/`html`/`svg` without widening what
  the basename index could see.** It was built from `_iter_code_files`, whose
  `CODE_EXT` contains none of those, so a real `favicon.svg` could never be
  resolved and was reported missing. The index now covers every tracked file.

### Fixed — three more
- The package-import guard applied only on the symbols-table path, so any
  file falling back to the source grep still had its imported names flagged
  (`createRoot()`, `render()`).
- `./`-relative and `../`-relative path claims were never resolved; they now
  resolve against the page's own directory, staying inside the repo.
- `export function* gen()` and `export abstract class` were not indexed.

### Changed
`irag dashboard` no longer re-runs the schema DDL and both migration helpers
on every request. `ThreadingHTTPServer` is thread-per-request, so the
thread-local connection cache always missed; the schema is now ensured once
at server start and requests only connect. (The 4.27.0 note that the leak fix
removed the repeated DDL was wrong — it removed the leak, not the DDL.)

## 4.28.0 — 2026-07-25

### Fixed — the token estimator was the least accurate option available
`_PIECE` matches `\w+`, and `\w` includes `_` — but the branch test was
`piece.isalnum()`, and `"my_var".isalnum()` is `False`. Every snake_case
identifier, the most common shape in source, fell through to the punctuation
branch and counted as **one token** regardless of length
(`some_very_long_identifier_name`: real 6, estimated 1).

Measured against `cl100k_base` over ~135k tokens of real Python, JS, HTML,
markdown and CSS, worst-case error across the five corpora:

| estimator | worst-case error |
|---|---|
| previous `_estimate` | 53% |
| plain `chars // 4` | 19% |
| **this release** | **9%** |

So the old docstring had it backwards: it justified the heuristic by claiming
`chars // 4` undercounts code, when `chars // 4` was in fact the more
accurate of the two. Fixing the identifier bug alone made things *worse*
(53% → 37% worst-case is still bad, and Python went +28% → +37%), because the
sub-word rule was already overcounting and the bug had been masking it. Both
constants are now calibrated: word runs cost one token per 6 characters, and
punctuation 0.6 each rather than 1, since BPE merges runs like `);` and `=>`.

This module is the only source of token accounting for `irag status`'s "est.
LLM tokens spent" and the dashboard burn chart. Anyone without the optional
`tiktoken` installed — the zero-dependency default — was seeing figures
inflated by a quarter to a half.

### Fixed — ignore patterns were case-sensitive on case-insensitive filesystems
macOS and Windows filesystems fold case, so a checked-in `Node_Modules/` was
scanned rather than ignored: one LLM call per dependency file. Segment
matching now folds case. `src/Node.tsx` is still tracked — only whole path
segments are compared, never substrings.

## 4.27.0 — 2026-07-25

### Fixed — the dashboard leaked a SQLite connection per request
`_State.conn()` called `ensure_db()` on every request: a brand-new connection,
never closed, plus a full re-run of the schema DDL and both migration helpers
each time. Handles on `memory.db` grew per request, and the accumulating live
readers kept WAL checkpointing from completing, so the WAL grew unbounded
until writers began timing out on the lock. Measured over 48 requests:
handles went **22 → 64** before, **8 → 8** after. The connection is now
thread-local and released in a `finally` around `handle_one_request` —
thread-local because `ThreadingHTTPServer` is thread-per-request and
`db.connect()` leaves `check_same_thread=True`; released per request because
that same model means caching alone would still allocate one per request.

A lock timeout out of `irag update` also escaped as a raw traceback, which
reads like corruption. It now names the cause and the remedy.

### Fixed — seven more fact-checker and indexing defects
- **A call site read as a definition.** The grep fallback's `[^;=]*` swallowed
  parentheses, so `if (subtract(a, b) > 0) {` matched as a definition of
  `subtract` — a genuinely hallucinated symbol passed as verified. This was a
  false *negative* in the one check whose whole job is catching lies.
- **`have_symbols` was database-global** (`SELECT 1 FROM symbols LIMIT 1`), so
  one indexed Python file forced every page through the strict symbol check —
  including files in languages the scanner never parses (`.kt`, `.swift`,
  `.vue`, `.dart`), whose real symbols were then reported as hallucinated.
  Now scoped to the page's own subject.
- **Indented locals were indexed as module symbols.** `^\s*` under `re.M`
  matched any indentation, so `const t = (a + b)` inside a function body
  became a module-level symbol. Anchored at column 0.
- **The path allowlist exempted whole file types.** `css`, `html`, `svg`,
  `scss`, `vue`, `kt`, `swift`, `dart`, `php`, `c/h/cpp` and more were never
  checked at all, so a wrong path claim about them was invisible.
- **Scoped npm packages were never version-checked.** `@react-three/fiber@9.6.1`
  captured only `fiber`, which never matched the manifest key.
- **Unpinnable specs were compared as literal versions.** `4.x`,
  `workspace:*` and `git+https://…#v3.2.47` are satisfied by many versions;
  claims disagreeing with the literal string were reported as mismatches.
  Such specs are now skipped as unverifiable rather than asserted against.
- **requirements.txt lost both ways:** PEP 508 markers and trailing comments
  leaked into the stored version (false positives), and `[extras]` stayed in
  the key so `uvicorn[standard]==0.30.0` meant a claim about `uvicorn` was
  never checked at all (false negative).

`tests/test_smoke.sh` gains a second precision block covering all of these,
and the existing negative test still proves a missing relative path, a
missing absolute path, a missing bare filename, a non-dependency `.js` and a
phantom symbol are all still caught.

## 4.26.0 — 2026-07-25

### Fixed — five defects found running irag on a real Vite/React/TS project

**`structure.py` — the symbol regex only matched a 2015 subset of JS.** The
`const` branch required `(`, `function` or `=>` straight after `=`, so every
`const X = factory(...)` was invisible: zustand stores, `createContext`,
`styled`, `forwardRef`. A TS type annotation between the name and `=` also
broke it, and `interface` / `type` / `enum` were never matched at all. One
real `src/store.ts` indexed **zero** symbols. It now accepts any initializer,
an optional type annotation, and TS type declarations. This was the deepest
of the five — it degraded `map`, `impact`, retrieval and context quality, not
just the linter.

**`linter.py` — four checks flagged things that were never wrong:**
- Absolute paths inverted the check. `repo / "/src/main.tsx"` discards `repo`
  (pathlib) and tested the filesystem root, so every Vite-style URL in a page
  was reported missing.
- A bare filename was denied rather than located: `store.ts` living at
  `src/store.ts` was reported as "does not exist in the repository".
- Package names satisfy the path regex. `three.js` — and `Next.js`, `Vue.js`,
  `Node.js` — were flagged as missing files. Tokens with no separator whose
  stem is a declared dependency are now skipped.
- Symbols imported from packages were treated as phantom. `useFrame`,
  `Suspense`, `createRoot` live in `node_modules`, which is never indexed, so
  they could not possibly be found. Absence of evidence was being recorded as
  evidence of absence; bindings from non-relative specifiers are now skipped
  as unverifiable.

Each fix adds a skip path to a fact-checker, so the suite now also proves the
checker still catches genuine lies: a missing relative path, a missing
absolute path, a missing bare filename, a non-dependency `.js`, and a phantom
symbol are all still caught.

## 4.25.0 — 2026-07-25

### Fixed — the agent guide let an agent opt out of keeping the memory alive
A real session ended with the agent reporting *"I did not run `irag update`;
it costs roughly one LLM call per file across ~21 new files, so that's your
call"* — in a project where no Stop hook had ever been installed. Both halves
were faithful readings of the guide, so the guide was the bug:

- Phase 0 treated "`irag status` prints a dashboard" as proof the loop was
  running. Initialized and automated are different things. It now requires
  reading the Claude Code hook line out of `irag doctor` first — an
  initialized project with no hooks is the most common broken setup and is
  otherwise invisible.
- Phase 2 stated as fact that "a Stop hook runs `irag update` when you finish
  a turn". Now explicitly conditional on having verified it.
- Rule 2 said to update only "if you are not sure the Stop hook ran (or
  you're not Claude Code)" — which an agent under Claude Code resolves to
  "covered, skip it". Now unconditional, and it names the observed failure
  sentence verbatim so a model can match its own output against it.
- Phase 1's "the first `irag update` costs one LLM call per file" was being
  generalized to every update. Scoped to the initial build, with an explicit
  ban on quoting per-file cost as a reason to skip a routine update.
- `irag claude-setup` must now be verified by a follow-up `irag doctor`.

The guide ships as both `CLAUDE.md` and `AGENTS.md`; `irag export` reinstalls
it into an existing project.

### Known issue
A missing Claude Code hook is only a `WARN`, so `irag doctor` still exits 0
and still prints "all checks passed" underneath it. The guide now says to
read the hook line rather than trust the summary. Promoting it to `FAIL` is
the better fix and is not done here.

### Changed
- Showcase headline now carries the token claim directly — **"So your agent
  can look it up — for almost no tokens."** — rather than leaving it to the
  subcaption, superseding the wording recorded under 4.24.0 below.
- The scene's flying label is placed against the copy's block boxes and will
  drop itself rather than print over a card or paragraph; the hero lede is
  cut to two sentences and the closing CTA carries the install command.

## 4.24.0 — 2026-07-25

### Changed — the showcase lands on the actual selling point
The final beat was *"So you can ask what breaks"*, which sells blast radius.
That is a feature, not the reason anyone installs this. It now reads **"So
your agent looks it up."** — structure, neighbours and blast radius read
straight from the graph, instantly and for zero tokens. The no-JS fallback
caption carries the same message.

### Fixed
- The showcase timeline could sit parked at t=0 with the section blank: it
  was started by an IntersectionObserver that stopped delivering (a
  hand-made identical observer still fired). It now drives itself from the
  render loop and idles by a rect check when far off-screen — the same
  reason the scroll reveals were moved off IntersectionObserver earlier.

## 4.23.0 — 2026-07-25

### Changed — the dashboard now matches the site's *scale*, not just its tokens
Every colour, font and radius already matched, which is why token
comparisons kept saying "identical" while the two still looked different.
Rendered side by side, the dashboard was running at about 81% of the site:

| | site | dashboard (before) |
|---|---|---|
| heading | 38px | 25.6px |
| body | 16px / 26.9 | 14px / 21.7 |
| sidebar | 250px | 220px |

The whole type scale, the base font, the nav, the content width and the
sidebar are now the site's.

### Changed — a much larger visualisation
The 3D stage goes from `min(70vh, 660px)` to `min(84vh, 900px)`.

### Fixed — the 3D view never actually zoomed
`d = dist / (dist + z)` equals 1 whenever `z == 0`, so changing the camera
distance did nothing to the scale: the graph either overflowed the stage or
collapsed to a dot, and enlarging the stage made it worse. It is a real
perspective divide now, and the auto-fit **measures** where the outermost
node lands and corrects, instead of guessing with a closed-form formula.
The graph fills ~83% of the stage and is verified unclipped.

## 4.22.0 — 2026-07-25

### Fixed — the docs sidebar was being painted over, and the page was slow
Two real defects, both mine, both introduced with the docs backdrop:

- **A full-viewport scrim was covering the sidebar.** It hung off
  `.doc-shell::before`, and `.doc-shell` comes after `.side` in the DOM and
  creates its own stacking context (it has an opacity animation), so its
  fixed pseudo-element painted straight over the left column. The links
  computed to the right colour and rendered dark — which is why raising
  their contrast last release didn't help. Moved to `.wrap::before`, where
  it sits behind both columns.
- **`backdrop-filter` on the sidebar and the TOC was the "slow" feeling.**
  Blurring a sticky `100vh` backdrop re-composites every frame. Both are
  gone; the panel is opaque, which needs no blur. Scrolling now measures
  avg 16.7ms/frame with 0 frames over 32ms.

### Changed
- Transitions shortened: the launch warp 1150ms → 780ms, the docs fade
  420ms → 220ms.
- Sidebar colours raised unconditionally rather than only when the 3D scene
  is running, so the nav is legible in every case.

## 4.21.0 — 2026-07-25

### Fixed — the docs sidebar was sitting on nothing
It had **no background at all**, so the node field showed straight through
the links. It is a panel now (same treatment as the dashboard's sidebar),
and its links were lifted off `--text-dim` because a panel on a busier
surface needs more contrast: **11.8:1** for links, **7.7:1** for the active
one, both well past WCAG AA.

### Changed
- **Docs hold completely still.** The backdrop settles and then the render
  loop stops outright — no drift, no battery burn. Verified: identical
  painted-pixel counts two seconds apart. The only motion on a docs page is
  the fade.
- **The node merge is bigger.** Nodes now swell as they gather, so what
  they collapse into reads as one large body rather than a speck, and the
  bloom that follows starts larger and opens wider.

## 4.20.0 — 2026-07-25

### Changed — the launch transition zooms into the node, and docs just fade
- The transition ended on a white radial flash. Now the compressed node
  **opens up and swallows the screen**: the scattered nodes gather to a
  single point, that point is pulled to the centre of the frame, and then
  it expands past the viewport, so you fly *into* it and come out in the
  docs. The veil is a plain dark cover over the final 10%, purely to hide
  the navigation swap.
- **Docs arrive on a plain fade.** They were sliding, scaling and
  un-blurring, which is movement on a page you are trying to read.
- The docs node field is quieter (opacity .5 → .28): a texture behind the
  prose, never a competitor to it.

## 4.19.0 — 2026-07-25

### Added — the launch transition
Clicking **Get started** or **Read the architecture** no longer just loads a
page. Every node in the graph **collapses into a single point**, the camera
is thrown through it, and the docs arrive out of the white-out. The nodes
are the site's entire visual language, so navigation is built from them
rather than a generic fade.

### Changed — it flies now, instead of panning
The camera eased laterally between stops, which read as scrolling past a
backdrop. It now **lifts away from its target in transit and dives back in
on arrival** — measured: 35,295 painted pixels at a stop, 6,211 mid-flight,
46,606 at the next.

### Added — docs share the world
Doc pages carry the same node field on a slow ambient orbit (no rail, no
dive, dimmed behind a scrim so type stays the focus) and arrive on the
animation the landing page hands them.

### Added — fullscreen Visualize
The 3D codebase view has a fullscreen control; the canvas re-measures and
re-fits on the way in and out.

### Changed — dashboard and site are one theme
All 23 shared tokens already matched after 4.18.0; the dashboard now also
carries the site's ambient glow and node constellation. **Static on
purpose** — a task surface with live numbers should not have motion behind
it. Verified the text and metric colours are unchanged.

## 4.18.0 — 2026-07-25

### Added — **Visualize: your codebase in 3D, inspectable and live**
A new dashboard tab renders **your actual codebase** in three dimensions —
nodes are your files (sized by symbol count, coloured by top-level folder),
edges are your real imports, both read straight from the `symbols` and
`deps` tables. It is your repo, not a diagram of one.

- **Inspectable.** Click any file to open what irag knows about it: what it
  defines, what it imports, what imports it, its open contradictions, and
  its written summary. The import chips navigate, so you can walk the
  dependency graph node to node.
- **Live.** It re-reads the map every few seconds and rebuilds only when
  the structure actually changed. `GET /api/map?live=1` runs a sync + scan
  first (both zero-token and fingerprint-gated, throttled to once per 3s),
  so **a file you create appears on its own** — verified end to end with no
  manual scan.
- 3D force layout, perspective projection, z-sorted painting, auto-fit to
  the graph's extent; drag to orbit, scroll to zoom, hover to isolate a
  node and its neighbours. Hand-rolled, no 3D dependency.

### Changed — the dashboard and the site are now one theme
All 23 shared design tokens are identical. Two were not: `--text-faint`
(the dashboard's `#707885` failed WCAG AA at 4.47:1 — now `#828a99`, 5.73:1)
and the `--r-md` / `--r-lg` corner radii.

### Fixed
- `/api/map?live=1` swallowed every failure with a bare `except: pass`,
  which hid a real `database is locked` contention error and made the live
  update look silently broken. Contention now retries on the next poll and
  anything else is logged.

## 4.17.0 — 2026-07-25

### Changed — the node's name is readable now
It was 11px and dim, competing with 16px body copy and losing. It is now
**21px semibold with a dark halo** and its original casing, so it holds its
own as a label in the scene rather than reading as a footnote.

Placement had to follow:
- The label **flips to the other side of the node** when a long name
  ("Architecture") would otherwise run off the viewport, measured with
  `measureText` rather than guessed.
- Sections whose content is a **full-width grid** have no side margin to
  put a node in, so the focal node now also takes a **vertical** screen
  offset and lifts into the empty band beside the heading. "Architecture"
  was landing directly on the "Pluggable memory" copy.

## 4.16.0 — 2026-07-25

### Changed — the rail is made of the nodes now, and it flies
The section rail was a fixed strip of UI dots down the left edge. The
markers *are* the graph's nodes now: they dock together while you're
elsewhere and the one you arrive at **flies out into the scene**, where it
gets a contracting focus ring and its name.

- The docked cluster's anchor moves per section — right, top-right, far
  left, bottom-right — so the rail travels around the page instead of
  sitting in one strip. The canvas is behind the copy, so a marker can
  never cover text.
- Docked markers are dots and a connector only; labelling all six was
  clutter, and the node you've arrived at is already named out in the
  scene.
- The focal node is pushed into the page margin per section and its label
  is drawn outward, away from the centre column — it was landing on top of
  the body copy.
- The `<nav>` remains for keyboard and screen-reader users: real buttons,
  clipped to 1×1 until focused, then revealed with a visible focus ring.

## 4.15.0 — 2026-07-25

### Changed — the 3D scene and the page are now one system, not two
The camera drifted past a background graph while the copy scrolled
independently. They are now choreographed: **the camera arrives at a node,
settles, and the section resolves.**

- Each section owns a node. The camera is driven by *which section is
  centred* rather than by a global scroll fraction, so it comes to rest on
  a node while you read and travels only while you move between sections.
- **The copy is keyed to arrival**: a section sits at 20% opacity while the
  camera is in transit and resolves to full as it lands (measured: 0.22
  mid-travel → 1.0 on arrival).
- **The node you arrive at is named**, with a focus ring that contracts as
  the camera settles.
- The decorative scroll spine is now a **node rail**: one dot per stop,
  labelled, highlighting the current node, filling 0→100% as you descend,
  and clickable to fly to any section.

### Fixed
- The scene threw `ReferenceError: camT is not defined` and did not render
  at all — a leftover from replacing the waypoint block. Found only after
  discovering the browser check reporting "0 JS errors" was reading a
  variable nothing ever set; error capture is now a real `window.onerror`
  hook installed before page scripts run.
- Arrival opacity was applied to section *children*, where `.js .wipe.in`
  beat it on specificity, so nothing ever dimmed. Applied to the section.
- Removed the dead scroll-spine CSS the rail replaced.

## 4.14.0 — 2026-07-25

### Added — the landing page is a 3D space you fly through
The graph was a small panel beside the wordmark. It is now the **backdrop
of the entire page**, and scrolling flies a real camera through it.

- **A real camera**, not model rotation: position + look-at, world → view
  basis → perspective divide, painted back-to-front so near nodes occlude
  far ones. Six waypoints are interpolated on smoothstep against page
  scroll, so each section arrives somewhere specific — establishing shot,
  into `api`, close on `auth`, down into `db`, over to `tests`, then pull
  back wide. Each waypoint also carries a screen offset, so the
  establishing shot sits clear of the left-aligned hero copy.
- The world is four clusters (api / auth / db / tests) wired hub-to-hub
  along a dependency spine, generated from a fixed seed so the scene is
  identical on every load.
- The cursor parallaxes the camera around its target; a slow constant
  orbit keeps it alive when the page is still.
- Still zero dependencies: ~6KB of hand-rolled projection. **60fps
  measured** (avg 16.7ms/frame, 0 frames over 32ms), and it stops
  rendering entirely when the tab is hidden.

Degradation is real, not nominal: the scene never starts under
`prefers-reduced-motion`, below 760px, or without JS, and the original
static hero SVG is what shows in every one of those cases.

## 4.13.0 — 2026-07-25

### Fixed — the site footer had been stuck on v4.2.0 for ten releases
It was hardcoded. `site/build.py` now reads `__version__` out of
`irag/__init__.py`, so it can never drift again.

### Added — real 3D in the hero, and spatial depth on the page
- **The hero graph is now genuinely three-dimensional**: nodes live in a
  unit cube, rotate on a slow orbit, and are painted back-to-front with
  size, opacity and glow driven by perspective depth. It is a real
  projection with z-sorting, not a CSS fake — hand-rolled in ~4KB, because
  a 3D library would be roughly twenty times the site's entire JS budget
  on a page whose pitch is "zero dependencies". anime.js drives the
  arrival (the graph unfolds out of the origin as the camera dollies back)
  and the cursor orbits the camera on both axes, so the hero reads as a
  space you are looking into.
- **Spatial UI on the screenshots.** `.tilt` existed in the markup with no
  implementation at all; figures now sit in a perspective container and
  rotate toward the cursor with a Z lift, easing back via anime.js on
  leave. Fine-pointer devices only.
- Both degrade properly: `prefers-reduced-motion` keeps the original
  static SVG and disables the tilt entirely, and the SVG is also the no-JS
  default, so the hero is never an empty box.

## 4.12.0 — 2026-07-25

### Changed — the landing page's scroll section now shows what it claims
The headline read "Files become a graph" over a field of 288 abstract dots
that rippled, pulsed, and then lit ten of themselves at random. The claim
was asserted, never shown, and the wave sweep carried no meaning at all —
which is exactly why it read as arbitrary motion.

It now animates **real file paths**: seven of them start stacked as a plain
listing, fly out into graph positions, and wire themselves together along
their **actual imports**. The last beat highlights one file and the four
modules that depend on it, so the section ends on blast radius — the thing
the product is actually for. Three captions carry the argument: *Your
files.* → *Become a graph.* → *So you can ask what breaks.*

The scroll-scrub itself (a rAF loop lerping the timeline playhead) is
unchanged; only what it animates changed.

### Fixed
- Positioning and animation were fighting: `.sc-node` centred itself with
  `transform:translate(-50%,-50%)`, which anime.js overwrites wholesale
  when it animates `translateX/Y`. Split into a positioning wrapper and an
  animated chip.
- Reduced motion landed on the finished graph but skipped the blast-radius
  highlight, which is the point of the last beat.
- A full path is wider than the stage on a phone: narrow viewports now show
  file names only, with the layout pulled in from the edges.
- Removed the dot-grid CSS left behind by the rewrite.

## 4.11.0 — 2026-07-25

### Changed — README rewritten, screenshots regenerated
- The screenshots were from 4.2.0 (July 20) and showed a dashboard that no
  longer exists: no Pages tab, no Tools, no command palette. All five are
  regenerated at 2× against a realistic demo project, and two new views are
  documented (the unified page object, and the palette searching page text).
- README rebuilt around the reader's problem rather than a feature list: a
  one-line hook, the flagship screenshot immediately, a without/with cost
  table, then why it's different, quickstart, dashboard, measurements.
  Every claim in it is a figure measured elsewhere in this changelog.

### Fixed
- The first-run panel's lede was static while its steps were live, so it
  could say "no summaries have been written yet" directly above a step
  reading "memory built ✓". It now tracks the same state as the steps.

## 4.10.0 — 2026-07-25

### Fixed — **`from . import x` created no dependency edge** (Python graph was badly incomplete)
`from . import db` — the dominant intra-package import style in Python —
resolved to the *package directory* only, silently discarding the imported
module. The names in a `from X import a, b` were never considered as
submodules, so most intra-package edges simply did not exist.

Measured on irag's own source, the fix takes the graph from **20 to 70
import edges (3.5×)**, and `irag impact irag/db.py` from a confidently
wrong *"nothing imports this — change is contained"* to the correct **13
dependent modules**. Any Python project using relative imports had an
under-reported blast radius. Covered by a new smoke assertion.

### Changed
- README leads with the actual problem (you pay to re-read your codebase
  every session) and reports **measured** numbers instead of estimates,
  with a "Testing & measurements" section: what one command exercises, and
  results from running irag on its own source.
- The landing page gains a "Measured, not claimed" section with the same
  figures, and its hero and stat strip now match them.

## 4.9.0 — 2026-07-25

### Changed — **the dashboard is organised around your work, not irag's internals**
Three passes of polish hadn't fixed the structural problem: the UI was
grouped by irag's own features, so understanding a single file meant
visiting Pages (summary), Health (is it wrong), Map (what it imports) and
Sessions (what changed it). And Overview led with nine counters that
answer "how many rows are in the database", not "what should I do".

- **A command palette (`⌘K` / `Ctrl-K`, or the top-bar search box).** In a
  knowledge base, search *is* navigation; it was previously buried inside
  the Chat tab. The palette jumps to any page, view, or action from
  anywhere, matching page names locally and page *text* through the same
  FTS index the CLI uses (instant, zero tokens).
- **A page is now one object.** `/api/page` returns the summary, its
  version history, the open contradictions *on that page*, and its parsed
  structure in one response. The page view shows problems inline (with
  resolve), and what it defines / imports / is imported by — with the
  imports clickable, so the dependency graph is walkable from the page.
- **Overview leads with what needs attention** (claims contradicting the
  code, changes not yet summarised, failed syntheses), each with the
  action that fixes it. Silent when the memory is healthy; the metrics
  remain below.
- New `GET /api/search`.

### Fixed
- The palette showed duplicates: FTS returns one row per matching
  *revision*, so a page with several versions appeared several times.
- Page headings uppercased the subject — but a subject is a file path, and
  paths are case-sensitive (`SRC/AUTH/LOGIN.PY` is a different, wrong
  path). The generic `.panel h3` label style was leaking into it.
- The palette's local name matching only worked after the Pages tab had
  been opened; the page list now loads at startup.

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
