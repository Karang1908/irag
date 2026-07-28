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

from . import db

PATH_RE = re.compile(
    r"`([\w./-]+\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|mts|vue|svelte|go|rs|java"
    r"|kt|swift|dart|rb|php|cs|c|h|cc|cpp|hpp|md|json|toml|ya?ml|sql|sh|css"
    r"|scss|sass|less|html|htm|svg|lock|cfg|ini|txt))`"
)
VERSION_AT_RE = re.compile(
    r"(@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)@(\d+\.\d+[\w.\-]*)")
VERSION_WORD_RE = re.compile(r"`?([A-Za-z0-9_.-]+)`?\s+version\s+(\d+\.\d+[\w.\-]*)")
SYMBOL_RE = re.compile(r"`(\w+)\(\)`")
SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go",
              ".rs", ".java", ".rb", ".php", ".cs", ".kt", ".swift", ".dart",
              ".vue", ".svelte", ".c", ".h", ".cc", ".cpp", ".hpp"}


# A declared spec is only worth comparing against prose when it pins one
# concrete version. "4.x", "workspace:*", "git+https://...#v3" and ">=1,<2"
# are all satisfied by many versions, so a claim that disagrees with the
# literal spec string is not thereby wrong - it is unverifiable, and
# reporting it as a mismatch is a false positive.
_UNPINNED = re.compile(r"[x*|\s,]|^(?:git|github|file|link|workspace|npm|https?)[:+]",
                       re.I)


def _pinned(spec: str) -> str | None:
    """The concrete version a spec pins, or None if it pins no single one."""
    spec = re.sub(r"^[\^~>=<\s]+", "", str(spec)).strip()
    if not spec or _UNPINNED.search(spec):
        return None
    return spec if re.match(r"^\d+\.\d+", spec) else None


def _package_names(repo: Path) -> tuple[set[str], tuple[str, ...]]:
    """Declared third-party package names, and import-map path prefixes.

    `three/addons/controls/OrbitControls.js` is a bare specifier resolved
    through an <script type="importmap"> to a CDN — it is not a repo file
    and must never be reported missing. _read_manifest_versions only keeps
    entries pinned to a single version, so a normal `"three": "^0.160.0"`
    range is absent from it and cannot be used for this.

    Returns (names, prefixes): names are matched against the first path
    segment (two, for @scoped packages); prefixes are import-map keys that
    end in "/" and are matched against the whole specifier.
    """
    names: set[str] = set()
    prefixes: list[str] = []
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies",
                            "peerDependencies", "optionalDependencies"):
                names.update((data.get(section) or {}).keys())
        except (json.JSONDecodeError, OSError):
            pass
    for line in _requirements_names(repo):
        names.add(line)
    # import maps live in HTML; a project may have several
    count = 0
    for html in repo.rglob("*.htm*"):
        if count >= 40:
            break
        count += 1
        try:
            text = html.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(
                r"<script[^>]*type\s*=\s*['\"]importmap['\"][^>]*>(.*?)</script>",
                text, re.S | re.I):
            try:
                imports = (json.loads(m.group(1)) or {}).get("imports") or {}
            except json.JSONDecodeError:
                continue
            for key in imports:
                if key.endswith("/"):
                    prefixes.append(key)
                else:
                    names.add(key)
    nm = repo / "node_modules"
    if nm.is_dir():
        try:
            for entry in nm.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name.startswith("@"):
                    names.update(f"{entry.name}/{s.name}"
                                 for s in entry.iterdir() if s.is_dir())
                elif not entry.name.startswith("."):
                    names.add(entry.name)
        except OSError:
            pass
    return {n.lower() for n in names}, tuple(p.lower() for p in prefixes)


def _requirements_names(repo: Path) -> list[str]:
    """Package names from requirements.txt, pinned or not."""
    req = repo / "requirements.txt"
    if not req.is_file():
        return []
    out = []
    try:
        for line in req.read_text(encoding="utf-8").splitlines():
            line = line.split("#")[0].split(";")[0].strip()
            if not line or line.startswith("-"):
                continue
            name = re.split(r"[=<>!~\[]", line)[0].strip().lower()
            if name:
                out.append(name)
    except OSError:
        pass
    return out


def _is_package_spec(probe: str, names: set[str],
                     prefixes: tuple[str, ...]) -> bool:
    """True when probe resolves through a package manager / import map."""
    low = probe.lower()
    if any(low.startswith(p) for p in prefixes):
        return True
    parts = low.split("/")
    if parts[0].startswith("@") and len(parts) >= 2:
        return f"{parts[0]}/{parts[1]}" in names
    return parts[0] in names


