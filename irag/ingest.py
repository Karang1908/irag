"""irag.ingest — turn changes into queued events, at FILE granularity.

Every tracked file is a subject with its own page; every folder is a
subject whose page summarizes its children. A change to a file queues an
event for that file's page and bumps the staleness of every ancestor
folder (half weight), so summaries cascade upward: file pages first, then
folder pages, then the root.

Ignoring: the built-in ignore list ([modules].ignore) matches path
segments; a `.iragignore` file at the project root adds gitignore-style
lines (names, prefixes, or fnmatch globs). irag's own artifacts never
generate events.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import sqlite3
from pathlib import Path

from . import db

# irag's own build artifacts never generate events (prevents the
# export -> commit -> staleness -> synthesize -> export feedback loop)
SELF_ARTIFACTS = {"CLAUDE.md", "AGENTS.md"}


def _file_hash(path: "Path") -> str | None:
    """SHA-1 of the WHOLE file, read in chunks (bounded memory). Hashing
    only a prefix would miss edits past that offset, so change detection
    reads the entire file."""
    import hashlib
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()

MANIFEST_FILES = {
    "package.json", "requirements.txt", "pyproject.toml",
    "Cargo.toml", "go.mod", "pom.xml",
}

# files that never get pages (nothing meaningful to summarize)
BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svgz", ".bmp",
    ".pdf", ".zip", ".gz", ".tar", ".tgz", ".7z", ".rar",
    ".mp3", ".mp4", ".wav", ".mov", ".avi", ".webm",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".o", ".a",
    ".db", ".sqlite", ".sqlite3", ".lock", ".bin", ".dat",
}


def _git(args: list[str], cwd: Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=True,
        )
    except FileNotFoundError:
        raise SystemExit("irag: git not found on PATH")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"irag: git {' '.join(args)} failed: "
                         f"{exc.stderr.strip()}")
    return result.stdout


def git_toplevel(cwd: Path | None = None) -> Path | None:
    """The enclosing git repository's top-level dir, or None."""
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                            cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def has_git_repo() -> bool:
    return git_toplevel() is not None


def git_rooted(repo: Path) -> bool:
    """True when the project root IS a git repository's top level — the
    only case where commit-based ingestion and git hooks apply. A project
    scoped to a subdirectory of some larger repo (e.g. a folder inside a
    versioned home dir) runs in snapshot mode instead: git's paths are
    toplevel-relative and would not match the project's subjects."""
    return git_toplevel(repo) == repo.resolve()


def repo_root() -> Path:
    """Project root resolution is DIRECTORY-WISE: the nearest ancestor
    (including cwd) that contains .irag wins; otherwise cwd. An enclosing
    git repository is deliberately NOT consulted — a giant ancestor repo
    (a versioned Desktop/home) must never silently become the project.
    Nested projects each keep their own .irag; whichever is nearest to
    where you run the command is the one you operate on."""
    cur = Path.cwd()
    for candidate in (cur, *cur.parents):
        if (candidate / ".irag").is_dir():
            return candidate
    return cur


# ---------------------------------------------------------------------
# ignoring
# ---------------------------------------------------------------------
def _ignore_patterns(cfg: dict, repo: Path | None) -> tuple[set, list]:
    """(segment_names, glob_patterns). Cached on cfg."""
    cache = cfg.get("_ignore_cache")
    if cache is not None:
        return cache
    segments = {str(x).lower() for x in cfg["modules"]["ignore"]}
    globs: list[str] = []
    if repo is not None:
        ig = repo / ".iragignore"
        if ig.is_file():
            try:
                for line in ig.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    line = line.rstrip("/")
                    if any(ch in line for ch in "*?[") or "/" in line:
                        globs.append(line)
                    else:
                        segments.add(line)
            except OSError:
                pass
    cfg["_ignore_cache"] = (segments, globs)
    return segments, globs


