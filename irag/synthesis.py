"""irag.synthesis — hierarchical LLM synthesis: file pages, then folders.

Two prompt types. FILE pages summarize one file from its full (capped)
content plus structural facts. FOLDER pages roll up their direct
children's summaries — synthesized bottom-up after files, so the root
page is a summary of summaries. The model only writes prose; queue
state, versioning, and staleness live in SQL.
"""
from __future__ import annotations

import json
import shlex
import sqlite3
import subprocess
from pathlib import Path

from . import db

FILE_INSTRUCTION = """You maintain the context page for ONE source file. \
Your output is read by AI coding agents to work on this file without \
opening it first, and is mechanically fact-checked against the codebase. \
Rewrite the page now.

OUTPUT FORMAT — exactly this structure, nothing else:

# <file path>
One sentence: what this file is for.

## Public interface
- `symbol()` — what it does, takes, returns, in one line (only symbols
  defined IN THIS FILE, from STRUCTURAL FACTS or the content below)

## Behavior & gotchas
- Invariants, side effects, error handling, ordering requirements —
  only what the content evidences.

## Connections & blast radius
- How this file works with its neighbors: what it imports and why, who
  imports it and for what. If this file were deleted or broken, state
  concretely what stops working — derive this ONLY from the BLAST RADIUS
  list provided below (those are the real, parsed dependents). If the
  list is empty, say the file is a leaf: nothing else breaks directly.

## Recent changes
- Newest first, one factual line each, max 5.

HARD RULES:
1. Backtick every path and symbol; callables as `name()`. Unbackticked
   claims cannot be verified.
2. Mention ONLY what appears in STRUCTURAL FACTS or the file content.
   Never carry over anything from the current page that no longer exists.
3. Versions only as they literally appear in a manifest.
4. No filler. Every line must tell an agent something actionable.
5. Uncertain? Omit it. 80-250 words. Plain markdown, no code fences
   around the page, no preamble — output starts with the # heading."""

FOLDER_INSTRUCTION = """You maintain the overview page for ONE folder of \
a code repository, summarizing its children. AI coding agents read it to \
decide which files to work with. Rewrite it now from the child summaries \
below.

OUTPUT FORMAT — exactly this structure, nothing else:

# <folder path>
One or two sentences: this folder's responsibility in the project.

## Contents
- `child` — its role in one clause (one bullet per direct child listed
  below; subfolders first, then files)

## How it fits together
- 2-5 bullets on how the children relate: who calls whom, shared
  patterns, entry points. Only what the summaries below evidence.

## Recent changes
- Newest first, one line each, max 5.

HARD RULES:
1. Backtick every path and symbol.
2. Mention ONLY children listed below. Never invent structure.
3. No filler. 100-300 words. Plain markdown, no code fences, no
   preamble — output starts with the # heading."""

FILE_CONTENT_CAP = 8000


def pending_file_pages(conn, cfg) -> list[sqlite3.Row]:
    threshold = int(cfg["staleness"]["threshold"])
    return conn.execute(
        """SELECT p.* FROM pages p
           WHERE p.pinned = 0 AND p.page_type = 'file'
             AND ((p.current_revision_id IS NULL AND EXISTS (
                     SELECT 1 FROM events e
                     WHERE e.subject_id = p.subject_id
                       AND e.status = 'queued'))
                  OR p.staleness_score >= ?)
           ORDER BY p.staleness_score DESC""",
        (threshold,),
    ).fetchall()


def pending_folder_pages(conn, cfg) -> list[sqlite3.Row]:
    """Folders due: never synthesized (with at least one synthesized child)
    or stale. Deepest first so parents see fresh children."""
    threshold = int(cfg["staleness"]["threshold"])
    rows = conn.execute(
        """SELECT p.* FROM pages p
           WHERE p.pinned = 0 AND p.page_type = 'folder'
             AND (p.current_revision_id IS NULL
                  OR p.staleness_score >= ?)""",
        (threshold,),
    ).fetchall()
    eligible = []
    for page in rows:
        if not _children(conn, page["subject_id"]):
            continue
        # never roll up a folder while a direct child folder still lacks
        # a revision — the fixpoint loop will pick this one up after the
        # child is written, so the rollup always sees complete children
        if _unready_child_folder(conn, page["subject_id"]):
            continue
        eligible.append(page)
    eligible.sort(key=lambda r: (0 if r["subject_id"] == "." else
                                 -r["subject_id"].count("/") - 1))
    return eligible