def _read_manifest_versions(repo: Path) -> dict[str, str]:
    """Collect declared dependency versions from package.json and requirements.txt.
    Only entries pinning a single concrete version are returned; anything a
    range or URL could satisfy is left out so it is never asserted against."""
    versions: dict[str, str] = {}
    pkg = repo / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies"):
                for name, spec in (data.get(section) or {}).items():
                    pin = _pinned(spec)
                    if pin:
                        versions[name.lower()] = pin
        except (json.JSONDecodeError, OSError):
            pass
    req = repo / "requirements.txt"
    if req.is_file():
        try:
            for line in req.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if "==" not in line or line.startswith("#"):
                    continue
                name, ver = line.split("==", 1)
                # strip PEP 508 environment markers and trailing comments,
                # which otherwise land inside the "version"
                ver = ver.split(";")[0].split("#")[0].strip()
                # uvicorn[standard] must still answer to "uvicorn"
                name = re.sub(r"\[.*?\]", "", name).strip().lower()
                pin = _pinned(ver)
                if name and pin:
                    versions[name] = pin
        except OSError:
            pass
    return versions


# Bindings pulled in from a package rather than from this repo. A symbol
# that arrives this way lives in node_modules / site-packages, which the
# scanner never indexes - so "not in the symbol table" says nothing about
# whether it exists. Absence of evidence is not evidence of absence.
_JS_IMPORT_BINDINGS_RE = re.compile(
    r"import\s+(?:type\s+)?([^;'\"]+?)\s+from\s+['\"]([^'\"]+)['\"]")
_PY_IMPORT_FROM_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+(.+)$", re.M)


def _external_names(repo: Path, subject_id: str) -> set[str]:
    """Names the file imports from a non-relative (package) specifier."""
    out: set[str] = set()
    f = repo / subject_id
    if not f.is_file():
        return out
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for binds, spec in _JS_IMPORT_BINDINGS_RE.findall(text):
        if spec.startswith((".", "/")):
            continue                      # repo-local: the index should know it
        for part in binds.replace("{", " ").replace("}", " ").split(","):
            part = part.strip()
            if not part:
                continue
            if " as " in part:
                part = part.split(" as ")[-1].strip()
            part = part.lstrip("* ").strip()
            if part.isidentifier():
                out.add(part)
    for mod, binds in _PY_IMPORT_FROM_RE.findall(text):
        if mod.startswith("."):
            continue
        for part in binds.split(","):
            part = part.strip().strip("()").split(" as ")[-1].strip()
            if part.isidentifier():
                out.add(part)
    return out


def _defines_pattern(sym: str) -> re.Pattern:
    """Matches a *definition* of ``sym`` (not a call to it)."""
    esc = re.escape(sym)
    return re.compile(
        # def/function/const/fn/func NAME (Py/JS/Go/Rust), or a bare
        # NAME(...) { / NAME(...) throws (Java/C/C++/C# method or ctor).
        # Parens are excluded from the argument class so a call site inside
        # a larger expression cannot masquerade as a signature.
        rf"\b(?:def|function|const|let|var|class|interface|type|enum"
        rf"|fn|func)\s+{esc}\b"
        rf"|(?<![.\w]){esc}\s*\([^;=()\n]*?\)\s*(?:\{{|throws\b)"
        # object-literal / class members: `name(...) {`, `name: (...) =>`,
        # `name: function`, which is how zustand actions and methods appear
        rf"|(?<![.\w]){esc}\s*:\s*(?:async\s*)?(?:\(|function\b)"
    )


def subj_dir(repo: Path, subject_id: str) -> Path:
    """The directory whose source backs this page."""
    if subject_id == ".":
        return repo
    p = repo / subject_id
    return p if p.is_dir() else p.parent


def _read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8", errors="replace")[:200_000]
    except OSError:
        return ""


def _module_source(target: Path) -> str:
    """Source backing a page: the file itself, or a folder page's tree.

    A file page greps only its own file. Its siblings are already reachable
    through the symbols table (via the page's imports), and this grep exists
    only to find declarations the top-level index deliberately omits - nested
    helpers and object-literal members - which are in the same file. Reading
    the whole directory let "right symbol, wrong file" pass unflagged.
    """
    if target.is_file():
        return _read(target)
    if not target.is_dir():
        return ""
    out = ""
    for f in target.rglob("*"):
        if f.is_file() and f.suffix in SOURCE_EXT:
            out += _read(f)
    return out


# `render()` in prose may be createRoot(...).render(...) - a method on an
# object returned at runtime. Nothing in a static index can confirm or deny
# it, so a method call that isn't locally defined is unverifiable, not false.
def _method_call_re(sym: str) -> re.Pattern:
    return re.compile(rf"\.\s*{re.escape(sym)}\s*\(")


