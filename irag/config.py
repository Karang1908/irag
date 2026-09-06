"""irag.config — configuration loading and defaults.

Configuration lives at ``.irag/config.toml`` in the repo root. Values are
deep-merged over the built-in defaults, so a config file may specify only
the keys it wants to override.
"""
from __future__ import annotations

import copy
import json
import re
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_TOML = '''[ingest]
mode = "auto"               # auto | git | snapshot (auto = git if present)

[llm]
provider = "claude"         # claude | codex | agy | ollama | custom
command = "claude -p"       # prompt on stdin, markdown on stdout
model = ""                  # optional provider model (required by ollama)
model_label = "claude"
timeout = 300
retries = 1                 # retry transient exit/timeout/empty-output failures
input_cost_per_million = 0.0   # optional; enables local cost estimates
output_cost_per_million = 0.0

[web]
enabled = true
provider = "auto"          # auto | brave | tavily | searxng | duckduckgo
endpoint = ""              # SearXNG base URL; secrets stay in environment vars
max_results = 6
timeout = 12

[modules]
# every non-ignored file gets its own page; every folder gets a rollup.
# 'ignore' entries match any path segment; add a .iragignore file at the
# project root for gitignore-style patterns (names, prefixes, globs).
ignore = [".irag", ".git", ".claude", "irag_vault", "node_modules", "venv", ".venv", "dist", "build", "__pycache__", ".idea", ".vscode"]

[staleness]
commit = 10
dependency = 20
threshold = 1        # 1 = resynthesize on every change (raise to batch, e.g. 100)

[retrieval]
token_budget = 8000
full_max = 4
min_score = 20

[sessions]
# store the verbatim conversation (your prompts + the agent's replies) in
# the project diary, not just the file changes it made. Off by default:
# transcripts can contain secrets. When true, SessionEnd ingests the
# Claude Code transcript; other agents pass one with 'session-end
# --transcript <file.jsonl>'. Read it back with 'irag transcript <id>'.
capture_transcript = false
max_messages = 400          # keep at most this many messages per session
max_message_chars = 4000    # truncate any single message to this many chars

[check]
max_staleness = 150
fail_on_contradictions = true
fail_on_staleness = true      # set false to let 'irag check' pass despite stale pages
fail_on_unsynthesized = true  # a page with no version at all means synthesis never ran
fail_on_facts = false         # OFF by default: these are shell commands stored in the
                              # memory database, which travels with the repo. Turning this
                              # on means `irag check` executes them — only do that in a
                              # repo whose registered commands you vouch for.
'''