def _unready_child_folder(conn, folder: str) -> bool:
    if folder == ".":
        cond = "subject_id NOT LIKE '%/%' AND subject_id != '.'"
        params: tuple = ()
    else:
        cond = "subject_id LIKE ? AND subject_id NOT LIKE ?"
        params = (folder + "/%", folder + "/%/%")
    return conn.execute(
        f"""SELECT 1 FROM pages WHERE {cond}
            AND page_type='folder' AND current_revision_id IS NULL
            AND pinned=0 LIMIT 1""", params).fetchone() is not None


def _children(conn, folder: str) -> list[sqlite3.Row]:
    """Direct child pages (files and folders) that have a current revision."""
    if folder == ".":
        cond = ("p.subject_id NOT LIKE '%/%' AND p.subject_id != '.'"
                " AND p.subject_type IN ('file','folder')")
        params: tuple = ()
    else:
        cond = ("p.subject_id LIKE ? AND p.subject_id NOT LIKE ?"
                " AND p.subject_type IN ('file','folder')")
        params = (folder + "/%", folder + "/%/%")
    return conn.execute(
        f"""SELECT p.*, r.body_markdown body FROM pages p
            JOIN revisions r ON r.revision_id = p.current_revision_id
            WHERE {cond}
            ORDER BY (p.page_type='folder') DESC, p.subject_id""",
        params).fetchall()


def _queued_events(conn, subject_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM events WHERE subject_id=? AND status='queued' "
        "ORDER BY created_at", (subject_id,)).fetchall()


