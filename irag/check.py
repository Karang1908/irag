"""irag.check — the CI gate: fail the build when memory disagrees with code.

Exit code 1 when there are open contradictions (configurable) or any page's
staleness exceeds the configured maximum.
"""
from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection, cfg: dict) -> int:
    """Print a health summary; return the intended process exit code."""
    max_staleness = int(cfg["check"]["max_staleness"])
    fail_on_contra = bool(cfg["check"]["fail_on_contradictions"])

    open_contras = conn.execute(
        "SELECT COUNT(*) c FROM contradictions WHERE resolved_at IS NULL"
    ).fetchone()["c"]
    over_stale = conn.execute(
        "SELECT COUNT(*) c FROM pages WHERE staleness_score > ?",
        (max_staleness,),
    ).fetchone()["c"]
    never_synth = conn.execute(
        "SELECT COUNT(*) c FROM pages WHERE current_revision_id IS NULL"
    ).fetchone()["c"]

    print("irag check")
    print("-" * 40)
    print(f"open contradictions       : {open_contras}")
    print(f"pages over max staleness  : {over_stale} (max {max_staleness})")
    print(f"pages never synthesized   : {never_synth}")

    failed = False
    if fail_on_contra and open_contras:
        print("FAIL: open contradictions present (run 'irag contradictions')")
        failed = True
    if over_stale:
        print("FAIL: stale pages present (run 'irag synthesize')")
        failed = True
    if not failed:
        print("OK")
    return 1 if failed else 0
