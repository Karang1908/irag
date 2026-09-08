# irag — Development Notes (for working on irag itself)

Context for AI agents working on this codebase.

## What this project is

A standalone CLI tool that replaces flat agent-memory files (CLAUDE.md,
.cursorrules) with a SQLite database in the target repo. Git commits feed
an event queue; an LLM synthesizes per-module context pages as append-only
revisions; a static linter records contradictions between page claims and
the actual code; retrieval serves tiered, token-budgeted context; and `irag
export` installs a static operator guide without dumping memory into it.

## Non-negotiable principles

1. **The model never does bookkeeping.** Locating, counting, dating,
   diffing, verifying = SQL. The LLM only writes prose (synthesis) and
   optionally audits prose (lint --llm). Do not add features that make the
   LLM track state.
2. **Memory is data with a prose projection.** CLAUDE.md in target repos
   is a generated artifact. Never make it a source of truth.
3. **Append-only revisions.** Never UPDATE or DELETE rows in `revisions`
   during normal operation; the explicit destructive
   `irag forget --purge` command is the sole exception. Rollback = insert the
   old body as a new revision. The
   `revisions_advance` trigger in db.py owns the current-revision pointer
   and staleness reset — never move the pointer in Python.
4. **Structural facts are parsed, never inferred.** The symbols/deps
   tables come from ast/regex only. No LLM output is ever written into
   them.
5. **Stdlib-only core.** Python >= 3.11, zero runtime dependencies.
6. **No self-churn.** irag-generated artifacts (CLAUDE.md, AGENTS.md,
   irag_vault/) are ignored by ingestion (`ingest.SELF_ARTIFACTS`) —
   never let irag's own output feed its queue.
7. **Idempotent ingestion.** Events are unique on (source_ref,
   subject_id); the git hook and `irag sync` may overlap safely.

## Layout

| File | Owns |
|---|---|
| `irag/db.py` | schema, ordered migrations, automatic pre-upgrade backups, downgrade refusal, triggers, shared helpers |
| `irag/mcp.py` | standards-compliant JSON-RPC stdio MCP server and universal tool contracts |
| `irag/proof.py` | loopback-only full-stack contract, crawl, API, browser, test-command, and stress evidence |
| `irag/providers.py` | Claude, Codex, agy, Ollama, and custom process adapters with retries/telemetry |
| `irag/reports.py` | escaped, self-contained contradiction repair briefs for coding agents |
| `irag/config.py` | defaults + `.irag/config.toml` deep-merge and semantic validation (tomllib) |
| `irag/structure.py` | deterministic map: symbols + deps tables via ast/regex; scan gated on the working-tree fingerprint; feeds linter (exact symbols), synthesis (facts block), retrieval (+20 dep neighbors, map blocks), links/Obsidian |
| `irag/ingest.py` | hybrid git-commit + working-tree fingerprint changes → per-FILE events; ancestor folders bumped at half weight; ignoring = config segments + .iragignore globs + hidden paths + binary exts |
| `irag/synthesis.py` | two-phase hierarchical sweep: FILE pages (content+facts prompt) then FOLDER pages bottom-up (children-summary prompt); preserves critical interfaces, invariants, data flow, failure/security behavior, and rename context while bounding large-file input |
| `irag/linter.py` | static checks: missing_path (high), version_mismatch (medium), missing_symbol (low, heuristic); dedupe on identical open claim; auto-resolve static rows that stop failing (never llm_flagged rows) |
| `irag/retrieval.py` | scoring (+50 open-module, +25 parent, ≤+30 FTS, +15 link-hop, +10 recent, −20 stale, −40 contradicted) and FULL/DIGEST/INDEX serving under token_budget |
| `irag/provenance.py` | why / asof / rollback / pin |
| `irag/sessions.py` | conversation diary: begin/end with ID high-water marks (not timestamps), per-file `changes_detail`, a readable LLM narrative, and a separate deterministic `critical_context` ledger that the model cannot omit |
| `irag/export.py` | bundled static operator guide → CLAUDE.md + AGENTS.md (identical content), with ownership guards and atomic replacement |
| `irag/obsidian.py` | db → Obsidian vault: Modules/ notes, _versions/ chain (#version), _meta/ stats+instructions; wipe-and-rebuild behind `.irag-vault` marker guard |
| `irag/check.py` | CI gate: exit 1 on open contradictions or staleness > max |
| `irag/hooks.py` | post-commit/post-merge/post-checkout installers (never clobber foreign hooks) |
| `irag/stats.py` | shared metric builders (status_dict, token_series, activity) for CLI + dashboard |
| `irag/dashboard.py` | stdlib ThreadingHTTPServer on 127.0.0.1; /api/* JSON; chat router; persistent update/proof jobs with SSE progress and reconnect/poll fallback; assets/dashboard.html SPA |
| `irag/doctor.py` | install diagnostics: env, db/FTS integrity, config types, LLM probe, hooks, queue health |
| `irag/cli.py` | argparse; every command resolves the nearest `.irag` root (or cwd for init), opens `.irag/memory.db` |

## Conventions

- Product subprocesses use argument lists. The sole `shell=True` path is
  executable facts: user-authored proof commands, stored and run only after
  explicit opt-in via `[check].fail_on_facts` or `irag verify`.
- User-facing errors via `raise SystemExit("irag: ...")` — no raw
  tracebacks for user mistakes.
- Every write path ends in `conn.commit()`.
- Contradicted pages are never served without a warning banner.
- Docstrings and type hints on all public functions.
- Docs live twice: `docs/*.md` (repo) and `irag/assets/docs/*.md`
  (served by the dashboard's Docs tab). There is no build step — after
  editing a doc, `cp docs/<file>.md irag/assets/docs/` manually or they
  drift silently.
- `__version__` in `irag/__init__.py` and `version` in `pyproject.toml`
  must be bumped together — nothing enforces the pairing.
- Schema changes: additive upgrades go in `SCHEMA` (fresh installs) **and** a
  guarded `ALTER TABLE` via `db._add_column_if_missing` in `ensure_db`
  (existing installs). The whole upgrade is serialized and transactional.
  Schema changes require a new ordered migration, a rollback-safe data step,
  and an upgrade test. `ensure_db` creates a pre-migration SQLite backup and
  refuses databases newer than the running binary.

## Search duality (keep both)

`irag search` = SQL search (FTS5, deterministic, zero tokens).
`irag ask` = AI search (retrieval.serve builds context, run_llm answers,
citations required, contradicted pages flagged). Never merge them; never
make `ask` bypass retrieval.

## Testing

`sh tests/test_smoke.sh` — deterministic end-to-end run in a temp repo
using `tests/mock_llm.py` (emits a fake `src/ghost.py` reference so the
linter must catch a missing_path). It must pass before any release. The
mock LLM contract equals the real one: prompt on stdin, markdown on
stdout.

`python -m unittest discover -s tests -p 'test_*.py' -v` — focused protocol,
provider, migration, race-safety, rename-continuity, summary-retention, and
durable-job regressions. CI runs this focused suite natively on Ubuntu,
macOS, and Windows, and the full smoke suite on every supported Python.

## Roadmap (do not build unless asked)

Confidence scoring from lint history, pull-request contradiction annotations,
real-model summary-retention evals, and authenticated remote MCP transport.