def is_ignored(path: str, cfg: dict, repo: Path | None = None) -> bool:
    """True if the path is excluded by config ignore or .iragignore."""
    if path in SELF_ARTIFACTS:
        return True
    parts = Path(path).parts
    if not parts:
        return True
    # hidden files/dirs are never tracked (.git, .env, .iragignore, ...)
    # — also keeps secrets out of synthesis prompts
    if any(p.startswith(".") for p in parts):
        return True
    segments, globs = _ignore_patterns(cfg, repo)
    # case-insensitive: macOS and Windows filesystems are, so a checked-in
    # `Node_Modules/` would otherwise be scanned - one LLM call per
    # dependency file. Over-ignoring a `Build/` costs nothing by comparison.
    if any(p.lower() in segments for p in parts):
        return True
    for pattern in globs:
        if (fnmatch.fnmatch(path, pattern)
                or fnmatch.fnmatch(path, pattern + "/*")
                or path == pattern or path.startswith(pattern + "/")):
            return True
    return False


# ---------------------------------------------------------------------
# subjects
# ---------------------------------------------------------------------
# Extensions are a hint, not evidence. A NUL byte in the first few KB is the
# same test `git` and `grep` use, and it costs one small read.
_SNIFF_BYTES = 4096


def looks_binary(file: Path) -> bool:
    """True if the file's leading bytes contain a NUL."""
    try:
        with open(file, "rb") as fh:
            return b"\x00" in fh.read(_SNIFF_BYTES)
    except OSError:
        return False        # unreadable/vanished: let the caller decide


def file_subject(path: str, cfg: dict, repo: Path | None = None) -> str | None:
    """A tracked file's subject is its own path. None if ignored/binary."""
    if is_ignored(path, cfg, repo):
        return None
    if Path(path).suffix.lower() in BINARY_EXTS:
        return None
    # Content, not just the name. A .py holding a pickle, a misnamed artifact,
    # or a UTF-16 source all previously earned a page - and their raw bytes
    # went into the synthesis prompt verbatim, costing an LLM call and leaving
    # a junk page that agents then read as if it were the file's summary.
    if repo is not None and looks_binary(repo / path):
        return None
    return str(Path(path).as_posix())


def ancestors(path: str) -> list[str]:
    """Folder subjects above a file/folder path, nearest first, ending '.'"""
    out = []
    cur = Path(path).parent
    while str(cur) not in ("", "."):
        out.append(cur.as_posix())
        cur = cur.parent
    out.append(".")
    return out


