"""irag.facts — executable memory: claims that carry their own proof.

Every other layer of irag describes what the code *says*. A page can be
lint-verified against the filesystem, the manifest and the symbol table,
but nothing in that set can tell you how the program *behaves* — that
`nmap -O` aborts the whole scan without root, that a fixed element inside
a `backdrop-filter` ancestor is positioned against the wrong box. Those
facts are learned by running something, and until now there was nowhere to
put them that could ever be re-checked.

A fact here is a claim plus the command that demonstrates it. `irag check`
re-runs them, so a behavioural claim that stops holding fails the build the
same way a hallucinated path does — and one that still holds is the only
kind of memory irag can re-establish from scratch instead of trusting.

The commands are the user's own, registered explicitly, and run only when
something asks for verification. Treat them exactly like a test suite: do
not register a command here that you would not run yourself.
"""
from __future__ import annotations

import shlex
import sqlite3
import subprocess
from pathlib import Path

DEFAULT_TIMEOUT = 120


def record(conn: sqlite3.Connection, claim: str, cmd: str,
           expect: str | None = None, expect_exit: int | None = None,
           subject_id: str = "", session_key: str | None = None) -> int:
    """Register a fact. Re-registering the same (claim, cmd) updates it."""
    claim = (claim or "").strip()
    cmd = (cmd or "").strip()
    if not claim:
        raise SystemExit("irag: a fact needs a claim")
    if not cmd:
        raise SystemExit(
            "irag: a fact needs --cmd, the command that demonstrates it. "
            "A claim with no proof belongs in 'irag learn'.")
    conn.execute(
        "INSERT INTO facts(claim, cmd, expect, expect_exit, subject_id, "
        "session_key) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(claim, cmd) DO UPDATE SET "
        "expect=excluded.expect, expect_exit=excluded.expect_exit, "
        "subject_id=excluded.subject_id",
        (claim, cmd, expect, expect_exit, subject_id, session_key))
    conn.commit()
    return conn.execute(
        "SELECT fact_id FROM facts WHERE claim=? AND cmd=?",
        (claim, cmd)).fetchone()["fact_id"]


def _judge(row, exit_code: int, output: str) -> tuple[str, str]:
    """(status, reason) for one execution."""
    if row["expect"]:
        if row["expect"] in output:
            return "pass", ""
        return "fail", (f"expected {row['expect']!r} in the output, "
                        f"got exit {exit_code}")
    want = row["expect_exit"]
    want = 0 if want is None else int(want)
    if exit_code == want:
        return "pass", ""
    return "fail", f"expected exit {want}, got {exit_code}"


# Characters that only a shell can honour. Without this check, a registered
# `echo hi | grep hi` was shlex-split into `echo` with the literal arguments
# `hi | grep hi`, whose output happens to contain "hi" — so the fact passed
# while testing something the user never wrote. A false ✓ is the worst
# possible bug in the one feature whose entire claim is that it re-proves
# itself, so a command that needs a shell gets one.
_SHELL_CHARS = set("|&;<>()$`*?~\n")


def needs_shell(cmd: str) -> bool:
    """True when the command contains an operator OUTSIDE quotes.

    A substring scan was wrong in the common case: `python3 -c 'a*b'` or
    `grep 'foo?' file` route through a shell they never needed, because the
    operator is quoted data rather than syntax. Quote state is tracked so
    only real syntax counts; anything shlex cannot tokenize at all is also
    handed to the shell, since that is exactly the input shlex would
    mis-split.
    """
    quote = None
    escaped = False
    for ch in cmd:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            continue
        if ch in _SHELL_CHARS:
            return True
    if quote:                       # unbalanced quote: shlex would raise
        return True
    try:
        shlex.split(cmd)
    except ValueError:
        return True
    return False


def _spawn(cmd: str, repo: Path, timeout: int):
    if needs_shell(cmd):
        return subprocess.run(cmd, cwd=repo, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    return subprocess.run(shlex.split(cmd), cwd=repo, capture_output=True,
                          text=True, timeout=timeout)


def run_one(conn: sqlite3.Connection, row, repo: Path,
            timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str]:
    """Execute one fact's command. Returns (status, reason)."""
    try:
        proc = _spawn(row["cmd"], repo, timeout)
        output = (proc.stdout or "") + (proc.stderr or "")
        status, reason = _judge(row, proc.returncode, output)
    except FileNotFoundError as exc:
        status, reason, output = "error", f"command not found: {exc}", ""
    except subprocess.TimeoutExpired:
        status, reason, output = "error", f"timed out after {timeout}s", ""
    except (OSError, ValueError) as exc:
        status, reason, output = "error", str(exc), ""
    conn.execute(
        "UPDATE facts SET last_run_at=datetime('now'), last_status=?, "
        "last_output=? WHERE fact_id=?",
        (status, output[-4000:], row["fact_id"]))
    conn.commit()
    return status, reason


def run_all(conn: sqlite3.Connection, repo: Path,
            subject_id: str | None = None,
            timeout: int = DEFAULT_TIMEOUT) -> list[tuple]:
    """Re-verify every fact (optionally scoped). Returns (row, status, reason)."""
    if subject_id:
        rows = conn.execute(
            "SELECT * FROM facts WHERE subject_id=? ORDER BY fact_id",
            (subject_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM facts ORDER BY fact_id").fetchall()
    return [(row, *run_one(conn, row, repo, timeout)) for row in rows]


def failing(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Facts whose last run did not pass. Never counts one that has not run:
    'not yet verified' is not the same as 'disproved', and the CI gate must
    not fail on a fact it has never executed."""
    return conn.execute(
        "SELECT * FROM facts WHERE last_status IS NOT NULL "
        "AND last_status != 'pass' ORDER BY fact_id").fetchall()


def for_subject(conn: sqlite3.Connection, subject_id: str,
                limit: int = 8) -> list[sqlite3.Row]:
    """Verified behavioural facts about a module, for briefings and prompts."""
    return conn.execute(
        "SELECT * FROM facts WHERE subject_id=? AND last_status='pass' "
        "ORDER BY last_run_at DESC LIMIT ?", (subject_id, limit)).fetchall()
