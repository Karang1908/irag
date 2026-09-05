"""irag.retrieval — relevance scoring and tiered, budget-gated serving.

Zero LLM tokens are spent here: relevance is computed with path matching,
FTS5 ranking, link expansion, recency, staleness, and contradiction
penalties — plain SQL and arithmetic.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db

W_MODULE_MATCH = 50
W_PARENT_MATCH = 25
W_FTS_MAX = 30
W_LINK_HOP = 15
W_DEP_NEIGHBOR = 20
W_RECENT = 10
P_STALE = 20
P_CONTRADICTED = 40
RECENT_DAYS = 7


def _fts_page_scores(conn, query: str) -> dict[int, int]:
    """Map page_id -> FTS score (best-ranked revision hit, capped)."""
    q = db.fts_sanitize(query or "")
    if not q:
        return {}
    try:
        rows = conn.execute(
            """SELECT r.page_id, MIN(rank) best
               FROM revisions_fts f JOIN revisions r ON r.revision_id=f.rowid
               WHERE revisions_fts MATCH ?
               GROUP BY r.page_id ORDER BY best LIMIT 20""",
            (q,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    scores: dict[int, int] = {}
    for i, row in enumerate(rows):
        # best-ranked pages get the full weight; decay by position
        scores[row["page_id"]] = max(W_FTS_MAX - i * 3, 5)
    return scores


def score(conn: sqlite3.Connection, cfg: dict,
          open_files: list[str] | None = None,
          query: str | None = None) -> list[dict]:
    """Score every page. Returns dicts sorted by score desc."""
    open_files = open_files or []
    threshold = int(cfg["staleness"]["threshold"])
    # A page whose file was deleted is history, not live memory. Serving
    # it meant search and context handed agents summaries of files that no
    # longer exist — and a rename is a delete plus an add, so every rename
    # added one permanently. `asof`/`why` still see it; only serving stops.
    pages = conn.execute(
        "SELECT * FROM pages WHERE COALESCE(deleted_at,'') = ''").fetchall()
    fts = _fts_page_scores(conn, query or "")

    # subjects derivable from open files: the file itself + its folders
    from .ingest import ancestors
    from pathlib import PurePosixPath
    open_modules: set[str] = set()
    open_parents: set[str] = set()
    for f in open_files:
        # Callers on Windows naturally pass backslashes; database subjects are
        # canonical POSIX-relative paths on every platform.
        norm = PurePosixPath(str(f).replace("\\", "/")).as_posix()
        # Remove explicit relative prefixes without stripping the meaningful
        # leading dot from paths such as `.github/workflows/ci.yml`.
        while norm.startswith("./"):
            norm = norm[2:]
        open_modules.add(norm)
        open_parents.update(ancestors(norm))

    # dependency neighbors of open-file modules (structural relevance)
    dep_neighbors: set[str] = set()
    if open_modules:
        marks = ",".join("?" * len(open_modules))
        for row in conn.execute(
                f"""SELECT target_subject x FROM deps
                    WHERE source_subject IN ({marks})
                    UNION
                    SELECT source_subject x FROM deps
                    WHERE target_subject IN ({marks})""",
                list(open_modules) * 2).fetchall():
            dep_neighbors.add(row["x"])
        dep_neighbors -= open_modules

    now = datetime.now(timezone.utc)
    results = []
    for page in pages:
        s = 0
        reasons = []
        sid = page["subject_id"]
        if sid in open_modules:
            s += W_MODULE_MATCH
            reasons.append("open file")
        elif sid in open_parents:
            s += W_PARENT_MATCH
            reasons.append("containing folder")
        if sid in dep_neighbors:
            s += W_DEP_NEIGHBOR
            reasons.append("dependency neighbor")
        if page["page_type"] in ("decisions", "lessons"):
            s += 25   # project logs always clear min_score
            reasons.append("project log")
        if sid == "." and page["page_type"] == "folder":
            s += 40   # the root overview is always worth serving
            reasons.append("project overview")
        if page["page_id"] in fts:
            s += fts[page["page_id"]]
            reasons.append("text match")
        try:
            updated = datetime.fromisoformat(page["last_updated_at"]).replace(
                tzinfo=timezone.utc)
            if now - updated < timedelta(days=RECENT_DAYS):
                s += W_RECENT
        except (TypeError, ValueError):
            pass
        # `relevance` is how much this page matters to the query; `score`
        # additionally carries the health penalties, and orders the results.
        # They are kept apart on purpose: a contradiction should DEMOTE a
        # page, never erase it. Penalising below min_score used to drop the
        # page out of the briefing entirely - so flagging a page deleted the
        # agent's memory of that file and left it to re-explore from scratch,
        # which is the exact cost irag exists to remove.
        relevance = s
        stale = page["staleness_score"] > threshold
        if stale:
            s -= P_STALE
        open_c = db.open_contradiction_count(conn, page["page_id"])
        if open_c:
            s -= P_CONTRADICTED
        results.append({
            "page": page, "score": s, "relevance": relevance,
            "reasons": reasons, "stale": stale, "contradictions": open_c,
        })

    # one link hop from the current top seeds
    results.sort(key=lambda r: r["score"], reverse=True)
    seeds = [r["page"]["page_id"] for r in results[:3] if r["score"] > 0]
    if seeds:
        linked = conn.execute(
            f"""SELECT target_page_id pid FROM links
                WHERE source_page_id IN ({','.join('?' * len(seeds))})
                UNION
                SELECT source_page_id pid FROM links
                WHERE target_page_id IN ({','.join('?' * len(seeds))})""",
            seeds + seeds,
        ).fetchall()
        linked_ids = {row["pid"] for row in linked} - set(seeds)
        for r in results:
            if r["page"]["page_id"] in linked_ids:
                r["score"] += W_LINK_HOP
                r["reasons"].append("linked")
        results.sort(key=lambda r: r["score"], reverse=True)
    return results


def briefing(conn: sqlite3.Connection, cfg: dict, root: Path,
             open_files: list[str] | None = None,
             query: str | None = None,
             budget_tokens: int | None = None) -> tuple[str, dict]:
    """Build the complete agent briefing shared by CLI and dashboard.

    Sync/scan remain the caller's responsibility so the dashboard can hold
    its cross-process lock for the whole read.  Centralizing the recap and
    dirty-tree notice keeps the web preview identical to ``irag context``.
    """
    md, machine = serve(conn, cfg, open_files=open_files, query=query,
                        budget_tokens=budget_tokens)
    from . import sessions, stats
    recap = sessions.recap_block(conn, n=2)
    if recap:
        md = md.replace("# Project Context (irag)",
                        f"# Project Context (irag)\n\n{recap}", 1)
        machine["recap"] = recap
    dirty = stats.dirty_count(root)
    if dirty:
        note = (f"note: {dirty} uncommitted change(s) in the working tree — "
                "the structural map reflects current files; synthesized page "
                "summaries may lag until the next update")
        md = md.replace("# Project Context (irag)",
                        f"# Project Context (irag)\n\n_{note}_", 1)
        machine["dirty_files"] = dirty
    return md, machine


def serve(conn: sqlite3.Connection, cfg: dict,
          open_files: list[str] | None = None,
          query: str | None = None,
          budget_tokens: int | None = None) -> tuple[str, dict]:
    """Build the tiered context markdown. Returns (markdown, machine_dict)."""
    ranked = score(conn, cfg, open_files, query)
    min_score = int(cfg["retrieval"]["min_score"])
    # distinguish "not provided" (None -> config default) from an explicit 0
    tok = budget_tokens if budget_tokens is not None \
        else cfg["retrieval"]["token_budget"]
    budget_chars = int(tok) * 4
    full_max = int(cfg["retrieval"]["full_max"])
    DIGEST_COST = 260   # ~ chars of one digest summary line, charged to budget
    INDEX_MAX = 120     # cap the cheap one-line tail so output stays bounded

    lines = ["# Project Context (irag)", ""]
    machine: dict = {"full": [], "digest": [], "index": [], "warnings": []}

    # NOT gated on min_score: the health penalties above are what push a
    # flagged page's score down, so gating here meant the page most in need
    # of "verify this before trusting it" was the one whose warning got
    # suppressed. Capped instead, so the section stays bounded.
    WARN_MAX = 12
    warnings = []
    flagged = [r for r in ranked if r["contradictions"] or r["stale"]]
    for r in flagged[:WARN_MAX]:
        if r["contradictions"]:
            warnings.append(
                f"⚠ `{r['page']['subject_id']}` has {r['contradictions']} open "
                f"contradiction(s) — verify claims against the code "
                f"(`irag contradictions`)."
            )
        elif r["stale"]:
            warnings.append(
                f"⚠ `{r['page']['subject_id']}` is stale "
                f"(score {r['page']['staleness_score']}) — may lag the code."
            )
    if len(flagged) > WARN_MAX:
        warnings.append(f"⚠ …and {len(flagged) - WARN_MAX} more flagged "
                        "page(s) — `irag contradictions` / `irag stale`.")
    if warnings:
        lines.append("## Warnings")
        lines.extend(warnings)
        lines.append("")
        machine["warnings"] = warnings

    eligible = [r for r in ranked
                if r.get("relevance", r["score"]) >= min_score]
    full: list[tuple[dict, str]] = []
    digest: list[dict] = []
    index: list[dict] = []
    used = 0
    for r in eligible:
        body = db.current_body(conn, r["page"]["page_id"]) or ""
        if len(full) < full_max and body and used + len(body) <= budget_chars:
            full.append((r, body))
            used += len(body)
        elif body and used + DIGEST_COST <= budget_chars:
            # digest entries also cost tokens — charge them so the whole
            # briefing honors budget_tokens; overflow falls through to the
            # cheap one-line index instead of ballooning unbounded
            digest.append(r)
            used += DIGEST_COST
        else:
            index.append(r)

    if full:
        lines.append("## FULL")
        for r, body in full:
            page = r["page"]
            version = conn.execute(
                "SELECT version_number v FROM revisions WHERE revision_id=?",
                (page["current_revision_id"],),
            ).fetchone()
            vno = version["v"] if version else "?"
            banner = ""
            if r["contradictions"]:
                banner = " ⚠ CONTRADICTED — verify against code"
            lines.append(
                f"### {page['title']} (v{vno}, updated "
                f"{page['last_updated_at']}){banner}"
            )
            lines.append(body)
            from . import structure
            facts = structure.facts_block(conn, page["subject_id"])
            if facts:
                lines.append("")
                lines.append("**map:**")
                lines.append(facts)
            lines.append("")
            machine["full"].append({
                "subject": page["subject_id"], "version": vno,
                "score": r["score"], "body": body,
                "contradictions": r["contradictions"],
            })

    if digest:
        lines.append("## DIGEST")
        for r in digest:
            page = r["page"]
            rev = conn.execute(
                "SELECT change_summary, body_markdown FROM revisions "
                "WHERE revision_id=?", (page["current_revision_id"],),
            ).fetchone()
            summary = (rev["change_summary"] or rev["body_markdown"][:200]) \
                if rev else "(no content)"
            lines.append(f"- **{page['title']}** (score {r['score']}): {summary}")
            machine["digest"].append(
                {"subject": page["subject_id"], "summary": summary,
                 "score": r["score"]})
        lines.append("")

    remaining = [r for r in ranked
                 if r.get("relevance", r["score"]) < min_score] + index
    if remaining:
        lines.append("## INDEX")
        for r in remaining[:INDEX_MAX]:
            page = r["page"]
            lines.append(
                f"- {page['title']} (score {r['score']}, "
                f"staleness {page['staleness_score']})"
            )
            machine["index"].append(
                {"subject": page["subject_id"], "score": r["score"],
                 "staleness": page["staleness_score"]})
        if len(remaining) > INDEX_MAX:
            lines.append(f"- …and {len(remaining) - INDEX_MAX} more "
                         "(raise [retrieval].token_budget or query to narrow)")

    return "\n".join(lines), machine


def search(conn: sqlite3.Connection, query: str) -> list[dict]:
    """Plain FTS search over revision bodies (current revisions surfaced first)."""
    q = db.fts_sanitize(query)
    if not q:
        return []
    try:
        rows = conn.execute(
            """SELECT p.subject_id, p.title, r.version_number, r.revision_id,
                      p.current_revision_id,
                      -- no highlight markers: this excerpt is read by
                      -- agents, and wrapping matches in brackets rewrote
                      -- real text ('src/store.ts' -> 'src/[store].ts')
                      snippet(revisions_fts, 0, '', '', '…', 12) snip
               FROM revisions_fts f
               JOIN revisions r ON r.revision_id = f.rowid
               JOIN pages p ON p.page_id = r.page_id
               WHERE revisions_fts MATCH ?
                 AND COALESCE(p.deleted_at,'') = ''
               ORDER BY (r.revision_id = p.current_revision_id) DESC, rank
               LIMIT 15""",
            (q,),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        # the intended catch is a malformed FTS query (users type quotes,
        # parens and bare operators). Anything else - a locked database, a
        # missing table - must not masquerade as "no matches found".
        msg = str(exc).lower()
        if "fts5" not in msg and "malformed" not in msg and "syntax" not in msg:
            raise
        return []
    return [
        {"subject": row["subject_id"], "title": row["title"],
         "version": row["version_number"],
         "current": row["revision_id"] == row["current_revision_id"],
         "snippet": row["snip"]}
        for row in rows
    ]
