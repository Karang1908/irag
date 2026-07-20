"""irag.export — install the agent guide (CLAUDE.md + AGENTS.md).

CLAUDE.md and AGENTS.md are irag's static "how to use irag" instructions
for a coding agent — the operator manual bundled with the package
(assets/AGENT_GUIDE.md). `irag init` installs them into the project root;
`irag export` re-installs them. They are deliberately NOT a memory dump:
the memory lives in .irag/memory.db and the agent reads it on demand via
`irag context` / `recap` / `search` / `map`. `irag update` therefore never
rewrites these files — it only updates the database.
"""
from __future__ import annotations

import os
from pathlib import Path

GUIDE = Path(__file__).parent / "assets" / "AGENT_GUIDE.md"
MARKER = "<!-- irag agent guide"
TARGETS = ("CLAUDE.md", "AGENTS.md")


def _atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + os.replace so an interrupted run can never
    leave a half-written file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def install_guide(repo: Path) -> dict:
    """Install CLAUDE.md + AGENTS.md (the agent operator manual) at the
    project root. A file that already exists and was NOT written by irag is
    left untouched (we never clobber a user's own CLAUDE.md). Returns the
    lists of written and skipped paths so the caller can report."""
    body = GUIDE.read_text(encoding="utf-8")
    text = (f"{MARKER} — installed by irag; do not hand-edit, re-install "
            f"with 'irag export' -->\n\n{body}")
    written: list[Path] = []
    skipped: list[Path] = []
    for name in TARGETS:
        p = repo / name
        if p.exists():
            head = p.read_text(encoding="utf-8", errors="replace")[:200]
            if MARKER not in head:
                skipped.append(p)
                continue
        _atomic_write(p, text)
        written.append(p)
    return {"written": written, "skipped": skipped}
