# CLAUDE.md — You manage this project's memory with irag

You (Claude, or any coding agent reading this) are responsible for
operating **irag**, the knowledge base for whatever project you work in.
irag gives you persistent, verified memory: every file and folder has an
AI-written summary page, versioned on every change, mechanically
fact-checked against the code, and searchable. Your job is to keep that
memory alive and use it instead of exploring from scratch. This document
is everything you need. Follow it without being asked.

## Phase 0 — Is irag set up here?

Run `irag status`, then `irag doctor`. Three outcomes:

1. **It prints a dashboard** → irag is initialized. That is NOT the same
   as automated. Before you go anywhere, find the Claude Code hook line
   in `irag doctor`'s output:
   - `✓ PASS  Claude Code hook wired` → good, skip to Phase 2.
   - `! WARN  Claude Code hook   not set up` → **run `irag claude-setup`
     right now, then re-run `irag doctor` and confirm the line flipped to
     PASS.** Until it does, nothing is automatic: no context is injected
     at session start, no session is logged, and `irag update` never runs
     on its own. An initialized project with no hooks is the single most
     common broken setup, and it is invisible unless you look.

   Read that specific line. A missing hook is only a WARN, so `irag
   doctor` still exits 0 and still prints **"all checks passed"** at the
   bottom. Neither the exit code nor the summary line will tell you the
   memory loop is dead.
2. **"not initialized here — run 'irag init'"** → do Phase 1.
3. **"command not found"** → irag isn't installed. Install it from the
   source folder: `pip install -e <path-to-irag>` (Python 3.11+, zero
   dependencies), then do Phase 1.

## Phase 1 — Bootstrap (once per project)

```
irag init
```

Works with or without git (snapshot mode fingerprints files when there is
no repo). Then configure the summarizer LLM in `.irag/config.toml`:

- Claude Code available → the default `command = "claude -p"` already
  works; change nothing.
- Antigravity → `command = "agy --dangerously-skip-permissions -p {prompt}"`
  (if output comes back empty, use the pseudo-TTY wrapper in
  docs/SETUP.md).
- Any other CLI → it must accept a prompt and print markdown; use
  `{prompt}` (argv) or `{promptfile}` (temp file) placeholders, or
  nothing for stdin.

Verify, then build the initial memory and wire the hooks:

```
irag doctor --probe-llm     # must pass before spending real tokens
irag update                 # first full synthesis: every file + folder
irag claude-setup           # SessionStart (memory in) + Stop (memory out) + SessionEnd (diary)
irag doctor                 # REQUIRED: confirm "Claude Code hook wired"
```

`claude-setup` is not optional and its result must be verified. If that
last `doctor` does not say the hook is wired, the memory loop is dead and
every later phase of this document silently does nothing. Fix it before
continuing, and tell the user.

If the user is present, tell them **this first** `irag update` costs one
LLM call per file and folder, and let them confirm on large repos.

That confirmation applies to the initial build and nothing else. Routine
updates after a few edits are ordinary running cost, not a decision to
escalate — see Phase 2 rule 2. Never quote the per-file cost as a reason
to skip a normal update.

## Phase 2 — The loop (every session, mostly automatic)

After claude-setup — **and only if `irag doctor` confirms the hook is
wired; check, never assume** — the loop runs itself: SessionStart opens a
conversation-log entry and injects ranked context (including a recap of
the previous sessions — read it, that is your continuity), a Stop hook
runs `irag update` when you finish a turn, and SessionEnd summarizes the
whole conversation into the project diary with everything it changed. Your obligations on top of that:

1. **Read before exploring.** NEVER grep, glob, or walk the codebase to
   learn what the project is — that burns your own tokens re-doing work
   the memory has already paid for. If a question can be answered by
   irag, it must be, before you spend a single token exploring the tree
   yourself. The knowledge base is your second brain; query it:
   - `irag recap` — "previously on this project": what the last
     conversations did (also auto-injected at session start)
   - `irag context --open <file> --query "<task>"` — ranked, budgeted
     context for the task at hand
   - `irag search <keywords>` — SQL search: instant FTS lookup, zero
     tokens (use for file names, symbols, "where is X")
   - `irag ask "<question>"` — AI search: reasoned answer with page
     citations (use for "how/why does X work")
   - `irag map [path]` / `irag impact <path>` — code structure and blast
     radius, parsed from the code, always current
   - `irag why "<claim>"` — trace any memory claim to the change that
     created it
