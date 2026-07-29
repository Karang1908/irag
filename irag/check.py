"""irag.check — the CI gate: fail the build when memory disagrees with code.

Exit code 1 when there are open contradictions (configurable) or any page's
staleness exceeds the configured maximum.
"""
from __future__ import annotations

import sqlite3


def run(conn: sqlite3.Connection, cfg: dict, repo=None,
        run_facts: bool = True) -> int:
    """Print a health summary; return the intended process exit code.

    When `repo` is given, executable facts are re-run: a behavioural claim
    that no longer holds fails the build exactly like a hallucinated path.
    Facts that have never been executed are not counted — "unverified" is
    not "disproved", and the gate must not fail on something it never ran.
    """
    max_staleness = int(cfg["check"]["max_staleness"])
    fail_on_contra = bool(cfg["check"]["fail_on_contradictions"])
    fail_on_stale = bool(cfg["check"].get("fail_on_staleness", True))
    fail_on_unsynth = bool(cfg["check"].get("fail_on_unsynthesized", True))

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

    fact_failures = []
    # Executable facts are shell commands stored IN THE MEMORY DATABASE, and
    # that database is meant to be committed and shared. Running them here by
    # default meant `git clone && irag check` — the gate this project's own
    # docs tell you to put in CI — executed whatever commands a contributor
    # had registered. That is remote code execution on every dev machine and
    # runner, so it is opt-in: the person enabling it is vouching for the
    # commands in their own repo.
    if repo is not None and run_facts and bool(
            cfg.get("check", {}).get("fail_on_facts", False)):
        from . import facts as facts_mod
        try:
            results = facts_mod.run_all(conn, repo, announce=True)
        except sqlite3.OperationalError:
            results = []           # pre-facts database
        # only a command that RAN and disproved the claim gates the build;
        # one that could not run here says nothing about the claim
        fact_failures = [(r, s, why) for r, s, why in results if s == "fail"]
        fact_errors = [(r, s, why) for r, s, why in results if s == "error"]
        fact_total = len(results)
    else:
        fact_errors = []
        try:
            fact_total = conn.execute(
                "SELECT COUNT(*) c FROM facts").fetchone()["c"]
        except sqlite3.OperationalError:
            fact_total = 0

    print("irag check")
    print("-" * 40)
    print(f"open contradictions       : {open_contras}")
    print(f"pages over max staleness  : {over_stale} (max {max_staleness})")
    print(f"pages never synthesized   : {never_synth}")
    if fact_total:
        ran = fact_total - len(fact_failures) - len(fact_errors)
        print(f"executable facts          : {ran}/{fact_total} verified"
              + (f", {len(fact_errors)} could not run here"
                 if fact_errors else ""))
        for row, _s, why in fact_errors[:5]:
            print(f"  ~ [{row['fact_id']}] {row['claim']}")
            print(f"      not gating — {why}")

    failed = False
    if fact_failures:
        print("FAIL: behavioural claims no longer hold:")
        for row, status, why in fact_failures[:8]:
            print(f"  [{row['fact_id']}] {row['claim']}")
            print(f"      $ {row['cmd']}")
            print(f"      {status}: {why}")
        if len(fact_failures) > 8:
            print(f"  +{len(fact_failures) - 8} more")
        failed = True
    if fail_on_contra and open_contras:
        print("FAIL: open contradictions present (run 'irag contradictions')")
        failed = True
    if fail_on_stale and over_stale:
        print("FAIL: stale pages present (run 'irag synthesize')")
        failed = True
    if fail_on_unsynth and never_synth:
        # a never-synthesized page has staleness_score 0, so the staleness
        # gate above cannot see it. Without this, a project whose LLM was
        # misconfigured reports "pages never synthesized: 40" and exits 0 -
        # a gate whose job is "fail when memory disagrees with code" passing
        # with no memory at all.
        print("FAIL: pages never synthesized (run 'irag update')")
        failed = True
    if not failed:
        print("OK")
    return 1 if failed else 0
