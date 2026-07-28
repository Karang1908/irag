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
JAVA_EXT = {".java"}
CS_EXT = {".cs"}
RB_EXT = {".rb"}
PHP_EXT = {".php"}
C_EXT = {".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"}
# Markup is scanned for imports only (it defines no symbols). Without it,
# `index.html` loading `app.js` via <script src> was not an edge, so
# `irag impact static/app.js` answered "change is contained" about a file
# the whole page depends on — a confident wrong answer that licenses the
# unsafe edit impact exists to prevent.
WEB_EXT = {".html", ".htm", ".css"}
CODE_EXT = (PY_EXT | JS_EXT | GO_EXT | RS_EXT | JAVA_EXT | CS_EXT | RB_EXT
            | PHP_EXT | C_EXT | WEB_EXT)

# Named groups: the alternation has grown past the point where positional
# indexes are safe to read. The `const` branch deliberately accepts ANY
# initializer - requiring `(`/`function`/`=>` after `=` missed every
# `const X = factory(...)` (zustand stores, createContext, styled, forwardRef),
# which left whole modules with zero indexed symbols. An optional type
# annotation is allowed between the name and `=` for TS.
# Anchored at column 0: `^\s*` under re.M matched any indentation, so
# `const t = (a + b)` inside a function body was indexed as a module symbol.
# Only top-level declarations belong in the structural index.
JS_SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?(?:function\s*\*?\s*(?P<fn>\w+)"
    r"|(?:abstract\s+)?class\s+(?P<cls>\w+)"
    r"|(?:interface|enum)\s+(?P<iface>\w+)"
    r"|type\s+(?P<ty>\w+)\s*="
    r"|(?:const|let|var)\s+(?P<var>\w+)(?:\s*:[^=\n]+?)?\s*="
    # destructured exports bind several names at once:
    #   export const { a, b } = obj   /   export const [x, y] = arr
    r"|(?:const|let|var)\s*(?P<destr>[{\[][^}\]\n]+[}\]])\s*=)",
    re.M,
)
# `import\s+` cannot match `import("./x.js")` — there is no whitespace after
# the keyword — so every dynamically imported module was invisible to the
# graph. The `import\s*\(` branch must precede the bare `import\s+` one.
JS_IMPORT_RE = re.compile(
    r"""(?:import\s+[^'"]*?from\s+|import\s*\(\s*|import\s+
        |require\s*\(\s*|export\s+[^'"]*?from\s+)
        ['"]([^'"]+)['"]""",
    re.X,
)
# <script src>, <link href>; also picks up <img src>, which resolves to a
# non-code file and is dropped by _resolve_import.
HTML_IMPORT_RE = re.compile(
    r"""<[a-zA-Z][^>]*?\s(?:src|href)\s*=\s*['"]([^'"]+)['"]""", re.S)
CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\()?\s*['"]([^'"]+)['"]""")
# `[...]` is the type-parameter list: without it every generic function
# (Go 1.18+) went unindexed, because the name is not followed by `(`.
GO_SYMBOL_RE = re.compile(
    r"^func\s+(?:\([^)]*\)\s+)?(\w+)\s*(?:\[[^\]]*\]\s*)?\(", re.M)
# Go declared no types at all: `type Server struct` and `type Handler
# interface` were invisible, so `irag map` showed a Go package as nothing
# but functions — while CLAUDE.md tells agents that map is parsed from the
# code and always current. Covers grouped declarations too, where the
# keyword appears once and the names are indented beneath it.
GO_TYPE_RE = re.compile(
    r"^type\s+(\w+)|^\s+(\w+)\s+(?:struct|interface)\s*\{", re.M)
GO_CONST_RE = re.compile(r"^(?:const|var)\s+(\w+)", re.M)
# `async`, `unsafe`, `const` and `extern "C"` all sit between `pub` and
# `fn`; without them every async method in a Rust codebase was unindexed,
# which is most of the API surface in anything doing I/O.
RS_SYMBOL_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:(?:default|const|async|unsafe|extern(?:\s+\"[^\"]*\")?)\s+)*"
    r"(?:fn\s+(\w+)|struct\s+(\w+)|enum\s+(\w+)|trait\s+(\w+))",
    re.M,
)
# `pub const LIMIT`, `static GLOBAL`, `type Alias = ...` are API surface a
# caller can name, and none of them were indexed.
RS_CONST_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(?:mut\s+)?(\w+)\s*:",
    re.M)
RS_TYPE_ALIAS_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?type\s+(\w+)\s*(?:<[^>]*>)?\s*=", re.M)

# --- languages added by the graph-widening pass ---------------------
# The phantom-symbol linter only ever flags a `name()` claim it can't
# find, so these regexes bias toward *over*-capturing callables: a missed
# method would falsely flag real code, an extra symbol only softens a
# check. Type declarations feed `irag map`; method/function captures feed
# both `map` and the linter.
JAVA_TYPE_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|abstract|final|static|sealed|strictfp)\s+)*"
    r"(?:class|interface|enum|record)\s+(\w+)", re.M)
# any brace-bodied `[modifiers/return type] name(...) {` — catches
# modifier-less and package-private methods and constructors that a
# modifier-anchored pattern would miss (the miss being the unsafe
# direction: it would falsely flag a real method as a phantom symbol).
# Control-flow keywords that share this shape are filtered in the loop.
JAVA_METHOD_RE = re.compile(
    r"^\s*(?:@\w+[\w.]*(?:\([^)]*\))?\s+)*[\w<>\[\].,?\s]*?\b(\w+)\s*"
    r"\([^;{]*\)\s*(?:throws[\w,.\s]+)?\{", re.M)

CS_TYPE_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|abstract|sealed|static|partial)\s+)*"
    r"(?:class|interface|struct|enum|record)\s+(\w+)", re.M)
CS_METHOD_RE = re.compile(
    r"^\s*(?:\[[^\]]*\]\s*)*[\w<>\[\].,?\s]*?\b(\w+)\s*\([^;{]*\)\s*\{", re.M)
# auto-properties are public API a caller names, and were not indexed
CS_PROPERTY_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|internal|static|virtual|override|"
    r"abstract|readonly|required)\s+)+[\w<>\[\].,?]+\s+(\w+)\s*\{\s*"
    r"(?:get|set|init)\b", re.M)

# control-flow and expression keywords that the generic method regexes
# above would otherwise capture as a method named e.g. `if` or `switch`
_METHOD_SKIP = {"if", "for", "while", "switch", "return", "catch", "do",
                "else", "new", "synchronized", "throw", "super", "this",
                "assert", "yield", "lock", "using", "fixed", "await",
                "instanceof", "sizeof",
                # `public static implicit operator int(S s)` made the
                # generic method pattern capture a symbol named `int`.
                # A primitive type name is never a method name.
                "int", "long", "short", "byte", "char", "bool", "boolean",
                "float", "double", "void", "string", "object", "decimal",
                "uint", "ulong", "ushort", "sbyte", "var", "operator"}

RB_TYPE_RE = re.compile(r"^\s*(?:class|module)\s+([A-Z]\w*)", re.M)
# `private def name` / `protected def name` are ordinary Ruby; anchoring
# on `def` alone missed every method declared that way.
RB_METHOD_RE = re.compile(
    r"^\s*(?:(?:private|public|protected|module_function)\s+)?"
    r"def\s+(?:self\.)?([A-Za-z_]\w*[?!=]?)", re.M)
RB_CONST_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=", re.M)
RB_IMPORT_RE = re.compile(r"""require_relative\s+['"]([^'"]+)['"]""")

PHP_TYPE_RE = re.compile(
    r"^\s*(?:(?:abstract|final)\s+)*(?:class|interface|trait)\s+(\w+)", re.M)
PHP_FUNC_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*"
    r"function\s+(\w+)", re.M)
PHP_CONST_RE = re.compile(
    r"^\s*(?:(?:public|private|protected|final)\s+)*const\s+(\w+)", re.M)
PHP_IMPORT_RE = re.compile(
    r"""(?:require|require_once|include|include_once)\s*\(?\s*['"]([^'"]+)['"]""")

C_TYPE_RE = re.compile(
    r"^\s*(?:typedef\s+)?(?:struct|class|enum|union)\s+(\w+)", re.M)
C_FUNC_RE = re.compile(
    r"^[A-Za-z_][\w\s\*&:<>,]*?\b(\w+)\s*\([^;{]*\)\s*(?:const\s*)?"
    r"(?:noexcept\s*)?\{", re.M)
C_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
# object-like and function-like macros are the names a caller actually
# writes in C; without them a page naming one looked like a phantom symbol
C_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)", re.M)
# words that C_FUNC_RE would otherwise mistake for a function name
_C_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "catch",
               "do", "else", "defined", "static_assert", "typeof", "and",
               "or", "not"}


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


IMPORT_EXTS = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs",
               ".rb", ".php", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs",
               ".java")


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


def _web_spec(rel: str, spec: str, bare_is_path: bool = False) -> str | None:
    """Repo-relative target for a JS/HTML/CSS specifier, or None when it
    cannot point at a file in this repo.

    `/static/app.js` is served from the repo root, not the filesystem root:
    treating it as "not relative" dropped every root-absolute reference on
    the floor. Bare specifiers (`three`, `react`) go through an import map
    or node_modules and are deliberately NOT guessed at — `bare_is_path` is
    set only for HTML/CSS, where `src="app.js"` really is a sibling file.
    """
    spec = spec.split("?")[0].split("#")[0].strip()
    if not spec or spec.startswith(
            ("http://", "https://", "//", "data:", "mailto:", "tel:")):
        return None
    if spec.startswith("/"):
        return spec.lstrip("/")
    if spec.startswith("."):
        return _rel_to_repo(rel, spec)
    if bare_is_path:
        return _rel_to_repo(rel, "./" + spec)
    return None


def _rel_to_repo(rel: str, spec: str) -> str:
    """Normalise a relative import spec (`./x`, `../y/z`) against the
    importing file's directory into a repo-relative path, without touching
    the filesystem. Used by JS/Ruby/C/PHP relative-import resolution."""
    parts: list[str] = []
    for p in (Path(rel).parent / spec).parts:
        if p == "..":
            if parts:
                parts.pop()
        elif p != ".":
            parts.append(p)
    return "/".join(parts)


def _line(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


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
            # `from X import a, b` — each name may itself be a submodule,
            # not just a symbol. `from . import db` is THE dominant
            # intra-package style in Python and resolves to the package
            # dir alone unless the names are considered too. Candidates
            # that aren't real files are dropped by _resolve_import, so
            # over-generating here is free.
            if node.level:  # relative: resolve against the file's dir
                base = Path(rel).parent
                for _ in range(node.level - 1):
                    base = base.parent
                target = base / (node.module or "").replace(".", "/")
                stem = str(target).replace("\\", "/")
            elif node.module:
                stem = node.module.replace(".", "/")
            else:
                continue
            if stem and stem != ".":
                yield ("imp", stem)
            for alias in node.names:
                yield ("imp", alias.name if stem in ("", ".")
                       else f"{stem}/{alias.name}")


def _regex_parse(text: str, rel: str, suffix: str):
    if suffix in JS_EXT:
        for m in JS_SYMBOL_RE.finditer(text):
            g = m.groupdict()
            line_no = _line(text, m.start())
            if g["destr"]:
                # one statement, several bindings; keep the local alias when
                # written `{ a: b }`, and drop defaults after `=`
                for part in g["destr"].strip("{}[]").split(","):
                    part = part.split("=")[0].split(":")[-1].strip()
                    part = part.lstrip(". ").strip()
                    if part.isidentifier():
                        yield ("sym", part, "const", line_no)
                continue
            name = g["fn"] or g["cls"] or g["iface"] or g["ty"] or g["var"]
            # a const/let/var binding is not a function: it may be a number,
            # an object, an array or a factory result. Calling all of them
            # "function" made `irag map` actively misleading, and CLAUDE.md
            # tells agents to trust that map as parsed from the code.
            kind = ("class" if g["cls"] else
                    "type" if (g["iface"] or g["ty"]) else
                    "const" if g["var"] else "function")
            yield ("sym", name, kind, line_no)
        for m in JS_IMPORT_RE.finditer(text):
            target = _web_spec(rel, m.group(1))
            if target:
                yield ("imp", target)
    elif suffix in WEB_EXT:
        # markup defines no symbols; it only wires files together
        pattern = CSS_IMPORT_RE if suffix == ".css" else HTML_IMPORT_RE
        for m in pattern.finditer(text):
            target = _web_spec(rel, m.group(1), bare_is_path=True)
            if target:
                yield ("imp", target)
    elif suffix in GO_EXT:
        for m in GO_SYMBOL_RE.finditer(text):
            yield ("sym", m.group(1), "function", _line(text, m.start()))
        for m in GO_TYPE_RE.finditer(text):
            name = m.group(1) or m.group(2)
            if name:
                yield ("sym", name, "type", _line(text, m.start()))
        for m in GO_CONST_RE.finditer(text):
            yield ("sym", m.group(1), "const", _line(text, m.start()))
    elif suffix in RS_EXT:
        for m in RS_SYMBOL_RE.finditer(text):
            name = next(g for g in m.groups() if g)
            kind = "function" if m.group(1) else "type"
            yield ("sym", name, kind, _line(text, m.start()))
        for m in RS_CONST_RE.finditer(text):
            yield ("sym", m.group(1), "const", _line(text, m.start()))
        for m in RS_TYPE_ALIAS_RE.finditer(text):
            yield ("sym", m.group(1), "type", _line(text, m.start()))
    elif suffix in JAVA_EXT:
        for m in JAVA_TYPE_RE.finditer(text):
            yield ("sym", m.group(1), "class", _line(text, m.start()))
        for m in JAVA_METHOD_RE.finditer(text):
            if m.group(1) not in _METHOD_SKIP:
                yield ("sym", m.group(1), "method", _line(text, m.start()))
        # Java imports are package paths, not files — resolving them needs
        # source roots we don't track, so Java contributes symbols but no
        # dependency edges (documented in the graph-widening notes).
    elif suffix in CS_EXT:
        for m in CS_TYPE_RE.finditer(text):
            yield ("sym", m.group(1), "class", _line(text, m.start()))
        for m in CS_METHOD_RE.finditer(text):
            if m.group(1) not in _METHOD_SKIP:
                yield ("sym", m.group(1), "method", _line(text, m.start()))
        for m in CS_PROPERTY_RE.finditer(text):
            yield ("sym", m.group(1), "const", _line(text, m.start()))
    elif suffix in RB_EXT:
        for m in RB_TYPE_RE.finditer(text):
            yield ("sym", m.group(1), "class", _line(text, m.start()))
        for m in RB_METHOD_RE.finditer(text):
            yield ("sym", m.group(1), "method", _line(text, m.start()))
        for m in RB_CONST_RE.finditer(text):
            yield ("sym", m.group(1), "const", _line(text, m.start()))
        for m in RB_IMPORT_RE.finditer(text):
            yield ("imp", _rel_to_repo(rel, m.group(1)))
    elif suffix in PHP_EXT:
        for m in PHP_TYPE_RE.finditer(text):
            yield ("sym", m.group(1), "class", _line(text, m.start()))
        for m in PHP_FUNC_RE.finditer(text):
            yield ("sym", m.group(1), "function", _line(text, m.start()))
        for m in PHP_CONST_RE.finditer(text):
            yield ("sym", m.group(1), "const", _line(text, m.start()))
        for m in PHP_IMPORT_RE.finditer(text):
            spec = m.group(1)
            if spec.startswith("."):
                yield ("imp", _rel_to_repo(rel, spec))
    elif suffix in C_EXT:
        for m in C_TYPE_RE.finditer(text):
            yield ("sym", m.group(1), "type", _line(text, m.start()))
        for m in C_FUNC_RE.finditer(text):
            name = m.group(1)
            if name in _C_KEYWORDS:
                continue   # `if (...) {`, `while (...) {`, ... are not funcs
            yield ("sym", name, "function", _line(text, m.start()))
        for m in C_DEFINE_RE.finditer(text):
            yield ("sym", m.group(1), "const", _line(text, m.start()))
        for m in C_INCLUDE_RE.finditer(text):
            yield ("imp", _rel_to_repo(rel, m.group(1)))


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
