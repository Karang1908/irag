"""Deterministic code, API, and security audit with optional OSV lookups.

The scanner is intentionally conservative: it reports evidence and confidence,
never labels heuristic text matches as proven exploits, never prints suspected
secret values, and still returns a complete local report if advisory lookup is
offline.
"""
from __future__ import annotations

import ast
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
import os
import re
import sqlite3
import time
import tomllib
from pathlib import Path
import ipaddress
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from . import ingest, structure


MAX_FILES = 20_000
MAX_FILE_BYTES = 1_000_000
MAX_OSV_PACKAGES = 500
TEXT_EXT = (structure.CODE_EXT | {".json", ".toml", ".yaml", ".yml",
            ".xml", ".ini", ".cfg", ".conf", ".gradle", ".kts", ".sh",
            ".bash", ".zsh", ".ps1", ".txt", ".lock"})
SPECIAL_FILES = {"Dockerfile", "Gemfile", "Gemfile.lock", "go.mod", "go.sum",
                 "package-lock.json", "composer.lock", "packages.lock.json",
                 "requirements.txt", "Pipfile.lock", "Cargo.lock"}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|client[_-]?secret|password|passwd|"
    r"secret[_-]?key)\b\s*[:=]\s*['\"]([^'\"\r\n]{8,})['\"]")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
ROUTE_PATTERNS = (
    re.compile(r"@(?:\w+\.)*(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)", re.I),
    re.compile(r"@(?:\w+\.)*route\(\s*['\"]([^'\"]+)['\"]([^\n]*)", re.I),
    re.compile(r"(?:\bapp|\brouter|\bserver|\bfastify)\.(get|post|put|patch|delete|options|head)\(\s*['\"]([^'\"]+)", re.I),
    re.compile(r"Route::(get|post|put|patch|delete|options)\(\s*['\"]([^'\"]+)", re.I),
    re.compile(r"\b(get|post|put|patch|delete)\s+['\"]([^'\"]+)['\"]\s*(?:=>|,\s*to:)", re.I),
)
AUTH_RE = re.compile(
    r"(?i)\b(auth|authoriz|permission|login_required|jwt|oauth|session|"
    r"current_user|Depends\s*\([^)]*(?:user|auth)|middleware)\b")


def run(conn: sqlite3.Connection, cfg: dict, root: Path, *,
        check_advisories: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    findings: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    file_rows: list[tuple[Path, str, str]] = []
    languages: Counter[str] = Counter()
    total_lines = todos = skipped_large = 0

    for path, rel in _iter_text_files(root, cfg):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_FILE_BYTES:
            skipped_large += 1
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        file_rows.append((path, rel, text))
        lines = text.splitlines()
        line_count = len(lines)
        total_lines += line_count
        languages[path.suffix.lower() or path.name] += 1
        todos += len(re.findall(r"(?i)\b(?:TODO|FIXME|HACK)\b", text))
        if line_count > 1200:
            findings.append(_finding(
                "quality.large-file", "medium", "quality", rel, 1,
                f"Large file has {line_count:,} lines",
                "Large files concentrate change risk and make review harder.",
                "Split by responsibility when a natural boundary exists.",
                "high"))
        findings.extend(_text_security(rel, lines, test_file=_is_test(rel)))
        routes.extend(_routes(rel, lines))
        if path.suffix.lower() == ".py":
            findings.extend(_python_checks(rel, text))

    findings.extend(_tracked_environment_files(root))
    findings.extend(_api_findings(routes))
    cycles = _dependency_cycles(conn)
    for cycle in cycles[:30]:
        findings.append(_finding(
            "quality.dependency-cycle", "medium", "architecture", cycle[0], 1,
            f"Dependency cycle across {len(cycle)} modules",
            " → ".join(cycle + [cycle[0]]),
            "Break the cycle at the narrowest interface or move the shared contract.",
            "high"))

    source_count = sum(1 for _, rel, _ in file_rows
                       if not _is_test(rel) and
                       Path(rel).suffix.lower() in structure.CODE_EXT)
    test_count = sum(1 for _, rel, _ in file_rows if _is_test(rel))
    if source_count >= 5 and test_count == 0:
        findings.append(_finding(
            "quality.no-tests", "medium", "quality", ".", 1,
            "No test files were detected",
            f"The scanner found {source_count} source files and no conventional test paths.",
            "Add focused tests around critical behavior; confirm custom test naming if used.",
            "medium"))

    packages = _dependencies(root)
    advisory = {"status": "not_run", "packages_checked": 0,
                "source": "https://osv.dev", "error": ""}
    if check_advisories:
        if packages:
            try:
                advisory_findings = _osv_findings(packages)
                findings.extend(advisory_findings)
                advisory.update(status="completed",
                                packages_checked=min(len(packages), MAX_OSV_PACKAGES))
            except (HTTPError, URLError, TimeoutError, OSError,
                    RuntimeError) as exc:
                error = _safe_error(exc)
                if isinstance(exc, HTTPError):
                    exc.close()
                advisory.update(status="unavailable", error=error)
        else:
            advisory.update(status="completed", packages_checked=0)

    findings.sort(key=lambda item: (
        SEVERITY_ORDER.get(item["severity"], 9), item["file"], item["line"],
        item["rule"]))
    _assign_stable_finding_ids(findings)
    _apply_triage(conn, findings)
    active_findings = [item for item in findings
                       if item["triage"]["effective_status"] == "open"]
    counts = Counter(item["severity"] for item in active_findings)
    raw_counts = Counter(item["severity"] for item in findings)
    category_counts = Counter(item["category"] for item in active_findings)
    triage_counts = Counter(item["triage"]["effective_status"]
                            for item in findings)
    fingerprint = structure.scan_fingerprint(conn, root) or _fallback_fingerprint(file_rows)
    duration_ms = int((time.monotonic() - started) * 1000)
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tree_fingerprint": fingerprint,
        "files_scanned": len(file_rows),
        "lines_scanned": total_lines,
        "skipped_large_files": skipped_large,
        "findings": findings,
        "active_findings": active_findings,
        "counts": {level: counts.get(level, 0)
                   for level in ("critical", "high", "medium", "low")},
        "raw_counts": {level: raw_counts.get(level, 0)
                       for level in ("critical", "high", "medium", "low")},
        "triage_counts": dict(sorted(triage_counts.items())),
        "category_counts": dict(sorted(category_counts.items())),
        "api": {
            "routes": routes,
            "route_count": len(routes),
            "auth_observed": sum(1 for row in routes if row["auth"] == "observed"),
            "auth_unknown": sum(1 for row in routes if row["auth"] == "unknown"),
        },
        "dependencies": {
            "detected": len(packages),
            "advisory_scan": advisory,
        },
        "quality": {
            "source_files": source_count,
            "test_files": test_count,
            "todo_markers": todos,
            "dependency_cycles": cycles,
            "languages": dict(languages.most_common()),
        },
        "recommendations": _recommendations(active_findings, routes, advisory),
        "duration_ms": duration_ms,
        "disclaimer": ("Heuristic findings require developer review. OSV matches are known "
                       "advisories for the detected package/version, not proof that a vulnerable "
                       "code path is reachable."),
    }
    cur = conn.execute(
        "INSERT INTO audit_runs(tree_fingerprint,files_scanned,lines_scanned,"
        "finding_count,result_json,duration_ms) VALUES(?,?,?,?,?,?)",
        (fingerprint, len(file_rows), total_lines, len(findings),
         json.dumps(report, ensure_ascii=False), duration_ms))
    report["audit_id"] = cur.lastrowid
    # Keep the persisted payload identical to the response, including its id.
    conn.execute("UPDATE audit_runs SET result_json=? WHERE audit_id=?",
                 (json.dumps(report, ensure_ascii=False), cur.lastrowid))
    conn.commit()
    return report