def _basename_index(repo: Path, cfg: dict) -> dict[str, list[str]]:
    """basename -> repo-relative paths, for resolving a bare filename claim."""
    from .ingest import is_ignored
    idx: dict[str, list[str]] = {}
    count = 0
    for f in repo.rglob("*"):
        if count > 20000:
            break
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(repo).as_posix()
        except ValueError:
            continue
        if is_ignored(rel, cfg, repo):
            continue
        count += 1
        idx.setdefault(f.name, []).append(rel)
    return idx


def _existing_open_claim(conn, page_id: int, claim: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM contradictions "
        "WHERE page_id=? AND claim=? AND resolved_at IS NULL",
        (page_id, claim),
    ).fetchone() is not None


def _dismissed_claim(conn, page_id: int, claim: str, ctype: str) -> bool:
    """A human already judged this exact claim on this page spurious.

    Dedup alone only matched OPEN rows, so resolving a contradiction hid it
    from the check and the next lint re-raised an identical claim under a
    new id. Per-ID resolution was therefore defeated by re-synthesis, and
    an agent that watches its judgment get overturned every update learns
    to ignore the linter wholesale — which costs far more than the original
    false positive.

    Only MANUAL resolutions dismiss. An auto-resolved claim (one that
    stopped failing on its own) must be free to re-raise: that is a real
    regression, not a settled question. A re-synthesis that changes the
    claim's wording also re-raises, since the fingerprint is the text.
    """
    return conn.execute(
        "SELECT 1 FROM contradictions "
        "WHERE page_id=? AND claim=? AND ctype=? "
        "AND resolved_at IS NOT NULL AND resolution_kind='manual'",
        (page_id, claim, ctype),
    ).fetchone() is not None


