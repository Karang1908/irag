# Changelog

All notable changes to irag. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions follow semver
in spirit (no public API contract yet beyond the CLI).

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
  update" trap (HANDOFF §8.1). Silent when idle.
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