def latest(conn: sqlite3.Connection, root: Path) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM audit_runs ORDER BY audit_id DESC LIMIT 1").fetchone()
    if not row:
        return None
    try:
        value = json.loads(row["result_json"])
    except (TypeError, json.JSONDecodeError):
        return None
    current = structure.scan_fingerprint(conn, root)
    value["stale"] = bool(current and current != row["tree_fingerprint"])
    findings = value.get("findings")
    if isinstance(findings, list):
        _apply_triage(conn, findings)
        active = [item for item in findings
                  if item["triage"]["effective_status"] == "open"]
        value["active_findings"] = active
        counts = Counter(item.get("severity") for item in active)
        value["counts"] = {level: counts.get(level, 0)
                           for level in ("critical", "high", "medium", "low")}
        raw = Counter(item.get("severity") for item in findings)
        value["raw_counts"] = {level: raw.get(level, 0)
                               for level in ("critical", "high", "medium", "low")}
        value["triage_counts"] = dict(Counter(
            item["triage"]["effective_status"] for item in findings))
        value["category_counts"] = dict(sorted(Counter(
            item.get("category") for item in active).items()))
        routes = ((value.get("api") or {}).get("routes") or [])
        advisory = ((value.get("dependencies") or {}).get("advisory_scan") or
                    {"status": "not_run"})
        value["recommendations"] = _recommendations(active, routes, advisory)
    return value


TRIAGE_STATUSES = {"open", "resolved", "false-positive", "risk-accepted"}


def triage_finding(conn: sqlite3.Connection, root: Path, finding_id: str,
                   status: str, rationale: str = "",
                   expires_at: str = "") -> dict[str, Any]:
    """Persist a review decision for one stable audit finding."""
    finding_id = finding_id.strip()
    status = status.strip().lower()
    rationale = rationale.strip()
    expires_at = expires_at.strip()
    if not re.fullmatch(r"[0-9a-f]{12}", finding_id):
        raise ValueError("finding id must be a 12-character hexadecimal id")
    if status not in TRIAGE_STATUSES:
        raise ValueError("status must be open, resolved, false-positive, or risk-accepted")
    if status != "open" and not rationale:
        raise ValueError("a rationale is required when closing or accepting a finding")
    if len(rationale) > 5_000:
        raise ValueError("rationale must be at most 5000 characters")
    if expires_at:
        try:
            date.fromisoformat(expires_at)
        except ValueError:
            raise ValueError("expiry must be an ISO date such as 2026-12-31") from None
    report = latest(conn, root)
    if not report or not any(item.get("id") == finding_id
                             for item in report.get("findings", [])):
        raise ValueError("finding is not present in the latest audit")
    conn.execute(
        "INSERT INTO audit_triage(finding_id,status,rationale,expires_at,updated_at) "
        "VALUES(?,?,?,?,datetime('now')) ON CONFLICT(finding_id) DO UPDATE SET "
        "status=excluded.status,rationale=excluded.rationale,"
        "expires_at=excluded.expires_at,updated_at=datetime('now')",
        (finding_id, status, rationale, expires_at or None))
    conn.commit()
    refreshed = latest(conn, root)
    if refreshed is None:  # the audit row cannot disappear inside this write
        raise RuntimeError("latest audit disappeared while saving triage")
    item = next(value for value in refreshed["findings"]
                if value.get("id") == finding_id)
    return item


