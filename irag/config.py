"""irag.config — configuration loading and defaults.

Configuration lives at ``.irag/config.toml`` in the repo root. Values are
deep-merged over the built-in defaults, so a config file may specify only
the keys it wants to override.
"""
from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

DEFAULT_TOML = '''[ingest]
mode = "auto"               # auto | git | snapshot (auto = git if present)

[llm]
command = "claude -p"       # prompt on stdin, markdown on stdout
model_label = "claude"
timeout = 300

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
        "command": "claude -p",
        "model_label": "claude",
        "timeout": 300,
        # how many pages to synthesize at once. 1 keeps the old strictly
        # sequential behaviour; raise it if your LLM CLI tolerates
        # concurrent invocations (most do — each is its own process).
        "parallel": 1,
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
        return copy.deepcopy(DEFAULTS)
    try:
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"irag: invalid TOML in {path}: {exc}") from exc
    merged = _deep_merge(copy.deepcopy(DEFAULTS), user)
    errors = validation_errors(merged)
    if errors:
        detail = "; ".join(errors)
        raise SystemExit(f"irag: invalid config in {path}: {detail}")
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
        if nonempty and isinstance(value, str) and not value.strip():
            errors.append(f"[{section_name}].{key} must not be empty")
        return value

    ingest = section("ingest")
    mode = expect(ingest, "ingest", "mode", str, nonempty=True)
    if isinstance(mode, str) and mode not in {"auto", "git", "snapshot"}:
        errors.append("[ingest].mode must be one of: auto, git, snapshot")

    llm = section("llm")
    expect(llm, "llm", "command", str, nonempty=True)
    expect(llm, "llm", "model_label", str, nonempty=True)
    expect(llm, "llm", "timeout", int, minimum=1)
    expect(llm, "llm", "parallel", int, minimum=1)

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
