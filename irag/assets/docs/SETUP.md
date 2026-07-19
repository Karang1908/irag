# irag — Setup Guide

## Requirements

- Python **3.11+** (irag uses `tomllib`; the core has zero third-party
  dependencies)
- git on PATH
- An LLM CLI that reads a prompt on **stdin** and writes markdown to
  **stdout** (default: `claude -p`; any command works)

## Install

From PyPI (when published) or from source:

```bash
pip install irag
# or, from a checkout:
pip install -e /path/to/irag
```

Verify: `irag --help`.

## Initialize a project

```bash
cd your-project
irag init
```

Git is optional. With a git repo, irag follows commits (richer provenance
— commit messages feed synthesis) and installs hooks. In a plain folder,
irag runs in **snapshot mode**: it fingerprints every file and `irag
sync` detects adds/edits/deletes by content hash — no commits needed.
`git init` later and it upgrades automatically (`[ingest].mode = "auto"`).
Set `mode = "snapshot"` to track uncommitted work even inside a git repo.

This does four things:

1. Creates `.irag/memory.db` (SQLite, WAL mode) and `.irag/config.toml`
2. Installs a `post-commit` git hook (`irag ingest-commit HEAD` — silent,
   never fails your commit). If you already have a post-commit hook, irag
   will not clobber it and prints the one line to append instead.
3. Ingests your existing git history into the event queue
4. Prints next steps

Add `.irag/` to `.gitignore` if you don't want the database committed
(committing it is also fine — it is a single file and merges are rare
because writes are append-only).

## Configure the LLM

Edit `.irag/config.toml`:

```toml
[llm]
command = "claude -p"    # prompt piped via stdin, markdown on stdout
model_label = "claude"   # recorded on every revision for provenance
timeout = 300
```

Prompt delivery adapts to your CLI via placeholders in `command`:
no placeholder → prompt piped on stdin (claude -p); `{prompt}` →
substituted as one argument; `{promptfile}` → prompt written to a temp
file whose path is substituted.

### Antigravity CLI (agy)

agy takes the prompt as an argument (`-p`), not stdin. Recommended
config (macOS/zsh — also fine on Linux):

```toml
[llm]
command = "agy --dangerously-skip-permissions -p {prompt}"
model_label = "antigravity"
timeout = 600
```