def sarif(report: dict[str, Any], *, include_triaged: bool = False) -> dict[str, Any]:
    """Convert an audit snapshot into SARIF 2.1.0 for CI/code-host UIs."""
    findings = report.get("findings", []) if include_triaged else (
        report.get("active_findings") or [])
    rules: dict[str, dict[str, Any]] = {}
    results = []
    levels = {"critical": "error", "high": "error", "medium": "warning",
              "low": "note"}
    for item in findings:
        rule = str(item.get("rule") or "irag.unknown")
        rules.setdefault(rule, {
            "id": rule,
            "shortDescription": {"text": str(item.get("title") or rule)[:1024]},
            "help": {"text": str(item.get("remediation") or
                                  "Review the evidence.")[:20_000]},
            "properties": {"category": item.get("category"),
                           "precision": item.get("confidence")},
        })
        location = {"physicalLocation": {
            "artifactLocation": {"uri": str(item.get("file") or ".")},
            "region": {"startLine": max(1, int(item.get("line") or 1))},
        }}
        result: dict[str, Any] = {
            "ruleId": rule,
            "level": levels.get(str(item.get("severity")), "warning"),
            "message": {"text": (f"{item.get('title', rule)}. "
                                 f"{item.get('evidence', '')} "
                                 f"Remediation: {item.get('remediation', '')}")[:20_000]},
            "locations": [location],
            "partialFingerprints": {"iragFindingId": item.get("id")},
            "properties": {"severity": item.get("severity"),
                           "confidence": item.get("confidence"),
                           "auditId": report.get("audit_id")},
        }
        if item.get("url"):
            result["properties"]["advisoryUrl"] = item["url"]
        results.append(result)
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {"name": "iRAG Code Audit",
                                "informationUri": "https://karang1908.github.io/irag/",
                                "rules": list(rules.values())}},
            "automationDetails": {"id": f"irag/audit/{report.get('audit_id', 'unknown')}"},
            "results": results,
        }],
    }


def _apply_triage(conn: sqlite3.Connection,
                  findings: list[dict[str, Any]]) -> None:
    rows = {row["finding_id"]: dict(row) for row in conn.execute(
        "SELECT finding_id,status,rationale,expires_at,updated_at FROM audit_triage")}
    today = date.today()
    for item in findings:
        stored = rows.get(item.get("id"))
        status = str((stored or {}).get("status") or "open")
        expires = str((stored or {}).get("expires_at") or "")
        expired = False
        if expires:
            try:
                expired = date.fromisoformat(expires) < today
            except ValueError:
                expired = True
        effective = "open" if expired else status
        item["triage"] = {
            "status": status,
            "effective_status": effective,
            "rationale": str((stored or {}).get("rationale") or ""),
            "expires_at": expires,
            "expired": expired,
            "updated_at": (stored or {}).get("updated_at"),
        }