def _insert(conn, page_id: int, revision_id: int, claim: str, truth: str,
            ctype: str, severity: str, check: str,
            detector: str = "static") -> bool:
    if _existing_open_claim(conn, page_id, claim):
        return False
    if _dismissed_claim(conn, page_id, claim, ctype):
        return False
    conn.execute(
        "INSERT INTO contradictions(page_id, revision_id, claim, truth, ctype, "
        "severity, detected_by) VALUES(?,?,?,?,?,?,?)",
        (page_id, revision_id, claim, truth, ctype, severity,
         f"{detector}:{check}"),
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
    pkg_names, pkg_prefixes = _package_names(repo)
    basenames = _basename_index(repo, cfg)
    added = 0

    for page in pages:
        body = page["body"]
        failing: set[str] = set()   # claims that (still) fail this run

        # (a) backticked path-like tokens must exist on disk
        for match in PATH_RE.finditer(body):
            rel = match.group(1)
            # Path("/src/main.tsx") is absolute, so `repo / rel` silently
            # discards repo and tests the filesystem root. Bundler URLs
            # (Vite's /src/...) are written exactly this way.
            probe = rel.lstrip("/")
            if (repo / probe).exists():
                continue
            # a claim may be written relative to the page's own directory:
            # `./styles/app.css` on a page for src/main.tsx means
            # src/styles/app.css
            if probe.startswith("./") or probe.startswith("../"):
                here = subj_dir(repo, page["subject_id"])
                try:
                    if (here / probe).resolve().is_relative_to(repo.resolve()) \
                            and (here / probe).exists():
                        continue
                except (OSError, ValueError):
                    pass
            # A bare token with no separator may be a package, not a file:
            # `three.js`, `Next.js`, `Vue.js` all satisfy PATH_RE.
            if "/" not in probe and probe.rsplit(".", 1)[0].lower() in manifest:
                continue
            # `three/addons/controls/OrbitControls.js` is a bare specifier
            # resolved by an import map or node_modules, not a repo file.
            if _is_package_spec(probe, pkg_names, pkg_prefixes):
                continue
            # ...or a real file referred to by name only, e.g. `store.ts`
            # living at src/store.ts. Say where it is instead of denying it.
            # fall back on the filename alone: `store.ts` may live at
            # src/store.ts, and `./styles/app.css` at src/styles/app.css
            if basenames.get(probe.rsplit("/", 1)[-1]):
                continue
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
        # scoped to THIS page: a repo-wide "any symbols at all?" test meant
        # one indexed Python file forced every page through the strict check,
        # including files in languages structure.py does not parse (.kt,
        # .swift, .vue, .dart) - whose real symbols were then reported as
        # hallucinated. No structural data for this subject => grep fallback.
        have_symbols = conn.execute(
            "SELECT 1 FROM symbols WHERE subject_id=? LIMIT 1",
            (page["subject_id"],)).fetchone() is not None
        if have_symbols:
            # a symbol claim is valid if it's defined in the page's OWN
            # module or in any module the page imports — not merely
            # "anywhere in the repo" (which missed real phantom-symbol
            # hallucinations) nor "only this file" (which would falsely
            # flag legitimate references to imported symbols)
            from . import structure
            mf = structure.module_facts(conn, page["subject_id"])
            allowed = ({page["subject_id"]} | set(mf.get("files") or [])
                       | set(mf.get("imports") or []))
            marks = ",".join("?" * len(allowed))
            external = _external_names(repo, page["subject_id"])
            src_cache: list[str] = []       # module source, read at most once
            for match in SYMBOL_RE.finditer(body):
                sym = match.group(1)
                if sym in external:
                    continue          # lives in a package; unverifiable, not false
                defined = conn.execute(
                    f"SELECT 1 FROM symbols WHERE (name=? OR name LIKE ? "
                    f"ESCAPE '\\') AND subject_id IN ({marks}) LIMIT 1",
                    (sym, f"%.{db.like_escape(sym)}", *allowed)).fetchone()
                if not defined:
                    # The index holds TOP-LEVEL declarations only - that is
                    # deliberate, so `irag map` isn't polluted with locals.
                    # It therefore is not an exhaustive list of what exists,
                    # and absence from it does not mean the symbol is not
                    # there: nested helpers and object-literal members
                    # (zustand actions, class methods) are all real. Confirm
                    # against the source before calling anything a phantom.
                    if not src_cache:
                        src_cache.append(_module_source(
                            repo / page["subject_id"]))
                    if src_cache[0] and _defines_pattern(sym).search(src_cache[0]):
                        continue
                    if src_cache[0] and _method_call_re(sym).search(src_cache[0]):
                        continue    # a method on some object; unverifiable
                if not defined:
                    failing.add(f"references symbol `{sym}()`")
                    if _insert(conn, page["page_id"], page["rev_id"],
                               claim=f"references symbol `{sym}()`",
                               truth=f"no definition of {sym} in "
                                     f"{page['subject_id']} or anything it "
                                     f"imports",
                               ctype="missing_symbol", severity="low",
                               check="missing_symbol"):
                        added += 1
        subj_path = repo / page["subject_id"]
        mod_dir = (subj_path if subj_path.is_dir()
                   else subj_path.parent) if page["subject_id"] != "." else repo
        if not have_symbols and mod_dir.is_dir():
            source = _module_source(mod_dir)
            # the package-import guard belongs on this path too: it used to
            # apply only when the symbols table had data, so every file
            # falling back to the grep still had its imported names flagged
            external = _external_names(repo, page["subject_id"])
            for match in SYMBOL_RE.finditer(body):
                sym = match.group(1)
                if sym in external:
                    continue
                if source and _method_call_re(sym).search(source):
                    continue        # a method on some object; unverifiable
                if source and not _defines_pattern(sym).search(source):
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
                    "resolution_notes='auto-resolved: claim no longer fails', "
                    "resolution_kind='auto' "
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
        "resolution_notes=?, resolution_kind='manual' "
        "WHERE contradiction_id=?",
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
        flagged: set[str] = set()
        if out.strip().upper() != "NONE":
            for line in out.splitlines():
                if "|" not in line:
                    continue
                # tolerate markdown-table formatting: "| claim | why |"
                line = line.strip().strip("|").strip()
                if not line or set(line) <= set("-|: "):
                    continue          # separator row
                claim, why = (s.strip() for s in line.split("|", 1))
                if not claim:
                    continue
                flagged.add(claim)
                if _insert(conn, page["page_id"], page["rev_id"],
                           claim=claim, truth=why, ctype="llm_flagged",
                           severity="medium", check="llm", detector="llm"):
                    added += 1
        # auto-resolve LLM-flagged claims this page no longer trips. These
        # carry the 'llm:' prefix so the static tier's auto-resolve (scoped
        # to 'static:%') never touches them — this is the only place they
        # resolve, so a re-run that stops flagging a claim clears it.
        for row in conn.execute(
                "SELECT contradiction_id, claim FROM contradictions "
                "WHERE page_id=? AND resolved_at IS NULL "
                "AND detected_by LIKE 'llm:%'",
                (page["page_id"],)).fetchall():
            if row["claim"] not in flagged:
                conn.execute(
                    "UPDATE contradictions SET resolved_at=datetime('now'), "
                    "resolution_notes='auto-resolved: LLM no longer flags it', "
                    "resolution_kind='auto' "
                    "WHERE contradiction_id=?", (row["contradiction_id"],))
    conn.commit()
    return added