def _queue_file_event(conn, cfg, subject: str, ref: str, payload: dict,
                      event_type: str = "commit") -> bool:
    """Insert an event for a file page (idempotent on ref+subject) and bump
    the file page and its ancestor folders. Returns True if inserted."""
    exists = conn.execute(
        "SELECT 1 FROM events WHERE source_ref=? AND subject_id=?",
        (ref, subject)).fetchone()
    if exists:
        return False
    db.get_or_create_page(conn, subject, subject_type="file",
                          page_type="file")
    conn.execute(
        "INSERT INTO events(event_type, source_ref, subject_id, payload, "
        "session_key) VALUES(?, ?, ?, ?, ?)",
        (event_type, ref, subject, json.dumps(payload), db.active_key()))
    bump = int(cfg["staleness"]["commit"])
    if Path(subject).name in MANIFEST_FILES:
        bump += int(cfg["staleness"]["dependency"])
    conn.execute(
        "UPDATE pages SET staleness_score = staleness_score + ? "
        "WHERE subject_type='file' AND subject_id=?", (bump, subject))
    for folder in ancestors(subject):
        db.get_or_create_page(conn, folder, subject_type="folder",
                              page_type="folder")
        conn.execute(
            "UPDATE pages SET staleness_score = staleness_score + ? "
            "WHERE subject_type='folder' AND subject_id=?",
            (max(bump // 2, 1), folder))
    return True


def ingest_commit(conn: sqlite3.Connection, cfg: dict, ref: str,
                  repo: Path | None = None) -> int:
    """Ingest a single commit: one queued event per touched file."""
    out = _git(["show", "--name-only", "--pretty=format:%H%n%ct%n%s", ref],
               cwd=repo)
    lines = out.splitlines()
    if len(lines) < 3:
        return 0
    commit_hash, message = lines[0], lines[2]
    files = [ln.strip() for ln in lines[3:] if ln.strip()]
    inserted = 0
    for f in files:
        subject = file_subject(f, cfg, repo)
        if subject is None:
            continue
        if repo is not None and _content_already_known(conn, repo, subject):
            continue   # snapshot already captured this exact content
        if _queue_file_event(conn, cfg, subject, commit_hash,
                             {"files": [subject], "message": message}):
            inserted += 1
    conn.commit()
    return inserted


def _content_already_known(conn, repo: Path, subject: str) -> bool:
    """True if the file's current bytes match the stored fingerprint AND
    the page has a synthesized revision — committing already-summarized
    content should not trigger a rewrite."""
    row = conn.execute("SELECT hash FROM tree_state WHERE path=?",
                       (subject,)).fetchone()
    if not row:
        return False
    f = repo / subject
    if not f.is_file():
        return False
    digest = _file_hash(f)
    if digest is None or digest != row["hash"]:
        return False
    page = conn.execute(
        "SELECT current_revision_id FROM pages "
        "WHERE subject_type='file' AND subject_id=?", (subject,)).fetchone()
    return bool(page and page["current_revision_id"])


def _no_longer_source(sid: str, subject_type: str, cfg: dict,
                      repo: Path) -> bool:
    """True if this subject has stopped being trackable source.

    Two ways that happens. It became ignored - a new .iragignore entry. Or the
    file is still on disk but is no longer *source*: a build artifact, a
    pickle or compiled output written over a source path. The second is
    invisible to the snapshot diff, because `file_subject` now returns None
    for it, so no event fires, no staleness accrues, and the page sits there
    asserting functions that no longer exist in a file that is no longer code.

    A file that was DELETED is deliberately excluded here - deletion has its
    own handling, which writes a tombstone page rather than forgetting it.
    """
    if is_ignored(sid, cfg, repo):
        return True
    if subject_type != "file":
        return False
    path = repo / sid
    return path.is_file() and looks_binary(path)


def purge_ignored(conn: sqlite3.Connection, cfg: dict, repo: Path) -> int:
    """Remove pages (and their revisions/contradictions/links/symbols) for
    subjects that are no longer trackable source — newly ignored, or turned
    binary under a source path. Queued events for them are skipped. Session
    history text is untouched. Returns pages removed."""
    removed = 0
    rows = conn.execute(
        "SELECT page_id, subject_id, subject_type FROM pages "
        "WHERE subject_type IN ('file','folder')").fetchall()
    for row in rows:
        sid = row["subject_id"]
        if sid == ".":
            continue
        if not _no_longer_source(sid, row["subject_type"], cfg, repo):
            continue
        pid = row["page_id"]
        conn.execute("DELETE FROM contradictions WHERE page_id=?", (pid,))
        conn.execute("DELETE FROM links WHERE source_page_id=? "
                     "OR target_page_id=?", (pid, pid))
        conn.execute("UPDATE pages SET current_revision_id=NULL "
                     "WHERE page_id=?", (pid,))
        conn.execute("DELETE FROM revisions WHERE page_id=?", (pid,))
        conn.execute("DELETE FROM pages WHERE page_id=?", (pid,))
        conn.execute("UPDATE events SET status='skipped' "
                     "WHERE subject_id=? AND status='queued'", (sid,))
        conn.execute("DELETE FROM symbols WHERE subject_id=?", (sid,))
        conn.execute("DELETE FROM deps WHERE source_subject=? "
                     "OR target_subject=?", (sid, sid))
        conn.execute("DELETE FROM tree_state WHERE path=?", (sid,))
        removed += 1
    if removed:
        conn.commit()
    return removed


def sync(conn: sqlite3.Connection, cfg: dict,
         repo: Path | None = None) -> int:
    """Catch up on changes: git commits or working-tree snapshots per
    [ingest].mode (auto|git|snapshot). Git mode requires the project
    root to BE a git toplevel — a project scoped to a subdirectory of a
    larger repo always snapshots (see git_rooted)."""
    if repo is None:
        repo = repo_root()
    mode = str(cfg.get("ingest", {}).get("mode", "auto"))
    if mode == "git" and not git_rooted(repo):
        raise SystemExit(
            "irag: [ingest].mode = \"git\" but the project root is not a "
            "git repository's top level — use mode = \"auto\" or "
            "\"snapshot\", or 'git init' the project itself")
    use_git = git_rooted(repo) if mode == "auto" else (mode == "git")
    purge_ignored(conn, cfg, repo)
    if not use_git:
        return snapshot(conn, cfg, repo)
    total = 0
    head = _git(["rev-parse", "HEAD"], cwd=repo).strip() \
        if _has_commits(repo) else None
    if head is not None:
        last = db.get_meta(conn, "last_synced")
        if last != head:
            range_spec = f"{last}..HEAD" if last else "HEAD"
            try:
                out = _git(["rev-list", "--reverse", range_spec], cwd=repo)
            except SystemExit:
                out = _git(["rev-list", "--reverse", "HEAD"], cwd=repo)
            for commit in out.split():
                total += ingest_commit(conn, cfg, commit, repo)
            db.set_meta(conn, "last_synced", head)
    # HYBRID: also fingerprint the working tree, so UNCOMMITTED edits
    # (how coding agents usually leave files) are detected too. A file
    # caught by both paths merges into one synthesis, since a sweep
    # consumes all queued events for a subject at once.
    if mode != "git":
        total += snapshot(conn, cfg, repo, baseline_if_empty=True)
    return total


def _has_commits(repo: Path | None = None) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"],
        cwd=repo, capture_output=True, text=True,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------
# Snapshot mode: content-hash the tree, diff, emit events — no git.
# The page revision chain (v1, v2, ...) is the memory's own versioning;
# snapshots are just an alternative change trigger.
# ---------------------------------------------------------------------
SNAP_MAX_FILE = 1_000_000
SNAP_MAX_FILES = 20_000


def _tree_hashes(repo: Path, cfg: dict) -> dict[str, str]:
    hashes: dict[str, str] = {}
    count = 0
    for f in sorted(repo.rglob("*")):
        if count >= SNAP_MAX_FILES:
            print(f"irag: note — snapshot stopped at {SNAP_MAX_FILES} files; "
                  "add a .iragignore to exclude generated/vendored trees")
            break
        if not f.is_file():
            continue
        rel = f.relative_to(repo).as_posix()
        if is_ignored(rel, cfg, repo):
            continue
        digest = _file_hash(f)
        if digest is None:
            continue
        hashes[rel] = digest
        count += 1
    return hashes


def snapshot(conn: sqlite3.Connection, cfg: dict, repo: Path,
             baseline_if_empty: bool = False) -> int:
    """Diff the working tree against stored fingerprints; one event per
    changed/deleted file. Returns events inserted.

    baseline_if_empty: when no fingerprints exist yet (first run in a git
    repo whose history was already ingested), just record the baseline
    without emitting events."""
    current = _tree_hashes(repo, cfg)
    previous = {row["path"]: row["hash"] for row in
                conn.execute("SELECT path, hash FROM tree_state").fetchall()}
    if baseline_if_empty and not previous:
        conn.execute("DELETE FROM tree_state")
        conn.executemany("INSERT INTO tree_state(path, hash) VALUES(?,?)",
                         list(current.items()))
        conn.commit()
        return 0
    changed = [p for p, h in current.items() if previous.get(p) != h]
    deleted = [p for p in previous if p not in current]
    if not changed and not deleted:
        return 0

    seq = int(db.get_meta(conn, "snap_seq", "0")) + 1
    db.set_meta(conn, "snap_seq", seq)
    ref = f"snap:{seq}"

    inserted = 0
    for p in changed:
        subject = file_subject(p, cfg, repo)
        if subject and _queue_file_event(
                conn, cfg, subject, ref,
                {"files": [subject], "message": "working-tree change"},
                event_type="snapshot"):
            inserted += 1
    for p in deleted:
        subject = file_subject(p, cfg, repo)
        if subject and _queue_file_event(
                conn, cfg, subject, ref,
                {"files": [], "deleted": [subject],
                 "message": "file deleted from working tree"},
                event_type="snapshot"):
            inserted += 1

    conn.execute("DELETE FROM tree_state")
    conn.executemany("INSERT INTO tree_state(path, hash) VALUES(?,?)",
                     list(current.items()))
    conn.commit()
    return inserted