def check_local_api(base_url: str, routes: list[dict], *,
                    timeout: int = 3) -> dict[str, Any]:
    """Safely probe discovered read-only routes on a loopback server.

    Only GET/HEAD endpoints without path parameters are eligible. Redirects
    are not followed, which prevents a local endpoint from bouncing the
    checker into an internal or public network target.
    """
    base = base_url.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("base URL must be http(s), for example http://127.0.0.1:3000")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, a query, or a fragment")
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        if host.lower() != "localhost":
            raise ValueError(
                "live API checks require localhost or a literal loopback IP") from None
    else:
        if not literal.is_loopback:
            raise ValueError("live API checks are restricted to loopback hosts")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid local API port: {exc}") from None
    try:
        addresses = socket.getaddrinfo(host, port,
                                       type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError(f"could not resolve local API host: {exc}") from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_loopback
                            for item in addresses):
        raise ValueError("live API checks are restricted to loopback hosts")
    candidates = []
    seen = set()
    for route in routes:
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if method not in ("GET", "HEAD", "ANY"):
            continue
        if not path.startswith("/") or any(token in path for token in
                                            ("<", ">", "{", "}", "*", ":")):
            continue
        key = (("HEAD" if method == "HEAD" else "GET"), path)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(key)
    if not candidates:
        candidates = [("GET", "/")]
    candidates = candidates[:30]

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = build_opener(_NoRedirect)

    def probe(item: tuple[str, str]) -> dict:
        method, path = item
        url = urljoin(base + "/", path.lstrip("/"))
        started = time.monotonic()
        status = None
        error = ""
        try:
            request = Request(url, method=method, headers={
                "User-Agent": "iRAG-local-api-check/1",
                "Accept": "application/json,text/plain,*/*",
            })
            with opener.open(request, timeout=timeout) as response:
                status = int(response.status)
                response.read(1024)
        except HTTPError as exc:
            status = int(exc.code)
            exc.close()
        except (URLError, TimeoutError, OSError) as exc:
            error = str(getattr(exc, "reason", exc))[:300]
        duration = int((time.monotonic() - started) * 1000)
        if error:
            outcome = "unreachable"
        elif status is not None and 200 <= status < 300:
            outcome = "healthy"
        elif status in (401, 403):
            outcome = "protected"
        elif status is not None and 300 <= status < 400:
            outcome = "redirect"
        elif status is not None and status >= 500:
            outcome = "server_error"
        else:
            outcome = "response"
        return {"method": method, "path": path, "url": url,
                "status": status, "outcome": outcome,
                "duration_ms": duration, "error": error}

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as pool:
        checks = list(pool.map(probe, candidates))
    return {
        "base_url": base,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": checks,
        "counts": dict(Counter(item["outcome"] for item in checks)),
        "note": "Only discovered, parameter-free GET/HEAD routes were called; redirects were not followed.",
    }


def _iter_text_files(root: Path, cfg: dict):
    """Walk deterministically while pruning ignored trees before descent."""
    count = 0
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        directory = Path(current)
        rel_dir = directory.relative_to(root)
        dirs[:] = [name for name in sorted(dirs)
                   if not ingest.is_ignored(
                       (rel_dir / name).as_posix(), cfg, root)]
        for name in sorted(files):
            if count >= MAX_FILES:
                return
            path = directory / name
            rel = path.relative_to(root).as_posix()
            if ingest.is_ignored(rel, cfg, root):
                continue
            if (path.suffix.lower() not in TEXT_EXT and
                    path.name not in SPECIAL_FILES):
                continue
            count += 1
            yield path, rel


def _text_security(rel: str, lines: list[str], *,
                   test_file: bool = False) -> list[dict]:
    out = []
    for no, line in enumerate(lines, 1):
        match = SECRET_RE.search(line)
        if match and not _placeholder(match.group(2)) and not re.search(
                r"(?i)(os\.environ|process\.env|getenv|secret[_-]?manager|"
                r"example|fixture|mock)", line):
            severity = "low" if test_file else "high"
            out.append(_finding(
                "security.hardcoded-secret", severity, "security", rel, no,
                f"Possible hard-coded {match.group(1).replace('_', ' ')}"
                + (" in a test fixture" if test_file else ""),
                line[:match.start(2)] + "***REDACTED***",
                ("Confirm this is synthetic fixture data and cannot be reused as a real credential."
                 if test_file else
                 "Move the value to an environment variable or secret manager and rotate it if real."),
                "low" if test_file else "medium"))
        if PRIVATE_KEY_RE.search(line):
            out.append(_finding(
                "security.private-key", "critical", "security", rel, no,
                "Private-key material appears in a scanned file",
                "Private key header detected; key contents were not retained.",
                "Remove it from history, rotate the key, and load it from protected storage.",
                "high"))
        patterns = (
            (r"\bshell\s*=\s*True\b", "security.shell-true", "high",
             "Subprocess enables a command shell", "Pass an argument array and keep shell=False."),
            (r"\bos\.system\s*\(", "security.os-system", "high",
             "os.system executes through a shell", "Use subprocess with an argument array and validated inputs."),
            (r"\b(?:eval|exec)\s*\(", "security.dynamic-exec", "medium",
             "Dynamic code execution detected", "Remove dynamic execution or strictly constrain the input."),
            (r"\bpickle\.(?:load|loads)\s*\(", "security.pickle", "high",
             "Pickle deserialization can execute code", "Never deserialize pickle data from an untrusted boundary."),
            (r"\bverify\s*=\s*False\b", "security.tls-disabled", "high",
             "TLS certificate verification appears disabled", "Restore certificate verification; install the required CA instead."),
            (r"(?i)Access-Control-Allow-Origin['\"\s:=>]+\*", "security.cors-wildcard", "medium",
             "Wildcard CORS origin detected", "Restrict CORS to the exact trusted origins."),
            (r"\bdebug\s*=\s*True\b", "security.debug", "medium",
             "Debug mode appears enabled", "Ensure debug mode cannot be enabled in deployed environments."),
            (r"\bchmod\s+777\b|\bos\.chmod\([^\n]*0?777", "security.world-writable", "high",
             "World-writable permissions detected", "Use the narrowest owner/group permissions required."),
        )
        for pattern, rule, severity, title, remediation in patterns:
            if re.search(pattern, line) and not _suppressed(lines, no, rule):
                fixture = test_file or _is_vendored(rel)
                actual_severity = "low" if fixture else severity
                actual_title = title + (" in test/vendor code" if fixture else "")
                out.append(_finding(
                    rule, actual_severity, "security", rel, no,
                    actual_title, _redact(line), remediation,
                    "medium" if fixture else "high"))
    return out


