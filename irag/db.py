"""irag.db — SQLite schema, connection, and migrations.

The five core relations of the Relational Synthesis Layer, ported from the
reference implementation to SQLite. Append-only where it matters; SQLite
triggers maintain the current-revision pointer, reset staleness, and keep
the FTS5 inverted index in sync.
"""
from __future__ import annotations

import sqlite3
import datetime as _datetime
from pathlib import Path

SCHEMA_VERSION = 8

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- An ordered, inspectable migration ledger. ``meta.schema_version`` is the
-- fast current pointer; this table is the audit trail that explains how a
-- database reached it.
CREATE TABLE IF NOT EXISTS schema_migrations (
  version     INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  applied_at  TEXT DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- Layer 2: synthesis
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pages (
  page_id             INTEGER PRIMARY KEY,
  page_type           TEXT NOT NULL DEFAULT 'module',   -- module | overview | decisions
  title               TEXT NOT NULL,
  subject_type        TEXT NOT NULL DEFAULT 'module',   -- polymorphic subject reference
  subject_id          TEXT NOT NULL,                    -- e.g. 'src/auth' or 'repo'
  current_revision_id INTEGER,
  staleness_score     INTEGER NOT NULL DEFAULT 0,
  pinned              INTEGER NOT NULL DEFAULT 0,
  confidence          REAL    NOT NULL DEFAULT 1.0,
  created_at          TEXT DEFAULT (datetime('now')),
  last_updated_at     TEXT DEFAULT (datetime('now')),
  UNIQUE(subject_type, subject_id)
);

CREATE TABLE IF NOT EXISTS revisions (
  revision_id           INTEGER PRIMARY KEY,
  page_id               INTEGER NOT NULL REFERENCES pages(page_id),
  version_number        INTEGER NOT NULL,
  body_markdown         TEXT NOT NULL,
  change_summary        TEXT,
  triggered_by_event_id INTEGER REFERENCES events(event_id),
  llm_model_used        TEXT,
  tokens_used           INTEGER DEFAULT 0,
  session_key          TEXT,
  created_at            TEXT DEFAULT (datetime('now'))
);
-- UNIQUE so a racing duplicate (page_id, version_number) fails loudly with
-- an IntegrityError instead of silently orphaning a revision. The version
-- number is computed inside the INSERT (a subquery under WAL's write lock)
-- at every writer, so this constraint should never actually trip.
CREATE UNIQUE INDEX IF NOT EXISTS idx_revisions_page ON revisions(page_id, version_number);

CREATE TABLE IF NOT EXISTS links (
  link_id        INTEGER PRIMARY KEY,
  source_page_id INTEGER NOT NULL REFERENCES pages(page_id),
  target_page_id INTEGER NOT NULL REFERENCES pages(page_id),
  link_type      TEXT DEFAULT 'related',
  UNIQUE(source_page_id, target_page_id, link_type)
);

-- ---------------------------------------------------------------
-- Layer 3: audit & control
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
  event_id    INTEGER PRIMARY KEY,
  event_type  TEXT NOT NULL,              -- commit | decision | rollback | sweep | manual
  source_ref  TEXT,                       -- commit hash, session id, ...
  subject_id  TEXT,                       -- module the event concerns ('' = repo-wide)
  payload     TEXT,                       -- JSON details (changed files, message, ...)
  status      TEXT NOT NULL DEFAULT 'queued',  -- queued|processing|completed|failed|skipped
  tokens_used INTEGER DEFAULT 0,
  session_key TEXT,
  created_at  TEXT DEFAULT (datetime('now')),
  processed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_queue ON events(status, subject_id, created_at);

-- Executable memory: a claim that carries its own proof. Every other kind
-- of page says what the code *is*; these say what it *does*, and are the
-- only facts irag can re-establish from scratch rather than trust. `check`
-- re-runs them, so a behavioural claim that stops holding fails the build
-- the same way a hallucinated path does.
CREATE TABLE IF NOT EXISTS facts (
  fact_id      INTEGER PRIMARY KEY,
  claim        TEXT NOT NULL,
  cmd          TEXT NOT NULL,      -- shell command that demonstrates it
  expect       TEXT,               -- substring required in output (NULL = exit 0)
  expect_exit  INTEGER,            -- required exit code, when set
  subject_id   TEXT,               -- module this is about ('' = repo-wide)
  created_at   TEXT DEFAULT (datetime('now')),
  last_run_at  TEXT,
  last_status  TEXT,               -- pass | fail | error
  last_output  TEXT,
  session_key  TEXT,
  UNIQUE(claim, cmd)
);
CREATE INDEX IF NOT EXISTS idx_facts_subject ON facts(subject_id);

-- Concept pages: knowledge is feature-shaped, storage was file-shaped.
-- "How does root privilege affect scanning?" spans a scanner, a util and a
-- template that share no folder, so no folder page can answer it. Members
-- are curated by hand — this is the one page type irag does not infer.
CREATE TABLE IF NOT EXISTS topic_members (
  topic      TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  PRIMARY KEY (topic, subject_id)
);

CREATE TABLE IF NOT EXISTS contradictions (
  contradiction_id INTEGER PRIMARY KEY,
  page_id          INTEGER NOT NULL REFERENCES pages(page_id),
  revision_id      INTEGER REFERENCES revisions(revision_id),
  claim            TEXT,
  truth            TEXT,
  ctype            TEXT,                  -- missing_path | version_mismatch | missing_symbol | llm_flagged
  severity         TEXT DEFAULT 'medium', -- low | medium | high
  detected_by      TEXT,
  detected_at      TEXT DEFAULT (datetime('now')),
  resolved_at      TEXT,
  resolution_notes TEXT,
  -- 'manual' (a human judged the flag spurious) | 'auto' (the claim simply
  -- stopped failing). Only manual resolutions suppress a re-raise: an
  -- auto-resolved claim that starts failing again is a real regression.
  resolution_kind  TEXT
);
CREATE INDEX IF NOT EXISTS idx_contradictions_open
  ON contradictions(page_id) WHERE resolved_at IS NULL;

-- Conversation/session log: one row per coding-agent conversation,
-- with what changed — the project's diary, outside any chat window
CREATE TABLE IF NOT EXISTS sessions (
  session_id       INTEGER PRIMARY KEY,
  started_at       TEXT DEFAULT (datetime('now')),
  ended_at         TEXT,
  status           TEXT NOT NULL DEFAULT 'open',  -- open|closed|interrupted
  agent            TEXT DEFAULT 'claude-code',
  summary          TEXT,
  files_changed    TEXT,          -- JSON list of file subjects
  versions_written INTEGER DEFAULT 0,
  decisions        INTEGER DEFAULT 0,
  lessons          INTEGER DEFAULT 0,
  start_event_id    INTEGER DEFAULT 0,   -- high-water marks: the session
  start_revision_id INTEGER DEFAULT 0,   -- owns only rows created after
  changes_detail    TEXT,                -- JSON [{subject_id,version_number,
                                          -- change_summary}, ...] for every
                                          -- revision written this session
  session_key       TEXT,
  critical_context  TEXT,                -- lossless JSON decisions, lessons,
                                          -- commits, files, revision details
  task_label        TEXT,
  branch_name       TEXT,
  worktree_path     TEXT,
  head_sha          TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(started_at);

-- Verbatim conversation transcript (opt-in: [sessions].capture_transcript).
-- One row per user/assistant message in a logged session, so the diary can
-- hold the actual back-and-forth, not only the changes it produced. Off by
-- default because transcripts can carry secrets; enabling it is one config
-- line and the SessionEnd hook already pipes the transcript path in.
CREATE TABLE IF NOT EXISTS session_messages (
  message_id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(session_id),
  seq        INTEGER NOT NULL,
  role       TEXT NOT NULL,          -- user | assistant
  content    TEXT NOT NULL,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_session_messages
  ON session_messages(session_id, seq);

-- Working-tree fingerprints for snapshot-mode ingestion (no git needed)
CREATE TABLE IF NOT EXISTS tree_state (
  path TEXT PRIMARY KEY,
  hash TEXT NOT NULL
);

-- ---------------------------------------------------------------
-- Structural map (deterministic, rebuilt by irag.structure.scan)
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS symbols (
  symbol_id  INTEGER PRIMARY KEY,
  subject_id TEXT NOT NULL,
  file       TEXT NOT NULL,
  name       TEXT NOT NULL,
  kind       TEXT NOT NULL,          -- function | class | method | type
  line       INTEGER
);
CREATE INDEX IF NOT EXISTS idx_symbols_subject ON symbols(subject_id, name);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);

CREATE TABLE IF NOT EXISTS deps (
  dep_id         INTEGER PRIMARY KEY,
  source_subject TEXT NOT NULL,
  target_subject TEXT NOT NULL,
  import_count   INTEGER NOT NULL DEFAULT 1,
  UNIQUE(source_subject, target_subject)
);

-- Per-file parser fingerprints make content-only structural refreshes
-- incremental. Topology changes still intentionally trigger a full pass so
-- newly resolvable imports cannot be missed.
CREATE TABLE IF NOT EXISTS scan_state (
  path TEXT PRIMARY KEY,
  hash TEXT NOT NULL
);

-- Dashboard work survives page reloads and server restarts. Logs are JSON
-- arrays because they are short, ordered, and replaced atomically.
CREATE TABLE IF NOT EXISTS jobs (
  job_id       TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,
  status       TEXT NOT NULL,       -- queued | running | completed | failed
  progress     INTEGER NOT NULL DEFAULT 0,
  log_json     TEXT NOT NULL DEFAULT '[]',
  error        TEXT,
  created_at   TEXT DEFAULT (datetime('now')),
  started_at   TEXT,
  finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

-- Provider-neutral model accounting. Rates are user-supplied, so cost stays
-- NULL instead of pretending a hard-coded price is timeless or exact.
CREATE TABLE IF NOT EXISTS llm_runs (
  run_id              INTEGER PRIMARY KEY,
  provider            TEXT NOT NULL,
  model               TEXT,
  purpose             TEXT,
  status               TEXT NOT NULL,
  input_tokens         INTEGER NOT NULL DEFAULT 0,
  output_tokens        INTEGER NOT NULL DEFAULT 0,
  estimated_cost_usd   REAL,
  duration_ms          INTEGER NOT NULL DEFAULT 0,
  attempt_count        INTEGER NOT NULL DEFAULT 1,
  error                TEXT,
  created_at           TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_llm_runs_created ON llm_runs(created_at DESC);

-- Deterministic audit snapshots. The complete JSON result is retained so a
-- developer can compare what the scanner saw with the tree fingerprint that
-- produced it; a stale report is never presented as current.
CREATE TABLE IF NOT EXISTS audit_runs (
  audit_id      INTEGER PRIMARY KEY,
  tree_fingerprint TEXT NOT NULL,
  files_scanned INTEGER NOT NULL DEFAULT 0,
  lines_scanned INTEGER NOT NULL DEFAULT 0,
  finding_count INTEGER NOT NULL DEFAULT 0,
  result_json   TEXT NOT NULL,
  duration_ms   INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_runs_created
  ON audit_runs(created_at DESC, audit_id DESC);

-- The Thinking Studio is project memory too. Chat history and generated idea
-- sets live beside sessions rather than disappearing with browser storage.
CREATE TABLE IF NOT EXISTS studio_messages (
  message_id    INTEGER PRIMARY KEY,
  role          TEXT NOT NULL,       -- user | assistant
  mode          TEXT NOT NULL,
  content       TEXT NOT NULL,
  sources_json  TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_studio_messages_created
  ON studio_messages(message_id DESC);

CREATE TABLE IF NOT EXISTS studio_ideas (
  idea_id       INTEGER PRIMARY KEY,
  mode          TEXT NOT NULL,
  title         TEXT NOT NULL,
  body_markdown TEXT NOT NULL,
  sources_json  TEXT NOT NULL DEFAULT '[]',
  status        TEXT NOT NULL DEFAULT 'active', -- active | archived
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_studio_ideas_created
  ON studio_ideas(status, idea_id DESC);

-- Review is a workflow, not a pile of warnings. Finding ids are stable across
-- scans, so a developer can resolve, accept, or defer one with an explicit
-- rationale while a future scan continues to recognize the same evidence.
CREATE TABLE IF NOT EXISTS audit_triage (
  finding_id    TEXT PRIMARY KEY,
  status        TEXT NOT NULL DEFAULT 'open',
  rationale     TEXT NOT NULL DEFAULT '',
  expires_at    TEXT,
  updated_at    TEXT DEFAULT (datetime('now'))
);

-- Product work needs the same durable evidence trail as code work. Experiments
-- connect an idea to a falsifiable hypothesis, metric, outcome, and decision.
CREATE TABLE IF NOT EXISTS experiments (
  experiment_id INTEGER PRIMARY KEY,
  title         TEXT NOT NULL,
  hypothesis    TEXT NOT NULL,
  metric        TEXT NOT NULL,
  target        TEXT NOT NULL DEFAULT '',
  status        TEXT NOT NULL DEFAULT 'planned',
  outcome       TEXT NOT NULL DEFAULT '',
  decision      TEXT NOT NULL DEFAULT '',
  created_at    TEXT DEFAULT (datetime('now')),
  updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_experiments_status
  ON experiments(status, experiment_id DESC);

-- Watchlists never pretend model memory is current. Every refresh stores the
-- exact dated search evidence so the user can see what changed between runs.
CREATE TABLE IF NOT EXISTS watchlists (
  watchlist_id  INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  query         TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active',
  created_at    TEXT DEFAULT (datetime('now')),
  updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS watchlist_snapshots (
  snapshot_id   INTEGER PRIMARY KEY,
  watchlist_id  INTEGER NOT NULL REFERENCES watchlists(watchlist_id),
  provider      TEXT NOT NULL,
  retrieved_at  TEXT NOT NULL,
  results_json  TEXT NOT NULL,
  added_json    TEXT NOT NULL DEFAULT '[]',
  removed_json  TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_watchlist_snapshots
  ON watchlist_snapshots(watchlist_id, snapshot_id DESC);

-- Mergeable team-memory bundles use content-derived record ids. This ledger
-- makes imports idempotent without making a shared SQLite file a Git merge
-- target, and intentionally excludes transcripts and executable facts.
CREATE TABLE IF NOT EXISTS memory_imports (
  record_id     TEXT PRIMARY KEY,
  record_type   TEXT NOT NULL,
  content_hash  TEXT NOT NULL DEFAULT '',
  imported_at   TEXT DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------
-- Inverted index: FTS5 over revision bodies (external-content table)
-- ---------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS revisions_fts USING fts5(
  body_markdown,
  content='revisions',
  content_rowid='revision_id'
);

-- Keep FTS in sync (append-only in practice; delete/update covered anyway)
CREATE TRIGGER IF NOT EXISTS revisions_ai AFTER INSERT ON revisions BEGIN
  INSERT INTO revisions_fts(rowid, body_markdown)
  VALUES (new.revision_id, new.body_markdown);
END;
CREATE TRIGGER IF NOT EXISTS revisions_ad AFTER DELETE ON revisions BEGIN
  INSERT INTO revisions_fts(revisions_fts, rowid, body_markdown)
  VALUES ('delete', old.revision_id, old.body_markdown);
END;

-- The signature trigger of the architecture: a new revision advances the
-- page pointer and resets staleness — no application code involved.
CREATE TRIGGER IF NOT EXISTS revisions_advance AFTER INSERT ON revisions BEGIN
  UPDATE pages
     SET current_revision_id = new.revision_id,
         last_updated_at     = datetime('now'),
         staleness_score     = 0
   WHERE page_id = new.page_id;
END;
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # concurrent writers (a Stop-hook `irag update` overlapping another
    # session, a rollback racing a synthesis pass) should WAIT for the
    # WAL write lock, not fail instantly with "database is locked"
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


# Which conversation this PROCESS is acting for. `irag update` is spawned by
# the Stop hook, which supplies the session id, so everything one process
# writes belongs to one session - that is what makes stamping cheap and
# correct without threading a key through every sync() call site.
_ACTIVE_KEY: str | None = None


def set_active_key(key: str | None) -> None:
    """Record who this process writes for.

    A falsy key is IGNORED rather than clearing an already-resolved one.
    `cmd_update` called this with the result of an optional `--id`, which is
    None without a hook payload, and so wiped the key `_open()` had just
    resolved from the sole open session: every write became unattributable and
    the diary reported that nothing had happened. Set-only removes that whole
    class of mistake instead of guarding each call site.
    """
    global _ACTIVE_KEY
    if key:
        _ACTIVE_KEY = key


def active_key() -> str | None:
    return _ACTIVE_KEY


def replace_active_key(key: str | None) -> str | None:
    """Set even a null key and return the previous value for scoped callers.

    ``set_active_key`` intentionally ignores nulls because one-shot CLI
    commands resolve attribution before their handler runs. Long-lived MCP
    servers need stronger isolation: after one session ends, the next call
    must not inherit that session merely because it shares a Python process.
    """
    global _ACTIVE_KEY
    previous = _ACTIVE_KEY
    _ACTIVE_KEY = key
    return previous


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    had_schema = _database_has_schema(conn)
    old_version = _stored_schema_version(conn)
    if old_version > SCHEMA_VERSION:
        conn.close()
        raise SystemExit(
            f"irag: this database uses schema v{old_version}, but this "
            f"irag only understands through v{SCHEMA_VERSION}. Refusing to "
            "open it because that would be an unsafe downgrade.")
    # Back up before *any* compatibility DDL touches an existing database.
    # A failed migration is therefore recoverable even if SQLite or the
    # process is interrupted between the idempotent schema pass and a data
    # backfill step.
    if had_schema and old_version < SCHEMA_VERSION:
        _backup_before_migration(conn, db_path, old_version, SCHEMA_VERSION)
    conn.executescript(SCHEMA)
    conn.commit()
    if _stored_schema_version(conn) == SCHEMA_VERSION:
        return conn
    # Upgrade the entire schema under one reserved write lock.  Otherwise two
    # processes opening an old database can both observe a missing column and
    # race the same ALTER TABLE; per-column commits also leave partial schemas
    # behind when a process is interrupted.
    try:
        conn.execute("BEGIN IMMEDIATE")
        # A second opener may have completed the upgrade while this connection
        # waited for the reserved lock, so check again inside the transaction.
        current = _stored_schema_version(conn)
        if current > SCHEMA_VERSION:
            raise SystemExit(
                f"irag: database schema v{current} is newer than supported "
                f"v{SCHEMA_VERSION}; refusing unsafe downgrade")
        for version, name, migrate in MIGRATIONS:
            if version <= current:
                continue
            migrate(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version,name) "
                "VALUES(?,?)", (version, name))
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(version),))
            current = version
        conn.commit()
    except BaseException:
        conn.rollback()
        conn.close()
        raise
    return conn


def _stored_schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.OperationalError:
        return 0
    try:
        return int(row["value"]) if row is not None else 0
    except (TypeError, ValueError):
        return 0


def _database_has_schema(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' LIMIT 1").fetchone() is not None


def _backup_before_migration(conn: sqlite3.Connection, db_path: Path,
                             old: int, new: int) -> Path:
    """Create a consistent SQLite backup before an automatic upgrade."""
    stamp = _datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = (db_path.parent / "backups" /
            f"pre-migration-v{old}-to-v{new}-{stamp}.db")
    dest.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
    except BaseException:
        target.close()
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    target.close()
    return dest


def _migration_1_legacy_columns(conn: sqlite3.Connection) -> None:
    """Add columns introduced before the migration ledger existed."""
    _add_column_if_missing(conn, "sessions", "changes_detail", "TEXT")
    # who owns this session: two agents on one repo (Claude Code + agy) each
    # ran bare `session-begin`/`session-end`, and without an identity the
    # second begin force-closed the first as 'interrupted' while it was still
    # running, then the first end closed the second's row.
    _add_column_if_missing(conn, "sessions", "session_key", "TEXT")
    # per-event / per-revision attribution. Session ownership alone was not
    # enough: two concurrent sessions each claimed the other's work, because
    # the diary window was "every event since I started" with no upper bound
    # and no owner. Stamping the writer lets a session report only its own.
    _add_column_if_missing(conn, "events", "session_key", "TEXT")
    _add_column_if_missing(conn, "revisions", "session_key", "TEXT")
    _add_column_if_missing(conn, "contradictions", "resolution_kind", "TEXT")
    _add_column_if_missing(conn, "pages", "content_fingerprint", "TEXT")
    # When a tracked file is deleted, its page must stop being served as
    # live memory — search and context were still handing agents summaries
    # of files that no longer exist, and a rename is a delete plus an add,
    # so every rename left one behind permanently. History is kept so
    # `asof` and `why` still work; only serving is affected.
    _add_column_if_missing(conn, "pages", "deleted_at", "TEXT")


def _migration_2_resolution_integrity(conn: sqlite3.Connection) -> None:
    """Preserve manual contradiction judgements and close old duplicates."""
    # Backfill resolutions made before the column existed, so upgrading does
    # not silently discard every judgement a human already made. The auto
    # paths write a fixed sentinel note; anything else was a person typing.
    conn.execute(
        "UPDATE contradictions SET resolution_kind = CASE "
        "  WHEN COALESCE(resolution_notes,'') LIKE 'auto-resolved:%' "
        "  THEN 'auto' ELSE 'manual' END "
        "WHERE resolved_at IS NOT NULL AND resolution_kind IS NULL")
    # Close the duplicates the old behaviour already spawned: rows that are
    # open only because re-synthesis re-raised a claim the human had dismissed.
    # Without this, upgrading fixes the future but leaves the existing pile.
    conn.execute(
        "UPDATE contradictions SET resolved_at=datetime('now'), "
        "resolution_kind='manual', "
        "resolution_notes='auto-closed on upgrade: duplicate of a claim you "
        "already resolved' "
        "WHERE resolved_at IS NULL AND EXISTS ("
        "  SELECT 1 FROM contradictions d WHERE d.page_id=contradictions.page_id"
        "    AND d.claim=contradictions.claim AND d.ctype=contradictions.ctype"
        "    AND d.resolved_at IS NOT NULL AND d.resolution_kind='manual')")


def _migration_3_unique_revisions(conn: sqlite3.Connection) -> None:
    """Promote legacy revision indexes when their data permits it."""
    _ensure_unique_revisions_index(conn)


def _migration_4_runtime_contracts(conn: sqlite3.Connection) -> None:
    """Install durable jobs, provider telemetry, and scan fingerprints.

    The current SCHEMA creates these tables before the ordered data pass. This
    explicit step is intentionally idempotent and gives the ledger a truthful
    boundary for databases upgrading from the v1 era.
    """
    conn.execute("UPDATE jobs SET status='failed', progress=100, "
                 "error=COALESCE(error,'dashboard stopped before completion'), "
                 "finished_at=COALESCE(finished_at,datetime('now')) "
                 "WHERE status IN ('queued','running')")


def _migration_5_contradiction_uniqueness(conn: sqlite3.Connection) -> None:
    """Make the open contradiction set race-safe across dashboard/CLI lint."""
    # Preserve the oldest open row as the stable id and close later duplicate
    # rows before adding the partial uniqueness constraint.
    conn.execute(
        "UPDATE contradictions SET resolved_at=datetime('now'), "
        "resolution_kind='auto', resolution_notes='auto-closed on upgrade: "
        "duplicate open contradiction' WHERE resolved_at IS NULL AND EXISTS ("
        "SELECT 1 FROM contradictions older WHERE older.page_id="
        "contradictions.page_id AND older.claim=contradictions.claim "
        "AND older.ctype=contradictions.ctype AND older.resolved_at IS NULL "
        "AND older.contradiction_id < contradictions.contradiction_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_contradictions_unique_open "
        "ON contradictions(page_id, claim, ctype) WHERE resolved_at IS NULL")


def _migration_6_lossless_session_context(conn: sqlite3.Connection) -> None:
    """Keep the evidence underneath every prose session summary."""
    _add_column_if_missing(conn, "sessions", "critical_context", "TEXT")


def _migration_7_intelligence_surfaces(conn: sqlite3.Connection) -> None:
    """Record the dashboard audit/studio storage boundary.

    ``SCHEMA`` creates the additive tables before ordered migrations run. The
    explicit ledger entry remains important: old binaries can refuse this
    database instead of silently ignoring newer project intelligence.
    """
    conn.execute("SELECT 1")


def _migration_8_delivery_and_evidence(conn: sqlite3.Connection) -> None:
    """Add branch-aware sessions and durable review/product evidence."""
    _add_column_if_missing(conn, "sessions", "task_label", "TEXT")
    _add_column_if_missing(conn, "sessions", "branch_name", "TEXT")
    _add_column_if_missing(conn, "sessions", "worktree_path", "TEXT")
    _add_column_if_missing(conn, "sessions", "head_sha", "TEXT")
    _add_column_if_missing(conn, "memory_imports", "content_hash",
                           "TEXT NOT NULL DEFAULT ''")


MIGRATIONS = (
    (1, "legacy columns", _migration_1_legacy_columns),
    (2, "contradiction integrity", _migration_2_resolution_integrity),
    (3, "unique revision versions", _migration_3_unique_revisions),
    (4, "durable jobs and incremental scans", _migration_4_runtime_contracts),
    (5, "race-safe open contradictions", _migration_5_contradiction_uniqueness),
    (6, "lossless session critical context", _migration_6_lossless_session_context),
    (7, "audit and thinking studio history", _migration_7_intelligence_surfaces),
    (8, "delivery, triage, and product evidence", _migration_8_delivery_and_evidence),
)


def _ensure_unique_revisions_index(conn: sqlite3.Connection) -> None:
    """Upgrade a pre-existing non-UNIQUE idx_revisions_page to UNIQUE.
    Fresh installs already get the UNIQUE index from SCHEMA; databases
    created before this change keep the old plain index (CREATE ... IF NOT
    EXISTS won't replace it), so promote it here. If legacy duplicate
    (page_id, version_number) rows already exist the promotion fails — we
    leave the plain index in place (the in-INSERT version computation still
    prevents new races) rather than crash on open."""
    row = conn.execute(
        "SELECT \"unique\" u FROM pragma_index_list('revisions') "
        "WHERE name='idx_revisions_page'").fetchone()
    if row is None or row["u"]:
        return
    duplicate = conn.execute(
        "SELECT 1 FROM revisions GROUP BY page_id, version_number "
        "HAVING COUNT(*) > 1 LIMIT 1").fetchone()
    if duplicate is not None:
        return
    conn.execute("DROP INDEX idx_revisions_page")
    conn.execute("CREATE UNIQUE INDEX idx_revisions_page "
                 "ON revisions(page_id, version_number)")


def _add_column_if_missing(conn: sqlite3.Connection, table: str,
                           column: str, coltype: str) -> None:
    """Additive schema patch for databases created before this column
    existed. CREATE TABLE IF NOT EXISTS in SCHEMA only helps fresh installs;
    this covers upgrades without a full migration system."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


# -----------------------------------------------------------------
# Small helpers used across modules
# -----------------------------------------------------------------
def get_meta(conn, key, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value):
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_or_create_page(conn, subject_id: str, subject_type: str = "module",
                       page_type: str = "module", title: str | None = None) -> sqlite3.Row:
    # Never commit inside a helper: callers combine page creation with an
    # event, revision, or full structural-map replacement.  The old commit
    # exposed half-written operations after a later error and made scan's
    # DELETE/INSERT rebuild observably non-atomic.  INSERT OR IGNORE also
    # handles a racing creator without rolling back the caller's work.
    conn.execute(
        "INSERT OR IGNORE INTO pages(page_type, title, subject_type, "
        "subject_id) VALUES(?,?,?,?)",
        (page_type, title or subject_id, subject_type, subject_id),
    )
    return conn.execute(
        "SELECT * FROM pages WHERE subject_type=? AND subject_id=?",
        (subject_type, subject_id),
    ).fetchone()


def current_body(conn, page_id: int) -> str | None:
    row = conn.execute(
        """SELECT r.body_markdown FROM pages p
           JOIN revisions r ON r.revision_id = p.current_revision_id
           WHERE p.page_id=?""",
        (page_id,),
    ).fetchone()
    return row["body_markdown"] if row else None


def open_contradiction_count(conn, page_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM contradictions WHERE page_id=? AND resolved_at IS NULL",
        (page_id,),
    ).fetchone()["c"]


def fts_sanitize(q: str) -> str:
    """Reduce a free-text query to a safe FTS5 OR-query. `\\w` is Unicode-
    aware on str patterns, so accented and CJK terms stay whole tokens
    instead of fragmenting into unrelated OR-clauses."""
    import re
    words = re.findall(r"\w{2,}", q)
    return " OR ".join(words[:12]) if words else ""


def like_escape(s: str) -> str:
    r"""Escape LIKE metacharacters so a literal path/name (which routinely
    contains `_`) can't act as a wildcard. Use with `LIKE ? ESCAPE '\'`."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def json_object(value: object) -> dict:
    """Decode a JSON object from an untrusted/legacy database boundary."""
    import json
    try:
        decoded = json.loads(value) if isinstance(value, (str, bytes)) else value
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def json_list(value: object) -> list:
    """Decode a JSON array without allowing one corrupt row to break a view."""
    import json
    try:
        decoded = json.loads(value) if isinstance(value, (str, bytes)) else value
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return []
    return decoded if isinstance(decoded, list) else []