def _event_messages(events) -> list[str]:
    out = []
    for ev in events:
        try:
            payload = json.loads(ev["payload"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        msg = payload.get("message") or payload.get("text")
        if msg and msg not in out:
            out.append(msg)
    return out


def build_file_prompt(conn, cfg, page, repo: Path):
    events = _queued_events(conn, page["subject_id"])
    parts = [FILE_INSTRUCTION, "", f"FILE: {page['subject_id']}", ""]
    from . import structure
    facts = structure.facts_block(conn, page["subject_id"])
    if facts:
        parts += ["STRUCTURAL FACTS (ground truth — do not contradict):",
                  facts, ""]
    blast = structure.impact(conn, page["subject_id"])
    if blast:
        parts.append("BLAST RADIUS (parsed dependents — if this file "
                     "breaks, these are affected, by hop distance):")
        parts.extend(f"- {subj} ({hop} hop{'s' if hop > 1 else ''})"
                     for subj, hop in blast[:15])
        parts.append("")
    else:
        parts += ["BLAST RADIUS: none — no other tracked file imports "
                  "this one.", ""]
    messages = _event_messages(events)
    if messages:
        parts.append("CHANGE NOTES:")
        parts.extend(f"- {m}" for m in messages[:8])
        parts.append("")
    fpath = repo / page["subject_id"]
    if fpath.is_file():
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            parts += [f"CONTENT of `{page['subject_id']}` "
                      f"(first {FILE_CONTENT_CAP} chars):",
                      text[:FILE_CONTENT_CAP], ""]
        except OSError:
            pass
    else:
        parts += ["NOTE: this file no longer exists on disk — write a "
                  "one-line tombstone page saying it was removed.", ""]
    body = db.current_body(conn, page["page_id"])
    parts += ["CURRENT PAGE:", body if body else "none — write the first "
              "version"]
    return "\n".join(parts), events


def build_folder_prompt(conn, cfg, page, repo: Path):
    events = _queued_events(conn, page["subject_id"])
    children = _children(conn, page["subject_id"])
    parts = [FOLDER_INSTRUCTION, "", f"FOLDER: {page['subject_id']}", "",
             "DIRECT CHILDREN AND THEIR SUMMARIES:"]
    for child in children:
        kind = "folder" if child["page_type"] == "folder" else "file"
        first = child["body"].strip().splitlines()
        gist = " ".join(first[1:3])[:300] if len(first) > 1 else first[0][:300]
        parts.append(f"- [{kind}] `{child['subject_id']}`: {gist}")
    parts.append("")
    messages = _event_messages(events)
    if messages:
        parts.append("CHANGE NOTES:")
        parts.extend(f"- {m}" for m in messages[:8])
        parts.append("")
    body = db.current_body(conn, page["page_id"])
    parts += ["CURRENT PAGE:", body if body else "none — write the first "
              "version"]
    return "\n".join(parts), events


ANSI_RE = None  # compiled lazily


def _strip_ansi(text: str) -> str:
    """Remove terminal escape sequences and CR (needed when the LLM CLI is
    wrapped in a pseudo-TTY, e.g. `script` around agy)."""
    global ANSI_RE
    if "\x1b" not in text and "\r" not in text:
        return text
    if ANSI_RE is None:
        import re
        ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*"
                             r"(\x07|\x1b\\)|\x1b[@-Z\\-_]")
    return ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def run_llm(cfg: dict, prompt: str) -> str:
    """Invoke the configured LLM command.

    Prompt delivery, chosen by placeholders in [llm].command:
      - "{prompt}"     -> substituted as a single argv element
      - "{promptfile}" -> prompt written to a temp file, path substituted
                          (recommended for CLIs like agy that take -p and
                          need a pseudo-TTY wrapper)
      - neither        -> piped via stdin (default; claude -p, mock, ...)
    """
    raw = cfg["llm"]["command"]
    cmd = shlex.split(raw)
    stdin_input = prompt
    tmp_path = None
    try:
        if any("{promptfile}" in tok for tok in cmd):
            import tempfile
            fh = tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8")
            fh.write(prompt)
            fh.close()
            tmp_path = fh.name
            cmd = [tok.replace("{promptfile}", tmp_path) for tok in cmd]
            stdin_input = ""
        elif any("{prompt}" in tok for tok in cmd):
            cmd = [tok.replace("{prompt}", prompt) for tok in cmd]
            stdin_input = ""
        try:
            result = subprocess.run(
                cmd, input=stdin_input, capture_output=True, text=True,
                timeout=int(cfg["llm"]["timeout"]),
            )
        except FileNotFoundError:
            raise SystemExit(
                f"irag: LLM command not found: {cmd[0]!r} — set "
                "[llm].command in .irag/config.toml")
        except subprocess.TimeoutExpired:
            raise SystemExit(f"irag: LLM command timed out after "
                             f"{cfg['llm']['timeout']}s")
        if result.returncode != 0:
            raise SystemExit(
                f"irag: LLM command failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:500]}")
        out = _strip_ansi(result.stdout).strip()
        if not out:
            raise SystemExit(
                "irag: LLM command produced no output. If you are using "
                "agy, note that 'agy -p' can drop stdout without a TTY — "
                "wrap it in a pseudo-TTY (see docs/SETUP.md, Antigravity "
                "section).")
        return out
    finally:
        if tmp_path:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def synthesize_page(conn, cfg, page, repo: Path, dry_run: bool = False) -> bool:
    if page["page_type"] == "folder":
        prompt, events = build_folder_prompt(conn, cfg, page, repo)
    else:
        prompt, events = build_file_prompt(conn, cfg, page, repo)
    if dry_run:
        print(f"===== DRY RUN — prompt for {page['subject_id']} =====")
        print(prompt)
        print("===== end prompt =====")
        return False

    event_ids = [ev["event_id"] for ev in events]
    if event_ids:
        conn.execute(
            f"UPDATE events SET status='processing' "
            f"WHERE event_id IN ({','.join('?' * len(event_ids))})",
            event_ids)
        conn.commit()
    try:
        body = run_llm(cfg, prompt)
    except SystemExit:
        if event_ids:
            conn.execute(
                f"UPDATE events SET status='failed', "
                f"processed_at=datetime('now') "
                f"WHERE event_id IN ({','.join('?' * len(event_ids))})",
                event_ids)
            conn.commit()
        raise
    if not body:
        raise SystemExit("irag: LLM returned empty output; page not updated")

    next_version = conn.execute(
        "SELECT COALESCE(MAX(version_number), 0) + 1 v FROM revisions "
        "WHERE page_id=?", (page["page_id"],)).fetchone()["v"]
    newest_event = event_ids[-1] if event_ids else None
    conn.execute(
        "INSERT INTO revisions(page_id, version_number, body_markdown, "
        "change_summary, triggered_by_event_id, llm_model_used, tokens_used) "
        "VALUES(?,?,?,?,?,?,?)",
        (page["page_id"], next_version, body,
         f"{page['page_type']} synthesis from {len(events)} event(s)",
         newest_event, cfg["llm"]["model_label"], len(body) // 4))
    if event_ids:
        conn.execute(
            f"UPDATE events SET status='completed', "
            f"processed_at=datetime('now') "
            f"WHERE event_id IN ({','.join('?' * len(event_ids))})",
            event_ids)
    conn.commit()
    return True


def requeue_stuck(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        """UPDATE events SET status='queued', processed_at=NULL
           WHERE status='failed'
              OR (status='processing'
                  AND created_at <= datetime('now', '-10 minutes'))""")
    conn.commit()
    return cur.rowcount


def sweep(conn, cfg, repo: Path, dry_run: bool = False,
          limit: int | None = None, subject: str | None = None) -> int:
    """Two-phase synthesis: pending file pages first, then folders
    bottom-up. Returns pages updated."""
    requeued = requeue_stuck(conn)
    if requeued:
        print(f"requeued {requeued} previously failed/stuck event(s)")
    if subject:
        page = conn.execute(
            "SELECT * FROM pages WHERE subject_id=?", (subject,)).fetchone()
        if not page:
            raise SystemExit(f"irag: no page for subject {subject!r}")
        pages = [page]
    else:
        pages = pending_file_pages(conn, cfg)
        if limit:
            pages = pages[:limit]
    done = 0
    for page in pages:
        if synthesize_page(conn, cfg, page, repo, dry_run=dry_run):
            done += 1
            print(f"synthesized: {page['subject_id']}")
    if not subject:
        # fixpoint: a folder becomes eligible once its children have
        # revisions, which can happen within this very sweep — iterate
        # until no more folders are pending (bounded by tree depth)
        for _ in range(30):
            folders = pending_folder_pages(conn, cfg)
            if dry_run:
                pass
            if limit is not None:
                folders = folders[: max(limit - done, 0)]
            if not folders:
                break
            progressed = False
            for page in folders:
                if synthesize_page(conn, cfg, page, repo, dry_run=dry_run):
                    done += 1
                    progressed = True
                    print(f"synthesized: {page['subject_id']}/ (folder)")
            if not progressed:
                break
    if done == 0 and not dry_run:
        _explain_nothing_pending(conn, cfg)
    return done


def _explain_nothing_pending(conn, cfg) -> None:
    threshold = int(cfg["staleness"]["threshold"])
    queued = conn.execute("SELECT COUNT(*) c FROM events "
                          "WHERE status='queued'").fetchone()["c"]
    pages = conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]
    max_stale = conn.execute(
        "SELECT COALESCE(MAX(staleness_score),0) m FROM pages "
        "WHERE pinned=0").fetchone()["m"]
    print("nothing pending —", end=" ")
    if pages == 0:
        print("no pages exist yet. Is there code here? Run 'irag sync', "
              "then check 'irag status'.")
    elif queued == 0:
        print("the event queue is empty (all changes already synthesized). "
              "New changes land automatically; check 'irag stale'.")
    else:
        print(f"{queued} queued event(s) exist but no unpinned page has "
              f"reached the staleness threshold ({max_stale}/{threshold}). "
              "Force one with 'irag synthesize --subject <path>' or lower "
              "[staleness].threshold in .irag/config.toml.")
