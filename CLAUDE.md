# irag agent operating guide

You, whatever coding agent you are, are responsible for operating **irag**,
the knowledge base for this project. When MCP tools named `irag_*` are
available, use them; otherwise use the equivalent `irag` CLI commands below.
irag gives you persistent, verified memory: every file and folder has an
AI-written summary page, versioned on every change, mechanically
fact-checked against the code, and searchable. Your job is to keep that
memory alive and use it instead of exploring from scratch. This document
is everything you need. Follow it without being asked.

## Phase 0 — Is irag set up here?

Call `irag_status` when MCP is connected; otherwise run `irag status`, then
`irag doctor`. Three outcomes:

1. **It returns project health** → irag is initialized. If MCP tools are
   visible, the integration is live and identical across clients. If you are
   using Claude Code without MCP, verify `Claude Code hook wired` in
   `irag doctor`; run `irag claude-setup` if it is missing. Other non-MCP
   clients use the explicit CLI session loop in Phase 2.
2. **"not initialized here — run 'irag init'"** → do Phase 1.
3. **"command not found"** → irag isn't installed. Install it from the
   source folder: `pip install -e <path-to-irag>` (Python 3.11+, zero
   dependencies), then do Phase 1.

## Phase 1 — Bootstrap (once per project)

```
irag init
```

Works with or without git. Then configure the summary writer in
`.irag/config.toml` with `provider = "claude"`, `"codex"`, `"agy"`,
`"ollama"`, or `"custom"`. Use `irag provider` to verify its executable.

Verify and build the initial memory:

```
irag doctor --probe-llm     # must pass before spending real tokens
irag update                 # first full synthesis: every file + folder
```

For MCP, register `irag mcp --root /absolute/project/path` once in the coding
client (see `docs/MCP.md`). For hook-driven Claude Code, run `irag
claude-setup`. Every other client can use the same MCP path or the explicit CLI
session commands; the memory database and behavior are identical.

If the user is present, tell them **this first** `irag update` costs one
LLM call per file and folder, and let them confirm on large repos.

That confirmation applies to the initial build and nothing else. Routine
updates after a few edits are ordinary running cost, not a decision to
escalate — see Phase 2 rule 2. Never quote the per-file cost as a reason
to skip a normal update.

## Phase 2 — The loop (every session)

With MCP, call `irag_start_session`, retain its `session_key`, then call
`irag_get_context` before broad exploration; finish with `irag_update` and
`irag_finish_session` (pass the key when the client exposes it). Claude hooks
perform the same lifecycle automatically. CLI-only agents use
`session-begin`, `context`, `update`, and `session-end`. Your obligations are:

1. **Orient from memory, decide from source.** Query irag *first* — never
   open a blind grep or walk the tree to learn what the project is, that
   burns your tokens re-doing work the memory already paid for. But pages
   orient you; they do not license an edit. **Before you change a file,
   read that file.** A summary is a lossy, possibly stale description of
   code, and this document elsewhere tells you the code is always the
   truth — so an edit made on prose alone contradicts the rule you are
   here to follow. Cheap loop: irag to find *where* and *why*, the file
   itself to decide *what*. The knowledge base is your second brain;
   query it:
   - MCP: `irag_get_context`, `irag_search`, `irag_code_map`,
     `irag_impact`, and `irag_trace_claim` are the same operations below.
     `irag_get_main_summary` returns the unabridged live project record;
     `irag_audit` runs the defensive code/API/dependency review; and
     `irag_web_search` retrieves current evidence with sources. Use those
     larger/open-world tools when the task actually calls for them.
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
   - `irag audit` — deterministic code, API, security, architecture, and
     dependency review; `--offline` skips OSV
   - `irag web-search "<current question>"` — live external evidence with
     URLs and retrieval time; never present an unavailable search as current