Known agy issue: `-p` can silently drop stdout when run without a TTY
(github issue #76). irag detects empty output and tells you; if you hit
it, use the pseudo-TTY wrapper instead:

```toml
# macOS
command = "script -q /dev/null sh -c 'agy --dangerously-skip-permissions -p \"$(cat $0)\"' {promptfile}"
# Linux
command = "script -qec 'agy --dangerously-skip-permissions -p \"$(cat $0)\"' /dev/null {promptfile}"
```

(irag strips the terminal escape codes `script` adds.) Add `--model
<name>` to pin a model. Verify with `irag doctor --probe-llm` before your
first `irag update`.

Any command with the same contract works, e.g. a local model wrapper,
`ollama run …` behind a script, or a mock for testing. Preview exactly
what would be sent without spending tokens:

```bash
irag synthesize --dry-run
```

## Granularity & ignoring

Every non-ignored, non-binary, non-hidden file gets its own page; every
folder gets a rollup page synthesized from its children (bottom-up, so
the root page is a summary of summaries). To exclude things, create a
`.iragignore` at the project root:

```
# names match any path segment
secrets
fixtures
# globs and paths work too
*.min.js
docs/generated
```

Built-in exclusions: the [modules].ignore list (node_modules, venv, ...),
all hidden files/dirs (.env never reaches an LLM prompt), binary
extensions, and irag's own artifacts. Note per-file granularity multiplies
LLM cost roughly by files-per-folder; tune [staleness].threshold to
control refresh frequency.

## Other config knobs

```toml
[modules]
ignore = [".irag", ".git", "node_modules", "venv", ".venv", "dist", "build", "__pycache__"]

[staleness]
commit = 10              # per-commit staleness bump
dependency = 20          # extra bump when a manifest file is touched
threshold = 1            # default: resynthesize on every change
                         # (raise to batch, e.g. 100, on large repos)

[retrieval]
token_budget = 8000      # serving budget (~chars/4)
full_max = 4             # max pages served in FULL tier
min_score = 20           # relevance floor

[check]
max_staleness = 150
fail_on_contradictions = true
```

## Daily loop (short version)

With the default `threshold = 1`, a single command does everything:

```bash
irag update      # sync -> new page versions for every change -> lint -> export
```

Agents run it themselves (CLAUDE.md tells them to, after every edit).
Questions: `irag search <words>` for instant SQL lookup, `irag ask
"<question>"` for an AI answer with citations.

## Daily loop (manual steps)

```bash
# after some commits (the hook queued events automatically):
irag stale                 # see what's due
irag synthesize            # update due pages
irag lint                  # verify claims against the code
irag contradictions        # review, then: irag resolve <id> --notes "..."
irag export                # regenerate CLAUDE.md
irag obsidian              # optional: rebuild the Obsidian vault
```

Open `irag_vault/` in Obsidian (Open folder as vault) and follow
`_meta/graph_settings.md` for the graph color groups. Add `irag_vault/` to
`.gitignore` — it is a build artifact. Close the vault before
regenerating (Obsidian holds file locks).

`irag sync` catches up on any commits made while the hook was absent
(e.g. commits from CI, rebases, or before `irag init`).

## Claude Code integration (recommended)

```bash
irag claude-setup
```

One command installs three hooks in `.claude/settings.json` (merge-safe,
idempotent), making the whole loop mechanical:

- **SessionStart** — opens the conversation log (`irag session-begin`)
  and injects `irag context --budget 3000`, so every session starts
  already knowing the project's verified state, map, decisions, lessons,
  and a recap of the last conversations.
- **Stop** — runs `irag update` after every turn (a no-op when nothing
  changed), so memory is synthesized as you work.
- **SessionEnd** — closes the diary entry (`irag session-end`): the
  conversation is summarized and logged with everything it changed.

The regenerated CLAUDE.md also instructs agents to use `irag map`, `irag
impact`, and `irag why` instead of exploring, and to record knowledge with
`irag learn` / `irag record-decision` so it survives to the next session.

## Other agents (Cursor, Antigravity IDE, Codex, ...)

`irag export` writes **AGENTS.md** alongside CLAUDE.md with identical
content — AGENTS.md is the cross-tool convention most non-Claude agents
read at session start, so the "use irag, don't re-explore" instructions
reach them natively. Two caveats:

- Only Claude Code gets *enforced* hooks. Any other agent follows the
  written instructions — including `irag session-begin` first /
  `irag session-end` last for the conversation diary — as instructed
  compliance, not harness enforcement. If an agent forgets, that session
  is simply missing from `irag recap`.
- Antigravity's own convention file (`GEMINI.md`) takes precedence over
  AGENTS.md on conflicts; if you maintain one, don't contradict the irag
  instructions in it.

## Context-file export workflow

Treat `CLAUDE.md` and `AGENTS.md` as build artifacts:

```bash
irag export     # writes both, with a "generated by irag" header
```

Regenerate after each synthesis sweep (or add it to the same make target /
pre-push hook). Never hand-edit the exports — record durable facts with
`irag record-decision "..."` or let synthesis pick them up from commits.

## CI gate

Fail the build when memory disagrees with the code:

```yaml
# .github/workflows/irag.yml
jobs:
  irag-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install irag
      - run: irag lint && irag check
```

`irag check` exits 1 when there are open contradictions
(`fail_on_contradictions = true`) or any page's staleness exceeds
`max_staleness`.

## Smoke test

A deterministic end-to-end test (uses a mock LLM, no tokens):

```bash
sh tests/test_smoke.sh
```
