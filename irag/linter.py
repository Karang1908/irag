"""irag.linter — compare wiki claims against repository ground truth.

The static tier is deterministic and free: it extracts checkable claims
from page bodies (paths, version pins, callable identifiers) and verifies
them against the filesystem and dependency manifests. Failures become rows
in the ``contradictions`` table — hallucination as a queryable row.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

PATH_RE = re.compile(
    r"`([\w./-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|rb|md|json|toml|ya?ml|sql|sh))`"
)
VERSION_AT_RE = re.compile(r"([A-Za-z0-9_.-]+)@(\d+\.\d+[\w.\-]*)")
VERSION_WORD_RE = re.compile(r"`?([A-Za-z0-9_.-]+)`?\s+version\s+(\d+\.\d+[\w.\-]*)")
SYMBOL_RE = re.compile(r"`(\w+)\(\)`")


def _read_manifest_versions(repo: Path) -> dict[str, str]:
    """Collect declared dependency versions from package.json and requirements.txt."""
    versions: dict[str, str] = {}
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies"):
                for name, spec in (data.get(section) or {}).items():
                    versions[name.lower()] = re.sub(r"^[\^~>=<]+", "", str(spec))
        except (json.JSONDecodeError, OSError):
            pass
    req = repo / "requirements.txt"
    if req.is_file():
        try:
            for line in req.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "==" in line and not line.startswith("#"):
                    name, ver = line.split("==", 1)
                    versions[name.strip().lower()] = ver.strip()
        except OSError:
            pass
    return versions


def _existing_open_claim(conn, page_id: int, claim: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM contradictions "
        "WHERE page_id=? AND claim=? AND resolved_at IS NULL",
        (page_id, claim),
    ).fetchone() is not None


def _insert(conn, page_id: int, revision_id: int, claim: str, truth: str,
            ctype: str, severity: str, check: str) -> bool:
    if _existing_open_claim(conn, page_id, claim):
        return False
    conn.execute(
        "INSERT INTO contradictions(page_id, revision_id, claim, truth, ctype, "
        "severity, detected_by) VALUES(?,?,?,?,?,?,?)",
        (page_id, revision_id, claim, truth, ctype, severity, f"static:{check}"),
    )
    return True


def lint(conn: sqlite3.Connection, cfg: dict, repo: Path,
         subject_id: str | None = None) -> int:
    """Run static checks on current page bodies. Returns contradictions added."""
    where = "WHERE p.current_revision_id IS NOT NULL"
    params: tuple = ()
    if subject_id:
        where += " AND p.subject_id=?"
        params = (subject_id,)
    pages = conn.execute(
        f"""SELECT p.page_id, p.subject_id, p.current_revision_id rev_id,
                   r.body_markdown body
            FROM pages p JOIN revisions r ON r.revision_id=p.current_revision_id
            {where}""",
        params,
    ).fetchall()

    manifest = _read_manifest_versions(repo)
    added = 0

    for page in pages:
        body = page["body"]
        failing: set[str] = set()   # claims that (still) fail this run

        # (a) backticked path-like tokens must exist on disk
        for match in PATH_RE.finditer(body):
            rel = match.group(1)
            if not (repo / rel).exists():
                failing.add(f"references path `{rel}`")
                if _insert(conn, page["page_id"], page["rev_id"],
                           claim=f"references path `{rel}`",
                           truth=f"{rel} does not exist in the repository",
                           ctype="missing_path", severity="high", check="missing_path"):
                    added += 1

        # (b) version claims vs manifests
        version_claims = list(VERSION_AT_RE.finditer(body)) + \
            list(VERSION_WORD_RE.finditer(body))
        for match in version_claims:
            name, ver = match.group(1), match.group(2)
            actual = manifest.get(name.lower())
            if actual and actual != ver:
                failing.add(f"{name} version {ver}")
                if _insert(conn, page["page_id"], page["rev_id"],
                           claim=f"{name} version {ver}",
                           truth=f"manifest declares {name} {actual}",
                           ctype="version_mismatch", severity="medium",
                           check="version_mismatch"):
                    added += 1

        # (c) backticked identifiers(): exact check against the symbols
        # table when the structural scan has data; grep fallback otherwise
        have_symbols = conn.execute(
            "SELECT 1 FROM symbols LIMIT 1").fetchone() is not None
        if have_symbols:
            for match in SYMBOL_RE.finditer(body):
                sym = match.group(1)
                defined = conn.execute(
                    "SELECT 1 FROM symbols WHERE name=? OR name LIKE ? LIMIT 1",
                    (sym, f"%.{sym}")).fetchone()
                if not defined:
                    failing.add(f"references symbol `{sym}()`")
                    if _insert(conn, page["page_id"], page["rev_id"],
                               claim=f"references symbol `{sym}()`",
                               truth=f"no definition of {sym} anywhere in the "
                                     f"scanned codebase",
                               ctype="missing_symbol", severity="low",
                               check="missing_symbol"):
                        added += 1
        subj_path = repo / page["subject_id"]
        mod_dir = (subj_path if subj_path.is_dir()
                   else subj_path.parent) if page["subject_id"] != "." else repo
        if not have_symbols and mod_dir.is_dir():
            source = ""
            for f in mod_dir.rglob("*"):
                if f.is_file() and f.suffix in {".py", ".ts", ".tsx", ".js",
                                                ".jsx", ".go", ".rs", ".java",
                                                ".rb"}:
                    try:
                        source += f.read_text(
                            encoding="utf-8", errors="replace")[:200_000]
                    except OSError:
                        continue
            for match in SYMBOL_RE.finditer(body):
                sym = match.group(1)
                pattern = re.compile(
                    rf"\b(?:def|function|const|fn|func)\s+{re.escape(sym)}\b"
                )
                if source and not pattern.search(source):
                    failing.add(f"references symbol `{sym}()`")
                    if _insert(conn, page["page_id"], page["rev_id"],
                               claim=f"references symbol `{sym}()`",
                               truth=f"no definition of {sym} found in "
                                     f"{page['subject_id']}",
                               ctype="missing_symbol", severity="low",
                               check="missing_symbol"):
                        added += 1

        # auto-resolve static contradictions that no longer reproduce
        # (page was revised, file restored, version corrected, ...)
        open_static = conn.execute(
            "SELECT contradiction_id, claim FROM contradictions "
            "WHERE page_id=? AND resolved_at IS NULL "
            "AND detected_by LIKE 'static:%'",
            (page["page_id"],),
        ).fetchall()
        for row in open_static:
            if row["claim"] not in failing:
                conn.execute(
                    "UPDATE contradictions SET resolved_at=datetime('now'), "
                    "resolution_notes='auto-resolved: claim no longer fails' "
                    "WHERE contradiction_id=?",
                    (row["contradiction_id"],),
                )
    conn.commit()
    return added


def resolve(conn: sqlite3.Connection, contradiction_id: int,
            notes: str | None = None) -> None:
    """Mark a contradiction resolved."""
    row = conn.execute(
        "SELECT 1 FROM contradictions WHERE contradiction_id=?",
        (contradiction_id,),
    ).fetchone()
    if not row:
        raise SystemExit(f"irag: no contradiction with id {contradiction_id}")
    conn.execute(
        "UPDATE contradictions SET resolved_at=datetime('now'), "
        "resolution_notes=? WHERE contradiction_id=?",
        (notes, contradiction_id),
    )
    conn.commit()


def lint_llm(conn: sqlite3.Connection, cfg: dict, repo: Path,
             subject_id: str | None = None) -> int:
    """Optional LLM tier: ask the model to flag claims that look inconsistent
    with the file listing. Inserts ``llm_flagged`` contradictions."""
    from . import synthesis

    where = "WHERE p.current_revision_id IS NOT NULL"
    params: tuple = ()
    if subject_id:
        where += " AND p.subject_id=?"
        params = (subject_id,)
    pages = conn.execute(
        f"""SELECT p.page_id, p.subject_id, p.current_revision_id rev_id,
                   r.body_markdown body
            FROM pages p JOIN revisions r ON r.revision_id=p.current_revision_id
            {where}""",
        params,
    ).fetchall()

    added = 0
    for page in pages:
        subj_path = repo / page["subject_id"]
        mod_dir = (subj_path if subj_path.is_dir()
                   else subj_path.parent) if page["subject_id"] != "." else repo
        listing = "\n".join(
            str(f.relative_to(repo)) for f in sorted(mod_dir.rglob("*"))
            if f.is_file()
        )[:4000] if mod_dir.is_dir() else "(no directory)"
        prompt = (
            "You are auditing a context page against a file listing. "
            "List each factual claim in the page that the listing contradicts, "
            "one per line, as: CLAIM | WHY. If none, output exactly: NONE\n\n"
            f"FILE LISTING for {page['subject_id']}:\n{listing}\n\n"
            f"PAGE:\n{page['body']}"
        )
        out = synthesis.run_llm(cfg, prompt)
        if out.strip().upper() == "NONE":
            continue
        for line in out.splitlines():
            if "|" not in line:
                continue
            claim, why = (s.strip() for s in line.split("|", 1))
            if claim and _insert(conn, page["page_id"], page["rev_id"],
                                 claim=claim, truth=why, ctype="llm_flagged",
                                 severity="medium", check="llm"):
                added += 1
    conn.commit()
    return added
