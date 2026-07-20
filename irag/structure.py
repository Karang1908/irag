"""irag.structure — the deterministic structural map (zero LLM tokens).

Parses source files (Python via ast; JS/TS/Go/Rust via regex) into two
relations: ``symbols`` (what is defined where) and ``deps`` (which module
imports which). This is the layer graph tools provide — a pre-computed
map the agent reads instead of grepping — except here it also *grounds*
the rest of irag: the linter verifies symbol claims against it, synthesis
prompts include it so the LLM hallucinates less, retrieval boosts
dependency neighbors, and the Obsidian graph gets real import edges.

Everything in this module is pure parsing: deterministic, free, local.
"""
from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

from . import db
from .ingest import file_subject, is_ignored

MAX_FILE_BYTES = 300_000
MAX_FILES = 4000

PY_EXT = {".py"}
JS_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
GO_EXT = {".go"}
RS_EXT = {".rs"}
CODE_EXT = PY_EXT | JS_EXT | GO_EXT | RS_EXT

JS_SYMBOL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?(?:function\s+(\w+)"
    r"|class\s+(\w+)"
    r"|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:\(|function\b|\w+\s*=>))",
    re.M,
)
JS_IMPORT_RE = re.compile(
    r"""(?:import\s+[^'"]*?from\s+|import\s+|require\s*\(\s*|export\s+[^'"]*?from\s+)
        ['"]([^'"]+)['"]""",
    re.X,
)
GO_SYMBOL_RE = re.compile(r"^func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(", re.M)
RS_SYMBOL_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:fn\s+(\w+)|struct\s+(\w+)|enum\s+(\w+)|trait\s+(\w+))",
    re.M,
)


def _iter_code_files(repo: Path, cfg: dict):
    count = 0
    for f in sorted(repo.rglob("*")):
        if count >= MAX_FILES:
            return
        if not f.is_file() or f.suffix not in CODE_EXT:
            continue
        rel = f.relative_to(repo)
        if is_ignored(rel.as_posix(), cfg, repo):
            continue
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        count += 1
        yield f, str(rel)


IMPORT_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs")


def _resolve_import(path_like: str, repo: Path,
                    known_files: set[str]) -> str | None:
    """Resolve an import target to a tracked FILE subject."""
    if path_like in known_files:
        return path_like
    for ext in IMPORT_EXTS:
        cand = path_like + ext
        if cand in known_files:
            return cand
    for idx in ("/__init__.py", "/index.ts", "/index.js", "/mod.rs"):
        cand = path_like + idx
        if cand in known_files:
            return cand
    return None


