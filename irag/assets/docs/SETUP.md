# irag — Setup Guide

## Requirements

- Python **3.11+** (irag uses `tomllib`; the core has zero third-party
  dependencies)
- git on PATH
- An LLM provider CLI for writing summaries. Built-in adapters support
  Claude Code, Codex, Antigravity (`agy`), and Ollama; a custom command still
  works. Reading memory never requires a model.

## Install

Not on PyPI yet — install straight from the public repository, or
from a checkout:

```bash
pip install git+https://github.com/Karang1908/irag.git
# or, from a checkout:
pip install -e /path/to/irag
```

Verify: `irag --help`.

## Initialize a project

```bash
cd your-project
irag init
```

**The directory you run `init` in becomes the project — nothing above
it.** Git is optional. When the directory is itself a git repo's top
level, irag follows commits (richer provenance — commit messages feed
synthesis) and installs hooks. In a plain folder — or a folder that
sits *inside* some larger git repository (a versioned home directory,
a monorepo you only want one corner of) — irag runs in **snapshot
mode**: it fingerprints every file and `irag sync` detects
adds/edits/deletes by content hash, without touching the enclosing
repo. `git init` the project itself later and it upgrades automatically
(`[ingest].mode = "auto"`). Set `mode = "snapshot"` to track
uncommitted work even inside a git repo.

### Multiple projects & hierarchy

Every project keeps its own `.irag/`; commands operate on the nearest
one above your current directory, so you can work several projects in
parallel — each `irag dashboard` shows only its own project (and picks
the next free port automatically if you run more than one). Because
each project installs its own `CLAUDE.md`/`AGENTS.md` agent guide
composes hierarchically the way Claude Code reads it: global
(`~/.claude/CLAUDE.md`) → any parent directory's file → the project's
generated one.

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
provider = "claude"      # claude | codex | agy | ollama | custom
command = "claude -p"    # custom/legacy command only
model = ""               # provider default; required for ollama
model_label = "claude"   # custom-command provenance label
timeout = 300
retries = 1
input_cost_per_million = 0.0   # optional local estimate, never hard-coded
output_cost_per_million = 0.0
```

Built-in examples:

```toml
[llm]
provider = "codex"
model = ""
timeout = 300
retries = 1

# Or fully local:
# provider = "ollama"
# model = "qwen3-coder"
```

Run `irag provider` to validate the adapter without spending tokens and
`irag doctor --probe-llm` for one end-to-end output probe. Every invocation is
recorded locally with provider, model, input/output token estimates, duration,
attempt count, status, and an estimated cost only when you supply rates.

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

## Connect every coding agent through MCP

The summary provider above writes memory. Your coding agent reads and manages
that memory through the built-in, vendor-neutral MCP server:

```bash
codex mcp add irag -- irag mcp --root /absolute/path/to/project
claude mcp add --scope project irag -- irag mcp --root /absolute/path/to/project
```

Cursor, Windsurf, VS Code and other MCP clients use the same executable and
arguments. See [MCP.md](MCP.md) for configuration shapes, the complete tool
contract, and the recommended agent lifecycle.

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
dependency = 20          # bump applied to a file's IMPORTERS when it
                         # changes (its page may describe that file's
                         # interface), and again when a manifest is touched
threshold = 1            # default: resynthesize on every change
                         # (raise to batch, e.g. 100, on large repos)
skip_trivial = true      # an edit touching only comments/whitespace skips
                         # the model entirely (compared against a
                         # comment-stripped fingerprint of the file)

[llm]
parallel = 1             # pages synthesized at once. 1 is strictly
                         # sequential; raise it if your LLM CLI tolerates
                         # concurrent invocations (each is its own process)

[retrieval]
token_budget = 8000      # serving budget (~chars/4)
full_max = 4             # max pages served in FULL tier
min_score = 20           # relevance floor

[check]
max_staleness = 150
fail_on_contradictions = true
fail_on_facts = false    # see the warning below before enabling
```

### What irag refuses to read

File contents reach the configured LLM and are stored in
`.irag/memory.db`, so three classes of file are excluded before anything
reads them — regardless of what your ignore files say:

- **Dotfiles and dot-directories** (`.env`, `.git`, `.ssh`, …).
- **Credential-shaped names**: `id_rsa`, `*.pem`, `*.key`, `*.p12`,
  `secrets.*`, `credentials*`, `service-account*.json`, `*.env`, and
  similar.
- **Symlinks whose target leaves the project.** A link inside the repo
  pointing at `~/.ssh/id_rsa` would otherwise be read as an ordinary
  source file. Links that stay inside the project work normally.

`.gitignore` is honoured **in both modes**, not only when a git repo
exists — snapshot mode walks the tree directly, so without this an
otherwise-ignored `id_rsa` would be ingested. `.iragignore` adds
gitignore-style patterns of your own; negations (`!pattern`) are skipped,
which can only over-ignore, never under-ignore.

If something sensitive is still reachable, add it to `.iragignore` and run
`irag sync` — pages for files that become ignored are purged.

### Source files are treated as untrusted input

A page is written by a model that has just read the file, and that page is
injected into your agent automatically — at session start, and by the
`PreToolUse` hook immediately before the file is edited. So a comment
saying "ignore all previous instructions" is an attempt to author the
page, with a delivery mechanism attached.

Three things guard it: file content is fenced and explicitly labelled as
data rather than instructions, the output contract is restated *after* the
content so the last word is irag's, and any instruction-shaped line that
survives into a page (`ignore previous instructions`, `curl … | sh`, a
fake `<system>` block) is stripped before the page is stored, with a
warning naming the file.

The linter cannot help here — it verifies paths, versions and symbols, and
this payload is prose.

### The dashboard refuses cross-origin writes

`irag dashboard` binds to 127.0.0.1, but a page you have open in the same
browser can still POST to it. Every mutating endpoint therefore requires
`Content-Type: application/json` — which forces a CORS preflight the
same-origin policy blocks — and rejects a request whose `Origin` is not
localhost. Without this, any site could have triggered an `update`
(spending real money), a `rollback` (silently corrupting memory your agent
then reads as truth), or unbounded backups.

### Executable facts run shell commands — that is why they are off

`irag verify` stores a claim together with the command that proves it, and
`irag check` can re-run them. Those commands live in `.irag/memory.db`,
which is **meant to be committed and shared**.

So `fail_on_facts` defaults to `false`. With it on, `git clone && irag
check` would execute whatever commands the repository carries — on every
developer machine and CI runner that runs the gate. Enable it only in a
repository whose registered commands you vouch for, and treat a pull
request that adds a fact exactly like a pull request that adds a CI script.
When enabled, every command is printed before it runs, and
`irag check --skip-facts` overrides it for a single invocation.

## Daily loop (short version)

With the default `threshold = 1`, a single command does everything:

```bash
irag update      # sync -> new page versions for every change -> lint
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
irag export                # (re)install the CLAUDE.md/AGENTS.md guide
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

The installed CLAUDE.md also instructs agents to use `irag map`, `irag
impact`, and `irag why` instead of exploring, and to record knowledge with
`irag learn` / `irag record-decision` so it survives to the next session.

## Other agents (Cursor, Antigravity IDE, Codex, ...)

`irag export` installs **AGENTS.md** alongside CLAUDE.md with identical
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

`CLAUDE.md` and `AGENTS.md` are irag's installed agent guide, not memory dumps:

```bash
irag export     # (re)installs both from the bundled operator manual
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
      - run: pip install git+https://github.com/Karang1908/irag.git
      - run: irag lint && irag check
```

`irag check` exits 1 when there are open contradictions
(`fail_on_contradictions = true`) or any page's staleness exceeds
`max_staleness`.

It does **not** run executable facts unless `fail_on_facts = true` — see
the warning in "Other config knobs". If you do enable them in CI, note
that a fact whose tool is missing on the runner is reported but does not
fail the build: only a command that ran and disproved its claim gates.

## Smoke test

A deterministic end-to-end test (uses a mock LLM, no tokens):

```bash
sh tests/test_smoke.sh
```