def _python_checks(rel: str, text: str) -> list[dict]:
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [_finding(
            "quality.python-syntax", "high", "quality", rel,
            int(exc.lineno or 1), "Python syntax error",
            str(exc.msg), "Fix the syntax error before relying on analysis or deployment.",
            "high")]
    lines = text.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = int(getattr(node, "end_lineno", node.lineno))
            length = end - int(node.lineno) + 1
            if length > 140:
                out.append(_finding(
                    "quality.long-function", "low", "quality", rel, node.lineno,
                    f"Function {node.name} spans {length} lines",
                    _line_at(lines, node.lineno),
                    "Extract coherent responsibilities when it improves testing and readability.",
                    "high"))
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            out.append(_finding(
                "quality.bare-except", "medium", "quality", rel, node.lineno,
                "Bare except catches process-control exceptions",
                _line_at(lines, node.lineno),
                "Catch the narrow exception types the operation can recover from.", "high"))
        elif isinstance(node, ast.Call):
            name = _call_name(node.func)
            if name == "yaml.load" and not any(k.arg == "Loader" for k in node.keywords):
                out.append(_finding(
                    "security.yaml-load", "high", "security", rel, node.lineno,
                    "yaml.load has no explicit safe loader",
                    _line_at(lines, node.lineno),
                    "Use yaml.safe_load for untrusted YAML.", "high"))
            if name in ("hashlib.md5", "hashlib.sha1"):
                out.append(_finding(
                    "security.weak-hash", "low", "security", rel, node.lineno,
                    f"{name} is not collision resistant",
                    _line_at(lines, node.lineno),
                    "Use SHA-256+ for security decisions; keep only if this is a non-security fingerprint.",
                    "medium"))
    return out


def _routes(rel: str, lines: list[str]) -> list[dict]:
    out = []
    handler_method: str | None = None
    handler_indent = -1
    route_indent: int | None = None
    for index, line in enumerate(lines):
        definition = re.match(r"^(\s*)def\s+(do_(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)|\w+)\s*\(",
                              line, re.I)
        if definition:
            indent = len(definition.group(1))
            if handler_method and indent <= handler_indent:
                handler_method = None
            if definition.group(2).lower().startswith("do_"):
                handler_method = definition.group(3).upper()
                handler_indent = indent
                route_indent = None
        if handler_method:
            indent = len(line) - len(line.lstrip())
            match = re.search(
                r"\b(?:self\.path|url\.path|parsed\.path)\s*==\s*['\"]([^'\"]+)['\"]",
                line)
            if match and (route_indent is None or indent <= route_indent):
                route_indent = indent
                out.append(_route_row(
                    handler_method, match.group(1), rel, index, lines))
            membership = re.search(
                r"\b(?:self\.path|url\.path|parsed\.path)\s+in\s+\(([^)]*)\)",
                line)
            if membership and (route_indent is None or indent <= route_indent):
                route_indent = indent
                for path in re.findall(r"['\"]([^'\"]+)['\"]",
                                       membership.group(1)):
                    out.append(_route_row(
                        handler_method, path, rel, index, lines))

        # Django/Starlette URL tables carry a path but not always a method.
        # Keep them as ANY: useful for contract coverage, never safe to call.
        match = re.search(r"\bpath\(\s*['\"]([^'\"]+)['\"]\s*,", line)
        if match:
            path = match.group(1)
            out.append(_route_row(
                "ANY", path if path.startswith("/") else "/" + path,
                rel, index, lines))

        # Spring annotations encode the verb in GetMapping/PostMapping.
        match = re.search(
            r"@(Get|Post|Put|Patch|Delete|Options|Head)Mapping\s*\(\s*"
            r"(?:value\s*=\s*)?['\"]([^'\"]+)['\"]", line, re.I)
        if match:
            out.append(_route_row(
                match.group(1), match.group(2), rel, index, lines))

        if Path(rel).suffix.lower() == ".go":
            match = re.search(
                r"\b\w+\.(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)"
                r"\(\s*['\"]([^'\"]+)['\"]", line)
            if match:
                out.append(_route_row(
                    match.group(1), match.group(2), rel, index, lines))
            match = re.search(
                r"\b(?:http\.)?HandleFunc\(\s*['\"]([^'\"]+)['\"]", line)
            if match:
                methods = re.findall(
                    r"\.Methods\(\s*['\"](GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)['\"]",
                    line, re.I) or ["ANY"]
                for method in methods:
                    out.append(_route_row(
                        method, match.group(1), rel, index, lines))

        for pindex, pattern in enumerate(ROUTE_PATTERNS):
            match = pattern.search(line)
            if not match:
                continue
            if pindex == 1:  # Flask-style route(path, methods=[...])
                path = match.group(1)
                methods = re.findall(r"['\"](GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)['\"]",
                                     match.group(2), re.I) or ["ANY"]
            else:
                methods = [match.group(1).upper()]
                path = match.group(2)
            context = "\n".join(lines[max(0, index - 4):index + 10])
            auth = "observed" if AUTH_RE.search(context) else "unknown"
            for method in methods:
                out.append({"method": method.upper(), "path": path[:1000],
                            "file": rel, "line": index + 1, "auth": auth})
            break

    next_path = _next_api_path(rel)
    if next_path:
        for index, line in enumerate(lines):
            match = re.search(
                r"\b(?:export\s+)?(?:async\s+)?(?:function|const)\s+"
                r"(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\b", line)
            if match:
                out.append(_route_row(
                    match.group(1), next_path, rel, index, lines))
    elif _pages_api_path(rel):
        out.append(_route_row("ANY", _pages_api_path(rel) or "/api",
                              rel, 0, lines))

    unique = {}
    for item in out:
        key = (item["method"], item["path"], item["file"], item["line"])
        unique[key] = item
    return list(unique.values())


