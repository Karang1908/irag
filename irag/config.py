"""irag.config — configuration loading and defaults.

Configuration lives at ``.irag/config.toml`` in the repo root. Values are
deep-merged over the built-in defaults, so a config file may specify only
the keys it wants to override.
"""
from __future__ import annotations

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

[check]
max_staleness = 150
fail_on_contradictions = true
'''

DEFAULTS: dict[str, Any] = {
    "ingest": {"mode": "auto"},
    "llm": {
        "command": "claude -p",
        "model_label": "claude",
        "timeout": 300,
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
    },
    "retrieval": {
        "token_budget": 8000,
        "full_max": 4,
        "min_score": 20,
    },
    "check": {
        "max_staleness": 150,
        "fail_on_contradictions": True,
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
    if not path.exists():
        return dict(DEFAULTS)
    try:
        with open(path, "rb") as fh:
            user = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"irag: invalid TOML in {path}: {exc}") from exc
    return _deep_merge(DEFAULTS, user)


def write_default(repo_root: Path) -> Path:
    """Write the default config file if it does not exist. Returns its path."""
    path = config_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(DEFAULT_TOML, encoding="utf-8")
    return path