2. **Update after editing — unconditionally.** If you created, modified
   or deleted files, call `irag_update` (MCP) or run `irag update` before
   you finish your turn. Do
   not reason about whether the Stop hook will cover it; run it anyway.
   It is a no-op when nothing has changed, so a redundant run is free,
   and a skipped run leaves the memory silently wrong for every later
   session.

   **Do not ask the user for permission to run it, and do not decline it
   on cost grounds.** Synthesizing changed files is what this tool is
   for; that cost is the product working, not an incident to escalate.
   If you want the user to know the size first, run `irag update
   --dry-run` — it reports how many pages would be synthesized and roughly
   what it would cost, without writing anything or calling the model —
   then run the update and report what it did.
   "I did not run irag update, it costs N calls, your call" is a failure
   to do your job — the memory is now stale and the user has to notice
   and fix it by hand.

   **`irag status` alone cannot tell you an update is needed.** It reports
   from the database, so files edited since the last sync are not counted
   in "pages due" — it prints a separate `! N file(s) changed since their
   page was written` warning for those. Treat either signal as "run
   update", and never read `pages due for synthesis : 0` as "nothing to
   do" while that warning is present.
3. **Keep the diary.** MCP clients call `irag_start_session` first and
   `irag_finish_session` last; current stateless clients pass the returned
   `session_key` to writes and the final call so identity stays isolated.
   Claude Code with verified hooks does this automatically. CLI-only agents
   run `irag session-begin` first and `irag session-end` last.

   **CLI-only agents generate one id and pass it to every write.** MCP and
   Claude hooks own the key automatically:

   ```
   irag session-begin --id <your-unique-id> --agent <you>
   irag update --id <same-id>          # every time, see rule 2
   irag session-end --id <same-id>
   ```

   Any stable unique string works — a uuid, a timestamp plus your name. This
   is not optional hygiene: without it irag has to guess which session a
   write belongs to from what happens to be open, and when two agents are
   open at once no guess can be right. Concurrent work then gets credited to
   nobody, and `irag session-end` will refuse to close anything rather than
   end someone else's session. If you forget, `irag session-begin` prints the
   id it minted for you — use that one.

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
   - `irag tried "<approach>" --because "<why it failed>" --module <path>`
     for a **dead end**. What you ruled out is worth as much as what
     worked — without it the next session pays full price to rediscover
     the same failure.
   - `irag verify "<what is true>" --cmd "<command>" --expect "<text>"
     --module <path>` for anything you learned by **running** something.
     A page can only be checked against the code's text; this is the one
     kind of memory that carries its own proof, and `irag check` re-runs
     it, so it fails the build if the behaviour ever changes. Use it for
     the facts that cost you the most to discover.
   - `irag candidates` lists drafts written automatically when a command
     failed. Confirm the useful ones with `irag learn`.
5. **When knowledge is feature-shaped, not file-shaped.** If what matters
   spans several files that share no folder ("how does auth affect
   uploads?"), make it a page:
   `irag topic "<concept>" --files a.py,b.py,c.py`, then `irag update`.
   Folder pages cannot express this — they are directory-shaped.
6. **`irag suggest`** prints what needs doing right now with the exact
   command for it. Run it when you are unsure what state the memory is in.

## Phase 3 — When memory and code disagree

THE CODE IS THE TRUTH, always. If a page carries a ⚠ warning or `irag
check` fails:

1. Call `irag_list_contradictions` or run `irag contradictions` — read the
   exact claims (claim vs truth, with severity). The result includes a complete
   coding-agent repair brief; the dashboard can open one per row or one master
   HTML handoff.
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
`contradictions` · `brief` · `suggest` · `facts` · `candidates` · `web-search`
Write: `update` · `learn` · `record-decision` · `tried` · `verify` ·
`topic` · `resolve`
Analysis & protocol: `audit` · `mcp` · `provider` · `contradiction-report`
Setup (Phase 1 only): `init` · `doctor` · `claude-setup`
Human-only: `rollback` · `pin` · `backup` · `dashboard` · `obsidian` ·
`export`

`irag brief <file>` is the fast one: everything known about a single file
— disputed claims, verified behaviour, dead ends, who imports it — in a
few lines. After `claude-setup` it fires automatically before every edit.

Full reference: docs/CLI_REFERENCE.md · Setup details: docs/SETUP.md ·
How it works: docs/ARCHITECTURE.md