DEFAULTS: dict[str, Any] = {
    "ingest": {"mode": "auto"},
    "llm": {
        "provider": "claude",
        "command": "claude -p",
        "model": "",
        "model_label": "claude",
        "timeout": 300,
        "retries": 1,
        "input_cost_per_million": 0.0,
        "output_cost_per_million": 0.0,
        # how many pages to synthesize at once. 1 keeps the old strictly
        # sequential behaviour; raise it if your LLM CLI tolerates
        # concurrent invocations (most do — each is its own process).
        "parallel": 1,
    },
    "web": {
        "enabled": True,
        "provider": "auto",
        "endpoint": "",
        "max_results": 6,
        "timeout": 12,
    },
    "modules": {
        "ignore": [".irag", ".git", ".claude", "irag_vault", "node_modules",
                   "venv", ".venv", "dist", "build", "__pycache__",
                   ".idea", ".vscode"],
    },
    "staleness": {
        "commit": 10,
        "dependency": 20,
        "threshold": 1,
        # skip the model when only comments/whitespace changed
        "skip_trivial": True,
    },
    "retrieval": {
        "token_budget": 8000,
        "full_max": 4,
        "min_score": 20,
    },
    "sessions": {
        "capture_transcript": False,
        "max_messages": 400,
        "max_message_chars": 4000,
    },
    "check": {
        "max_staleness": 150,
        "fail_on_contradictions": True,
        "fail_on_staleness": True,
        "fail_on_unsynthesized": True,
        "fail_on_facts": False,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict with ``override`` recursively merged over ``base``."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def config_path(repo_root: Path) -> Path:
    return repo_root / ".irag" / "config.toml"


def load(repo_root: Path) -> dict[str, Any]:
    """Load config for the repo, merging the TOML file over defaults."""
    path = config_path(repo_root)
    # deep-copy so nested dicts/lists are never shared with the module-level
    # DEFAULTS (a mutation of one repo's cfg must not leak into another's)
    if not path.exists():
        merged = copy.deepcopy(DEFAULTS)
        merged["_runtime"] = {"root": str(repo_root)}
        return merged
    try:
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"irag: invalid TOML in {path}: {exc}") from exc
    merged = _deep_merge(copy.deepcopy(DEFAULTS), user)
    # Configs written before provider adapters existed are deliberately
    # interpreted as custom commands. Silently treating an old `agy ...` or
    # wrapper command as Claude would be a backwards-incompatible change.
    user_llm = user.get("llm") if isinstance(user, dict) else None
    if isinstance(user_llm, dict) and "provider" not in user_llm:
        merged["llm"]["provider"] = "custom"
    errors = validation_errors(merged)
    if errors:
        detail = "; ".join(errors)
        raise SystemExit(f"irag: invalid config in {path}: {detail}")
    merged["_runtime"] = {"root": str(repo_root)}
    return merged


def validation_errors(cfg: object) -> list[str]:
    """Return actionable semantic errors for a merged iRAG config.

    Parsing TOML only proves syntax.  This guard prevents valid TOML with
    incompatible values from reaching dashboard workers or being interpreted
    differently by separate commands.  Unknown keys remain allowed for
    forwards compatibility.
    """
    if not isinstance(cfg, dict):
        return ["top-level value must be a table"]

    errors: list[str] = []

    def section(name: str) -> dict[str, Any] | None:
        value = cfg.get(name)
        if not isinstance(value, dict):
            errors.append(f"[{name}] must be a table")
            return None
        return value

    def expect(
        table: dict[str, Any] | None,
        section_name: str,
        key: str,
        expected: type,
        *,
        minimum: int | None = None,
        maximum: int | None = None,
        nonempty: bool = False,
    ) -> Any:
        if table is None or key not in table:
            return None
        value = table[key]
        valid = (
            type(value) is expected
            if expected in (bool, int)
            else isinstance(value, expected)
        )
        if not valid:
            errors.append(f"[{section_name}].{key} must be {expected.__name__}")
            return None
        if minimum is not None and value < minimum:
            errors.append(f"[{section_name}].{key} must be at least {minimum}")
        if maximum is not None and value > maximum:
            errors.append(f"[{section_name}].{key} must be at most {maximum}")
        if nonempty and isinstance(value, str) and not value.strip():
            errors.append(f"[{section_name}].{key} must not be empty")
        return value

    ingest = section("ingest")
    mode = expect(ingest, "ingest", "mode", str, nonempty=True)
    if isinstance(mode, str) and mode not in {"auto", "git", "snapshot"}:
        errors.append("[ingest].mode must be one of: auto, git, snapshot")

    llm = section("llm")
    provider = expect(llm, "llm", "provider", str, nonempty=True)
    if isinstance(provider, str) and provider.lower() not in {
            "claude", "codex", "agy", "ollama", "custom"}:
        errors.append("[llm].provider must be one of: claude, codex, agy, "
                      "ollama, custom")
    command = expect(llm, "llm", "command", str)
    if (isinstance(provider, str) and provider.lower() == "custom"
            and isinstance(command, str) and not command.strip()):
        errors.append("[llm].command must not be empty for provider=custom")
    expect(llm, "llm", "model", str)
    expect(llm, "llm", "model_label", str, nonempty=True)
    expect(llm, "llm", "timeout", int, minimum=1, maximum=3600)
    expect(llm, "llm", "retries", int, minimum=0, maximum=10)
    expect(llm, "llm", "parallel", int, minimum=1, maximum=32)
    for key in ("input_cost_per_million", "output_cost_per_million"):
        value = llm.get(key) if llm else None
        if value is not None and (type(value) not in (int, float) or value < 0):
            errors.append(f"[llm].{key} must be a non-negative number")

    web = section("web")
    expect(web, "web", "enabled", bool)
    web_provider = expect(web, "web", "provider", str, nonempty=True)
    if isinstance(web_provider, str) and web_provider.lower() not in {
            "auto", "brave", "tavily", "searxng", "duckduckgo"}:
        errors.append("[web].provider must be one of: auto, brave, tavily, "
                      "searxng, duckduckgo")
    endpoint = expect(web, "web", "endpoint", str)
    if (isinstance(web_provider, str) and web_provider.lower() == "searxng"
            and isinstance(endpoint, str) and not endpoint.strip()):
        errors.append("[web].endpoint must not be empty for provider=searxng")
    expect(web, "web", "max_results", int, minimum=1, maximum=10)
    expect(web, "web", "timeout", int, minimum=2, maximum=60)

    modules = section("modules")
    if modules is not None and "ignore" in modules:
        ignore = modules["ignore"]
        if not isinstance(ignore, list) or any(not isinstance(item, str) for item in ignore):
            errors.append("[modules].ignore must be an array of strings")

    staleness = section("staleness")
    expect(staleness, "staleness", "commit", int, minimum=0)
    expect(staleness, "staleness", "dependency", int, minimum=0)
    expect(staleness, "staleness", "threshold", int, minimum=1)
    expect(staleness, "staleness", "skip_trivial", bool)

    retrieval = section("retrieval")
    expect(retrieval, "retrieval", "token_budget", int, minimum=0)
    expect(retrieval, "retrieval", "min_score", int)
    expect(retrieval, "retrieval", "full_max", int, minimum=0)

    sessions = section("sessions")
    expect(sessions, "sessions", "capture_transcript", bool)
    expect(sessions, "sessions", "max_messages", int, minimum=1)
    expect(sessions, "sessions", "max_message_chars", int, minimum=1)

    check = section("check")
    expect(check, "check", "max_staleness", int, minimum=0)
    for key in (
        "fail_on_contradictions",
        "fail_on_staleness",
        "fail_on_unsynthesized",
        "fail_on_facts",
    ):
        expect(check, "check", key, bool)

    return errors


def write_default(repo_root: Path) -> Path:
    """Write the default config file if it does not exist. Returns its path."""
    path = config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        # A first-run interruption must never leave valid-looking partial TOML,
        # and concurrent init processes may both observe the file as absent.
        # They write identical defaults through unique atomic temp files.
        from .export import _atomic_write
        _atomic_write(path, DEFAULT_TOML)
    return path


_EDITABLE: dict[str, dict[str, type]] = {
    "llm": {
        "provider": str,
        "command": str,
        "model": str,
        "model_label": str,
        "timeout": int,
        "retries": int,
        "parallel": int,
        "input_cost_per_million": float,
        "output_cost_per_million": float,
    },
    "web": {
        "enabled": bool,
        "provider": str,
        "endpoint": str,
        "max_results": int,
        "timeout": int,
    },
}


def editable_settings(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return only dashboard-safe settings; runtime/private keys stay out."""
    return {
        section: {key: cfg[section][key] for key in fields}
        for section, fields in _EDITABLE.items()
    }


def update_settings(repo_root: Path, changes: object) -> dict[str, Any]:
    """Validate and atomically apply the dashboard's small config allowlist.

    The existing TOML is edited in place instead of serialized from the
    merged defaults. Comments, custom sections, and future keys therefore
    survive a dashboard save. Secrets are intentionally not part of the
    allowlist; web API keys are environment-only.
    """
    if not isinstance(changes, dict) or not changes:
        raise ValueError("settings must be a non-empty object")
    normalized: dict[str, dict[str, Any]] = {}
    for section_name, raw_values in changes.items():
        allowed = _EDITABLE.get(str(section_name))
        if allowed is None:
            raise ValueError(f"section {section_name!r} is not editable")
        if not isinstance(raw_values, dict) or not raw_values:
            raise ValueError(f"[{section_name}] changes must be an object")
        section_values: dict[str, Any] = {}
        for key, value in raw_values.items():
            expected = allowed.get(str(key))
            if expected is None:
                raise ValueError(f"[{section_name}].{key} is not editable")
            if expected is float:
                if type(value) not in (int, float):
                    raise ValueError(f"[{section_name}].{key} must be a number")
                value = float(value)
            elif type(value) is not expected:
                raise ValueError(
                    f"[{section_name}].{key} must be {expected.__name__}")
            if isinstance(value, str):
                limit = 4000 if key == "command" else 1000
                if len(value) > limit:
                    raise ValueError(
                        f"[{section_name}].{key} must be at most {limit} characters")
                value = value.strip()
            section_values[str(key)] = value
        normalized[str(section_name)] = section_values

    path = config_path(repo_root)
    text = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_TOML
    try:
        current = load(repo_root)
    except SystemExit:
        # Let the dashboard repair semantically invalid editable values. TOML
        # syntax errors still fail closed because editing around malformed
        # structure would risk overwriting unrelated user configuration.
        try:
            user = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ValueError(f"cannot edit malformed TOML: {exc}") from None
        current = _deep_merge(copy.deepcopy(DEFAULTS), user)
    candidate = copy.deepcopy(current)
    candidate.pop("_runtime", None)
    for section_name, values in normalized.items():
        candidate[section_name].update(values)
    errors = validation_errors(candidate)
    if errors:
        raise ValueError("; ".join(errors))

    for section_name, values in normalized.items():
        text = _update_toml_section(text, section_name, values)
    from .export import _atomic_write
    _atomic_write(path, text)
    return load(repo_root)


def _toml_literal(value: object) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) in (int, float):
        return str(value)
    if isinstance(value, str):
        # JSON basic strings are valid TOML basic strings for these bounded
        # dashboard values and correctly escape quotes, slashes, and controls.
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")


def _inline_comment(line: str) -> str:
    """Return a TOML inline comment, ignoring # characters inside strings."""
    quote = ""
    escaped = False
    for idx, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in ('"', "'"):
            quote = "" if quote == char else (char if not quote else quote)
            continue
        if char == "#" and not quote:
            return line[idx:].rstrip("\r\n")
    return ""


def _update_toml_section(text: str, section: str,
                         values: dict[str, Any]) -> str:
    """Replace/add scalar keys in one top-level TOML table."""
    lines = text.splitlines(keepends=True)
    header_re = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
    start = end = None
    for idx, line in enumerate(lines):
        match = header_re.match(line.rstrip("\r\n"))
        if not match:
            continue
        if start is None and match.group(1).strip() == section:
            start = idx
            continue
        if start is not None:
            end = idx
            break
    if start is None:
        suffix = "" if not text or text.endswith("\n") else "\n"
        block = [f"\n[{section}]\n"]
        block.extend(f"{key} = {_toml_literal(value)}\n"
                     for key, value in values.items())
        return text + suffix + "".join(block)
    if end is None:
        end = len(lines)
    pending = dict(values)
    key_re = re.compile(r"^(\s*)([A-Za-z0-9_-]+)\s*=")
    for idx in range(start + 1, end):
        match = key_re.match(lines[idx])
        if not match or match.group(2) not in pending:
            continue
        key = match.group(2)
        comment = _inline_comment(lines[idx])
        spacer = "  " if comment else ""
        lines[idx] = (f"{match.group(1)}{key} = {_toml_literal(pending.pop(key))}"
                      f"{spacer}{comment}\n")
    if pending:
        insertion = [f"{key} = {_toml_literal(value)}\n"
                     for key, value in pending.items()]
        lines[end:end] = insertion
    return "".join(lines)