def _py_parse(text: str, rel: str):
    """Yield ('sym', name, kind, line) and ('imp', dotted_or_relpath)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield ("sym", node.name, "function", node.lineno)
        elif isinstance(node, ast.ClassDef):
            yield ("sym", node.name, "class", node.lineno)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield ("sym", f"{node.name}.{sub.name}", "method", sub.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield ("imp", alias.name.replace(".", "/"))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve against file dir
                base = Path(rel).parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base / (node.module or "").replace(".", "/")
                yield ("imp", str(target).replace("\\", "/"))
            elif node.module:
                yield ("imp", node.module.replace(".", "/"))


def _regex_parse(text: str, rel: str, suffix: str):
    if suffix in JS_EXT:
        for m in JS_SYMBOL_RE.finditer(text):
            name = m.group(1) or m.group(2) or m.group(3)
            kind = "class" if m.group(2) else "function"
            yield ("sym", name, kind, text[:m.start()].count("\n") + 1)
        for m in JS_IMPORT_RE.finditer(text):
            spec = m.group(1)
            if spec.startswith("."):  # relative → repo path
                try:
                    # normalise without touching the filesystem root
                    parts = []
                    for p in (Path(rel).parent / spec).parts:
                        if p == "..":
                            if parts:
                                parts.pop()
                        elif p != ".":
                            parts.append(p)
                    yield ("imp", "/".join(parts))
                except ValueError:
                    continue
    elif suffix in GO_EXT:
        for m in GO_SYMBOL_RE.finditer(text):
            yield ("sym", m.group(1), "function", text[:m.start()].count("\n") + 1)
    elif suffix in RS_EXT:
        for m in RS_SYMBOL_RE.finditer(text):
            name = next(g for g in m.groups() if g)
            kind = "function" if m.group(1) else "type"
            yield ("sym", name, kind, text[:m.start()].count("\n") + 1)


def scan(conn: sqlite3.Connection, cfg: dict, repo: Path,
         force: bool = False) -> dict:
    """(Re)build the symbols and deps tables. Skips when HEAD is unchanged
    unless forced. Returns counts."""
    # gate on the working-tree fingerprint (kept fresh by sync), so
    # UNCOMMITTED edits refresh the map too; fall back to git HEAD only
    # when no fingerprints exist yet
    import hashlib
    state = conn.execute(
        "SELECT path, hash FROM tree_state ORDER BY path").fetchall()
    if state:
        head = hashlib.sha1(
            "".join(r["path"] + r["hash"] for r in state).encode()
        ).hexdigest()
    else:
        try:
            import subprocess
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                                  capture_output=True,
                                  text=True).stdout.strip()
        except OSError:
            head = ""
    if not force and head and db.get_meta(conn, "last_scanned_head") == head:
        return {"skipped": True,
                "symbols": conn.execute(
                    "SELECT COUNT(*) c FROM symbols").fetchone()["c"],
                "deps": conn.execute(
                    "SELECT COUNT(*) c FROM deps").fetchone()["c"]}

    code_files = list(_iter_code_files(repo, cfg))
    known_files = {rel for _, rel in code_files}
    sym_rows: list[tuple] = []
    edge_counts: dict[tuple[str, str], int] = {}
    pending_imports: list[tuple[str, str]] = []

    for f, rel in code_files:
        source_subject = file_subject(rel, cfg, repo)
        if source_subject is None:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        items = (_py_parse(text, rel) if f.suffix in PY_EXT
                 else _regex_parse(text, rel, f.suffix))
        for item in items:
            if item[0] == "sym":
                _, name, kind, line = item
                sym_rows.append((source_subject, rel, name, kind, line))
            else:
                pending_imports.append((source_subject, item[1]))
    for source_subject, imp in pending_imports:
        target = _resolve_import(imp, repo, known_files)
        if target and target != source_subject:
            key = (source_subject, target)
            edge_counts[key] = edge_counts.get(key, 0) + 1

    conn.execute("DELETE FROM symbols")
    conn.executemany(
        "INSERT INTO symbols(subject_id, file, name, kind, line) "
        "VALUES(?,?,?,?,?)", sym_rows)
    conn.execute("DELETE FROM deps")
    conn.executemany(
        "INSERT INTO deps(source_subject, target_subject, import_count) "
        "VALUES(?,?,?)",
        [(s, t, n) for (s, t), n in edge_counts.items()])

    # project dep edges into the links table (drives retrieval link-hop
    # and the Obsidian graph); manual 'related' links are left untouched
    conn.execute("DELETE FROM links WHERE link_type='imports'")
    for (s, t) in edge_counts:
        sp = db.get_or_create_page(conn, s, subject_type="file",
                                   page_type="file")
        tp = db.get_or_create_page(conn, t, subject_type="file",
                                   page_type="file")
        conn.execute(
            "INSERT OR IGNORE INTO links(source_page_id, target_page_id, "
            "link_type) VALUES(?,?, 'imports')",
            (sp["page_id"], tp["page_id"]))
    if head:
        db.set_meta(conn, "last_scanned_head", head)
    conn.commit()
    return {"skipped": False, "symbols": len(sym_rows),
            "deps": len(edge_counts)}


# ---------------------------------------------------------------------
# queries
# ---------------------------------------------------------------------
def _subject_filter(subject: str) -> tuple[str, tuple]:
    """SQL condition matching a file exactly or everything under a folder.
    The folder prefix is LIKE-escaped so a literal `_` in a path can't act
    as a wildcard and pull in sibling directories."""
    if subject == ".":
        return "1=1", ()
    return ("({col} = ? OR {col} LIKE ? ESCAPE '\\')",
            (subject, db.like_escape(subject) + "/%"))


def module_facts(conn: sqlite3.Connection, subject: str,
                 max_symbols: int = 20) -> dict:
    """Structural facts for a file (exact) or folder (aggregated)."""
    cond, params = _subject_filter(subject)
    syms = conn.execute(
        f"SELECT name, kind, file, line FROM symbols "
        f"WHERE {cond.format(col='subject_id')} ORDER BY file, line",
        params).fetchall()
    files = sorted({s["file"] for s in syms})
    inside = set(files) | {subject}
    out_deps = [d for d in conn.execute(
        f"SELECT DISTINCT target_subject t FROM deps "
        f"WHERE {cond.format(col='source_subject')} ORDER BY t",
        params).fetchall() if d["t"] not in inside
        and not (subject != "." and d["t"].startswith(subject + "/"))]
    in_deps = [d for d in conn.execute(
        f"SELECT DISTINCT source_subject s FROM deps "
        f"WHERE {cond.format(col='target_subject')} ORDER BY s",
        params).fetchall() if d["s"] not in inside
        and not (subject != "." and d["s"].startswith(subject + "/"))]
    return {
        "files": files,
        "symbols": [dict(s) for s in syms[:max_symbols]],
        "symbol_count": len(syms),
        "imports": [d["t"] for d in out_deps],
        "imported_by": [d["s"] for d in in_deps],
    }


def facts_block(conn: sqlite3.Connection, subject: str) -> str:
    """Compact markdown block of structural facts (for prompts/context)."""
    facts = module_facts(conn, subject)
    if not facts["files"] and not facts["imports"] and not facts["imported_by"]:
        return ""
    lines = []
    if facts["files"]:
        lines.append("files: " + ", ".join(f"`{f}`" for f in facts["files"][:10]))
    if facts["symbols"]:
        names = ", ".join(f"`{s['name']}`" for s in facts["symbols"])
        more = (f" (+{facts['symbol_count'] - len(facts['symbols'])} more)"
                if facts["symbol_count"] > len(facts["symbols"]) else "")
        lines.append(f"defines: {names}{more}")
    if facts["imports"]:
        lines.append("imports → " + ", ".join(facts["imports"]))
    if facts["imported_by"]:
        lines.append("imported by ← " + ", ".join(facts["imported_by"]))
    return "\n".join(lines)


def impact(conn: sqlite3.Connection, subject: str) -> list[tuple[str, int]]:
    """Transitive reverse dependencies: everything that could break if
    ``subject`` (a file or a folder) changes, with hop distance."""
    edges: dict[str, list[str]] = {}
    for row in conn.execute("SELECT source_subject s, target_subject t "
                            "FROM deps").fetchall():
        edges.setdefault(row["t"], []).append(row["s"])
    if subject == ".":
        seeds = list(edges.keys())
    else:
        seeds = [k for k in edges
                 if k == subject or k.startswith(subject + "/")]
        if subject in {s for lst in edges.values() for s in lst} \
                and subject not in seeds:
            seeds.append(subject)
        if not seeds:
            seeds = [subject]
    seen = set(seeds) | ({subject} if subject != "." else set())
    frontier = list(seeds)
    result: list[tuple[str, int]] = []
    hop = 0
    while frontier:
        hop += 1
        nxt = []
        for node in frontier:
            for dependent in edges.get(node, ()):
                if dependent not in seen:
                    seen.add(dependent)
                    result.append((dependent, hop))
                    nxt.append(dependent)
        frontier = nxt
    return result


def overview(conn: sqlite3.Connection) -> str:
    """Repo-wide one-screen map."""
    mods = conn.execute(
        """SELECT subject_id, COUNT(*) n FROM symbols
           GROUP BY subject_id ORDER BY subject_id""").fetchall()
    deps = conn.execute(
        "SELECT source_subject s, target_subject t, import_count n "
        "FROM deps ORDER BY n DESC").fetchall()
    if not mods and not deps:
        return "no structural data — run 'irag scan'"
    lines = ["FILES (symbols defined):"]
    for m in mods:
        lines.append(f"  {m['subject_id']}: {m['n']}")
    if deps:
        lines.append("DEPENDENCIES (imports):")
        for d in deps:
            lines.append(f"  {d['s']} → {d['t']}  (x{d['n']})")
    return "\n".join(lines)