def _route_row(method: str, path: str, rel: str, index: int,
               lines: list[str]) -> dict:
    if path and not path.startswith("/"):
        path = "/" + path
    context = "\n".join(lines[max(0, index - 4):index + 10])
    return {"method": method.upper(), "path": path[:1000], "file": rel,
            "line": index + 1,
            "auth": "observed" if AUTH_RE.search(context) else "unknown"}


def _next_api_path(rel: str) -> str | None:
    parts = list(Path(rel).as_posix().split("/"))
    if not parts or not re.fullmatch(r"route\.[cm]?[jt]sx?", parts[-1], re.I):
        return None
    for index in range(len(parts) - 2):
        if parts[index:index + 2] == ["app", "api"]:
            segments = [part for part in parts[index + 2:-1]
                        if not (part.startswith("(") and part.endswith(")"))]
            return "/api" + ("/" + "/".join(segments) if segments else "")
    return None


def _pages_api_path(rel: str) -> str | None:
    parts = list(Path(rel).as_posix().split("/"))
    for index in range(len(parts) - 1):
        if parts[index:index + 2] != ["pages", "api"]:
            continue
        segments = parts[index + 2:]
        if not segments or Path(segments[-1]).suffix.lower() not in {
                ".js", ".jsx", ".ts", ".tsx"}:
            return None
        segments[-1] = Path(segments[-1]).stem
        if segments[-1] == "index":
            segments.pop()
        return "/api" + ("/" + "/".join(segments) if segments else "")
    return None


def _api_findings(routes: list[dict]) -> list[dict]:
    out = []
    by_key: dict[tuple[str, str], list[dict]] = {}
    for route in routes:
        by_key.setdefault((route["method"], route["path"]), []).append(route)
    for (method, path), rows in by_key.items():
        if len(rows) > 1:
            first = rows[0]
            out.append(_finding(
                "api.duplicate-route", "medium", "api", first["file"],
                first["line"], f"Duplicate {method} {path} route",
                ", ".join(f"{row['file']}:{row['line']}" for row in rows),
                "Confirm routing order and consolidate or make the paths distinct.", "high"))
    sensitive = re.compile(r"(?i)(admin|debug|internal|delete|reset|token|secret|billing)")
    for route in routes:
        if route["auth"] == "unknown" and sensitive.search(route["path"]):
            fixture = _is_test(route["file"])
            out.append(_finding(
                "api.auth-review", "low" if fixture else "medium", "api",
                route["file"], route["line"],
                f"Review access control for {route['method']} {route['path']}"
                + (" in test fixture" if fixture else ""),
                "No nearby authentication marker was recognized. This is a heuristic, not proof of missing auth.",
                "Verify authorization at the route, router, gateway, or middleware layer.", "medium"))
    return out


def _dependency_cycles(conn: sqlite3.Connection) -> list[list[str]]:
    graph: dict[str, set[str]] = {}
    for row in conn.execute("SELECT source_subject,target_subject FROM deps"):
        graph.setdefault(row["source_subject"], set()).add(row["target_subject"])
        graph.setdefault(row["target_subject"], set())
    # Iterative Kosaraju avoids Python's recursion ceiling on ordinary large
    # repositories (a 2,000-module chain is not an exceptional codebase).
    order: list[str] = []
    seen: set[str] = set()
    for start in sorted(graph):
        if start in seen:
            continue
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            node, expanded = stack.pop()
            if expanded:
                order.append(node)
                continue
            if node in seen:
                continue
            seen.add(node)
            stack.append((node, True))
            stack.extend((target, False)
                         for target in reversed(sorted(graph.get(node, ())))
                         if target not in seen)

    reverse: dict[str, set[str]] = {node: set() for node in graph}
    for source, targets in graph.items():
        for target in targets:
            reverse[target].add(source)
    assigned: set[str] = set()
    components: list[list[str]] = []
    for start in reversed(order):
        if start in assigned:
            continue
        component = []
        component_stack = [start]
        assigned.add(start)
        while component_stack:
            node = component_stack.pop()
            component.append(node)
            for source in reverse[node]:
                if source not in assigned:
                    assigned.add(source)
                    component_stack.append(source)
        if (len(component) > 1 or
                (component and component[0] in graph[component[0]])):
            components.append(sorted(component))
    return sorted(components, key=lambda value: (-len(value), value))


