"""irag.db — SQLite schema, connection, and migrations.

The five core relations of the Relational Synthesis Layer, ported from the
reference implementation to SQLite. Append-only where it matters; SQLite
triggers maintain the current-revision pointer, reset staleness, and keep
the FTS5 inverted index in sync.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
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
  changes_detail    TEXT                 -- JSON [{subject_id,version_number,
                                          -- change_summary}, ...] for every
                                          -- revision written this session
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


def ensure_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
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
    conn.commit()
    _ensure_unique_revisions_index(conn)
    return conn


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
    try:
        conn.execute("DROP INDEX idx_revisions_page")
        conn.execute("CREATE UNIQUE INDEX idx_revisions_page "
                     "ON revisions(page_id, version_number)")
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.execute("CREATE INDEX IF NOT EXISTS idx_revisions_page "
                     "ON revisions(page_id, version_number)")
        conn.commit()


def _add_column_if_missing(conn: sqlite3.Connection, table: str,
                           column: str, coltype: str) -> None:
    """Additive schema patch for databases created before this column
    existed. CREATE TABLE IF NOT EXISTS in SCHEMA only helps fresh installs;
    this covers upgrades without a full migration system."""
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
        conn.commit()


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
