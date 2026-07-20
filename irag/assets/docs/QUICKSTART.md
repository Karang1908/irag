# Quickstart

irag is a verified knowledge base for your codebase: every file and
folder gets an AI-written summary page, versioned on every change,
fact-checked against the code, and served to coding agents.

## 1. Install (once)

```
pip install -e ./irag      # Python 3.11+, zero dependencies
```

## 2. Turn it on in a project

```
cd my-project
irag init                  # works with or without git
```

Then set your LLM in `.irag/config.toml` (default `claude -p` works if
you have Claude Code) and verify with `irag doctor --probe-llm`.

## 3. The one command to remember

```
irag update
```

Detects every change → writes a new version of each affected file page →
rolls up folder pages bottom-up → fact-checks everything. It updates the
database only; the `CLAUDE.md`/`AGENTS.md` agent guide is installed once
by `irag init` and tells agents to run it after edits.

## 4. Ask things

- **SQL search** (instant, free): `irag search "auth token"` or type
  keywords / a file path in the dashboard chat.
- **AI search** (reasoned, cited): `irag ask "how does login work?"` or
  ask a question in the dashboard chat — routing is automatic.

## 5. See it

- `irag dashboard` — this UI: live token burn and activity, health,
  the interactive dependency graph (pan / zoom / drag), the session
  diary with per-file changes, chat, and these docs.
- `irag obsidian` — the knowledge graph as an Obsidian vault
  (files green, folders blue, contradictions red, history gray).

## Coming back after a break

```
irag recap        # "previously on this project" — the conversation diary
irag sessions     # every logged conversation with what it changed
```

Fresh chats get the recap injected automatically, so resuming costs ~zero
tokens.

## Everyday commands

`irag status` · `irag stale` · `irag map <path>` · `irag impact <path>` ·
`irag why "<claim>"` · `irag diff <file>` · `irag contradictions` ·
`irag learn "<gotcha>"` · `irag record-decision "<choice>"` ·
`irag claude-setup` · `irag doctor` · `irag backup`