def _tracked_environment_files(root: Path) -> list[dict]:
    from . import ingest as ingest_mod
    if not ingest_mod.git_rooted(root):
        return []
    import subprocess
    try:
        result = subprocess.run(["git", "ls-files", "--", ".env", "*.pem",
                                 "*.key", "id_rsa", "id_ed25519"], cwd=root,
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return []
    out = []
    for rel in result.stdout.splitlines():
        rel = rel.strip()
        if rel:
            out.append(_finding(
                "security.sensitive-file-tracked", "high", "security", rel, 1,
                "Sensitive-looking file is tracked by Git",
                "Only the file name was inspected; contents were not read by this check.",
                "Confirm the file is safe. If it contained credentials, remove it from history and rotate them.",
                "high"))
    return out


def _dependencies(root: Path) -> list[dict]:
    values: dict[tuple[str, str, str], dict] = {}

    def add(ecosystem: str, name: object, version: object, source: str) -> None:
        package = str(name or "").strip()
        ver = str(version or "").strip().lstrip("v=")
        if not package or not ver or any(char in ver for char in "*<>, "):
            return
        key = ecosystem, package.lower(), ver
        values[key] = {"ecosystem": ecosystem, "name": package,
                       "version": ver, "source": source}

    for path in sorted(root.glob("requirements*.txt")):
        try:
            for line in _dependency_text(path).splitlines():
                match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*==\s*([^;#\s]+)", line)
                if match:
                    add("PyPI", match.group(1), match.group(2), path.name)
        except OSError:
            pass
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            data = tomllib.loads(_dependency_text(pyproject))
            deps = data.get("project", {}).get("dependencies", [])
            for item in deps if isinstance(deps, list) else []:
                match = re.match(r"\s*([A-Za-z0-9_.-]+)\s*==\s*([^;\s]+)", str(item))
                if match:
                    add("PyPI", match.group(1), match.group(2), "pyproject.toml")
        except (OSError, tomllib.TOMLDecodeError):
            pass
    lock = root / "package-lock.json"
    if lock.is_file():
        try:
            data = json.loads(_dependency_text(lock))
            packages = data.get("packages", {})
            if isinstance(packages, dict):
                for key, info in packages.items():
                    if not key or not isinstance(info, dict):
                        continue
                    name = info.get("name") or key.rsplit("node_modules/", 1)[-1]
                    add("npm", name, info.get("version"), "package-lock.json")
        except (OSError, json.JSONDecodeError):
            pass
    cargo = root / "Cargo.lock"
    if cargo.is_file():
        try:
            data = tomllib.loads(_dependency_text(cargo))
            for item in data.get("package", []):
                if isinstance(item, dict):
                    add("crates.io", item.get("name"), item.get("version"), "Cargo.lock")
        except (OSError, tomllib.TOMLDecodeError):
            pass
    gosum = root / "go.sum"
    if gosum.is_file():
        try:
            for line in _dependency_text(gosum).splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    add("Go", parts[0], parts[1].removesuffix("/go.mod"), "go.sum")
        except OSError:
            pass
    composer = root / "composer.lock"
    if composer.is_file():
        try:
            data = json.loads(_dependency_text(composer))
            for item in data.get("packages", []):
                if isinstance(item, dict):
                    add("Packagist", item.get("name"), item.get("version"), "composer.lock")
        except (OSError, json.JSONDecodeError):
            pass
    gem = root / "Gemfile.lock"
    if gem.is_file():
        try:
            in_specs = False
            for line in _dependency_text(gem).splitlines():
                if line == "  specs:":
                    in_specs = True
                    continue
                if in_specs and line and not line.startswith("    "):
                    in_specs = False
                if in_specs:
                    match = re.match(r"    ([A-Za-z0-9_.-]+) \(([^ )]+)", line)
                    if match:
                        add("RubyGems", match.group(1), match.group(2), "Gemfile.lock")
        except OSError:
            pass
    return list(values.values())[:MAX_OSV_PACKAGES]


def _dependency_text(path: Path) -> str:
    if path.stat().st_size > 20_000_000:
        raise OSError(f"dependency file too large: {path.name}")
    return path.read_text(encoding="utf-8", errors="replace")


def _osv_findings(packages: list[dict]) -> list[dict]:
    selected = packages[:MAX_OSV_PACKAGES]
    payload = {"queries": [
        {"version": item["version"], "package": {
            "name": item["name"], "ecosystem": item["ecosystem"]}}
        for item in selected
    ]}
    request = Request("https://api.osv.dev/v1/querybatch",
                      data=json.dumps(payload).encode("utf-8"), headers={
                          "Content-Type": "application/json",
                          "User-Agent": "iRAG/4 (+https://github.com/Karang1908/irag)",
                      })
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed OSV endpoint
        raw = response.read(8_000_001)
    if len(raw) > 8_000_000:
        raise RuntimeError("OSV response exceeded 8 MB")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise RuntimeError("OSV returned invalid JSON") from None
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or len(results) != len(selected):
        raise RuntimeError("OSV returned an unexpected result set")
    out = []
    for package, result in zip(selected, results):
        vulns = result.get("vulns", []) if isinstance(result, dict) else []
        for vuln in vulns[:50]:
            vid = str(vuln.get("id") or "") if isinstance(vuln, dict) else ""
            if not vid:
                continue
            out.append(_finding(
                "dependency.osv-advisory", "medium", "dependency",
                package["source"], 1,
                f"Known advisory {vid}: {package['name']} {package['version']}",
                f"OSV matched the exact detected {package['ecosystem']} package version.",
                "Open the OSV record, assess reachability, and upgrade to an unaffected version.",
                "high", url=f"https://osv.dev/vulnerability/{vid}"))
    return out


def _finding(rule: str, severity: str, category: str, file: str, line: int,
             title: str, evidence: str, remediation: str, confidence: str,
             *, url: str = "") -> dict:
    return {"id": "", "rule": rule, "severity": severity,
            "category": category, "file": file, "line": int(line),
            "title": title, "evidence": evidence[:2000],
            "remediation": remediation, "confidence": confidence,
            "url": url}


def _assign_stable_finding_ids(findings: list[dict]) -> None:
    """Identify evidence across harmless line moves, without collisions.

    Line numbers are locations, not identities: inserting a comment above a
    finding must not discard a developer's review. The occurrence ordinal only
    disambiguates identical evidence repeated within one file and remains
    stable when later duplicates are appended.
    """
    seen: Counter[str] = Counter()
    for item in findings:
        evidence = " ".join(str(item.get("evidence") or "").split())
        identity = "\0".join((
            str(item.get("rule") or ""), str(item.get("file") or ""),
            str(item.get("title") or ""), evidence))
        seen[identity] += 1
        item["id"] = hashlib.sha256(
            f"{identity}\0{seen[identity]}".encode("utf-8")).hexdigest()[:12]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _line_at(lines: list[str], line: int) -> str:
    return _redact(lines[line - 1] if 0 < line <= len(lines) else "")


def _redact(line: str) -> str:
    return SECRET_RE.sub(lambda match: match.group(0)[:match.start(2) - match.start(0)]
                         + "***REDACTED***" + match.group(0)[match.end(2) - match.start(0):],
                         line.strip())[:1000]


def _placeholder(value: str) -> bool:
    lower = value.lower().strip()
    return (len(set(lower)) <= 3 or any(word in lower for word in (
        "example", "sample", "placeholder", "changeme", "replace", "dummy",
        "fake", "mock", "test", "your_", "your-", "xxxx")))


def _is_test(rel: str) -> bool:
    lower = rel.lower()
    name = Path(lower).name
    return ("/test/" in f"/{lower}/" or "/tests/" in f"/{lower}/" or
            name.startswith("test_") or ".test." in name or ".spec." in name)


def _is_vendored(rel: str) -> bool:
    parts = {part.lower() for part in Path(rel).parts}
    return bool(parts & {"vendor", "vendors", "third_party", "third-party"})


def _suppressed(lines: list[str], line_number: int, rule: str) -> bool:
    """Honor one explicit, reviewable inline suppression.

    The exact rule name is required so a broad comment cannot accidentally
    silence unrelated checks. The previous line form leaves room for a human
    rationale beside the marker.
    """
    marker = f"irag-audit: allow {rule}"
    current = lines[line_number - 1] if 0 < line_number <= len(lines) else ""
    previous = lines[line_number - 2] if line_number > 1 else ""
    return marker in current or marker in previous


def _safe_error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"OSV HTTP {exc.code}"
    reason = getattr(exc, "reason", exc)
    return str(reason)[:300]


def _fallback_fingerprint(rows: list[tuple[Path, str, str]]) -> str:
    value = hashlib.sha1()
    for _, rel, text in rows:
        value.update(rel.encode("utf-8"))
        value.update(hashlib.sha1(text.encode("utf-8")).digest())
    return value.hexdigest()


def _recommendations(findings: list[dict], routes: list[dict],
                     advisory: dict) -> list[dict]:
    categories = Counter(item["category"] for item in findings)
    out = []
    high = sum(1 for item in findings if item["severity"] in ("critical", "high"))
    if high:
        out.append({"priority": "now", "title": "Triage high-severity evidence",
                    "detail": f"Review {high} critical/high finding(s), starting with exposed credentials and unsafe execution."})
    if categories["dependency"]:
        out.append({"priority": "next", "title": "Resolve known dependency advisories",
                    "detail": "Verify reachability, update lockfiles, and rerun tests plus this audit."})
    if categories["api"]:
        out.append({"priority": "next", "title": "Verify API boundaries",
                    "detail": "Confirm route-level or upstream authorization and remove ambiguous duplicates."})
    if routes and not any(route["auth"] == "observed" for route in routes):
        out.append({"priority": "review", "title": "Document the API auth boundary",
                    "detail": "No route-adjacent auth markers were recognized; record where authorization is enforced."})
    if advisory["status"] == "unavailable":
        out.append({"priority": "retry", "title": "Repeat the advisory lookup online",
                    "detail": "The local audit completed, but OSV was unavailable, so dependency vulnerabilities are unknown."})
    if not out:
        out.append({"priority": "maintain", "title": "Keep the baseline current",
                    "detail": "No actionable patterns were found. Rerun after dependency or API changes."})
    return out[:6]