2. **Update after editing — unconditionally.** If you created, modified
   or deleted files, run `irag update` before you finish your turn. Do
   not reason about whether the Stop hook will cover it; run it anyway.
   It is a no-op when nothing has changed, so a redundant run is free,
   and a skipped run leaves the memory silently wrong for every later
   session.

   **Do not ask the user for permission to run it, and do not decline it
   on cost grounds.** Synthesizing changed files is what this tool is
   for; that cost is the product working, not an incident to escalate.
   If you want the user to know the size first, run `irag status`, state
   the number of pages due, then run the update and report what it did.
   "I did not run irag update, it costs N calls, your call" is a failure
   to do your job — the memory is now stale and the user has to notice
   and fix it by hand.
3. **Keep the diary if hooks can't.** If you are Claude Code with
   claude-setup done, sessions log themselves — skip this. Any other
   agent (agy, Cursor, ...) must do it manually: run `irag session-begin`
   as your first action in a conversation, and `irag session-end` as your
   last action before the conversation closes (also when the user says
   they're done). **If anything else might be working this repo at the same
   time, pass a `--id`** — `irag session-begin --id <something-unique>` and
   `irag session-end --id <same>`. Without it, two conversations running at
   once can only be told apart when exactly one is open, so concurrent work
   ends up credited to nobody. Claude Code supplies this automatically.

   The diary is what makes `irag recap` work for the next session — do not
   skip it. Every session is logged redundantly with
   the exact per-file changes it made (not just which files, what
   changed in each) — `irag sessions --json` and the dashboard's
   Sessions tab both show this; use them to audit what a past
   conversation actually did before assuming.
4. **Record what you learn**, so the next session doesn't rediscover it:
   - `irag record-decision "<choice and why>" --module <path>` for
     architectural/tooling decisions
   - `irag learn "<gotcha, pitfall, constraint>" --module <path>` for
     anything that surprised you

## Phase 3 — When memory and code disagree

THE CODE IS THE TRUTH, always. If a page carries a ⚠ warning or `irag
check` fails:

1. `irag contradictions` — read the exact claims (claim vs truth, with
   severity).
2. If the code is wrong → fix the code, then `irag update` (the
   contradiction auto-resolves when the claim stops failing).
3. If the page is wrong → `irag update` refreshes it from current code.
4. If the flag is spurious → `irag resolve <id> --notes "<why>"`.

Never quote a ⚠-flagged page to the user as fact without verifying
against the code first.

## Troubleshooting (self-service)

- `irag doctor` — full diagnosis with PASS/WARN/FAIL and fixes; run this
  first whenever anything misbehaves.
- "nothing pending" from synthesize — the message explains why (no
  changes, empty queue, or below threshold).
- Failed/interrupted syntheses self-heal: the next `irag update`
  requeues and retries them automatically.
- `irag status --json` — machine-readable health for your own checks.
- Every command supports `--help`.

## Boundaries

- CLAUDE.md and AGENTS.md are irag's agent guide (this document),
  installed into the project by `irag init` and re-installable with
  `irag export`. Do not hand-edit them. `irag update` does NOT touch
  them — the memory lives in `.irag/` and you read it with the commands
  above (`context`, `recap`, `search`, `map`), never from a file dump.
- Never run `irag rollback`, `irag pin/unpin`, or edit
  `.irag/config.toml` on your own initiative — those are the human's
  calls; suggest them when relevant.
- Do not delete `.irag/` — it is the entire memory.
- `irag dashboard` and `irag obsidian` are human viewports; mention them
  to the user, don't run them yourself.

## Command index

Read: `context` · `recap` · `sessions` · `transcript` · `search` · `ask` ·
`map` · `impact` · `why` · `asof` · `diff` · `status` · `stale` ·
`contradictions`
Write: `update` · `learn` · `record-decision` · `resolve`
Setup (Phase 1 only): `init` · `doctor` · `claude-setup`
Human-only: `rollback` · `pin` · `backup` · `dashboard` · `obsidian` ·
`export`

Full reference: docs/CLI_REFERENCE.md · Setup details: docs/SETUP.md ·
How it works: docs/ARCHITECTURE.md
