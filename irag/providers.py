"""Provider-neutral LLM process adapters.

irag is not an SDK client and intentionally owns no API keys. Each adapter
normalizes one coding/model CLI into the same contract: prompt in, plain text
out, bounded time, retries, model provenance, and local usage telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
from typing import Callable


SUPPORTED = ("claude", "codex", "agy", "ollama", "custom")


@dataclass(frozen=True)
class Invocation:
    provider: str
    argv: list[str]
    prompt_mode: str = "stdin"  # stdin | argv | file
    temp_path: str | None = None


@dataclass(frozen=True)
class Result:
    text: str
    provider: str
    model: str
    attempts: int
    duration_ms: int


def model_label(cfg: dict) -> str:
    llm = cfg.get("llm", {})
    provider = str(llm.get("provider") or "custom").lower()
    model = str(llm.get("model") or "").strip()
    if model:
        return f"{provider}:{model}"
    label = str(llm.get("model_label") or "").strip()
    if provider != "claude" and label == "claude":
        return provider
    return label or provider


def configured_model(cfg: dict) -> str:
    """Return the actual selected model, excluding legacy provider labels."""
    llm = cfg.get("llm", {})
    model = str(llm.get("model") or "").strip()
    if model:
        return model
    provider = str(llm.get("provider") or "custom").lower()
    command = str(llm.get("command") or "").strip()
    if (provider == "custom" or
            (provider == "claude" and command and command != "claude -p")):
        return str(llm.get("model_label") or "").strip()
    return ""


def _custom(llm: dict, prompt: str) -> Invocation:
    raw = str(llm.get("command", ""))
    try:
        argv = shlex.split(raw)
    except ValueError as exc:
        raise SystemExit(f"irag: invalid [llm].command quoting: {exc}") \
            from None
    if not argv:
        raise SystemExit("irag: [llm].command must not be empty")
    if any("{promptfile}" in token for token in argv):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(prompt)
            path = fh.name
        return Invocation("custom", [t.replace("{promptfile}", path)
                                     for t in argv], "file", path)
    if any("{prompt}" in token for token in argv):
        return Invocation("custom", [t.replace("{prompt}", prompt)
                                     for t in argv], "argv")
    return Invocation("custom", argv)


def invocation(cfg: dict, prompt: str) -> Invocation:
    llm = cfg.get("llm", {})
    provider = str(llm.get("provider") or "custom").lower()
    model = str(llm.get("model") or "").strip()
    if provider not in SUPPORTED:
        raise SystemExit(f"irag: unsupported LLM provider {provider!r}")
    if provider == "custom":
        return _custom(llm, prompt)
    # The pre-provider config surface was one free-form command. Keep an
    # edited legacy/default command working even when a newly written config
    # also contains provider="claude" (old setup scripts commonly replace
    # exactly this line with a mock or wrapper).
    command = str(llm.get("command") or "").strip()
    if provider == "claude" and command and command != "claude -p":
        return _custom(llm, prompt)
    if provider == "claude":
        argv = ["claude", "-p"]
        if model:
            argv += ["--model", model]
        return Invocation(provider, argv)
    if provider == "codex":
        argv = ["codex", "exec", "--ephemeral", "--skip-git-repo-check",
                "--color", "never"]
        if model:
            argv += ["--model", model]
        return Invocation(provider, argv)
    if provider == "agy":
        argv = ["agy", "--print", "--output-format", "text"]
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return Invocation(provider, argv, "argv")
    if not model:
        raise SystemExit("irag: [llm].model is required for provider=ollama")
    return Invocation(provider, ["ollama", "run", model])


def availability(cfg: dict) -> tuple[bool, str]:
    """Check configuration and executable availability without a model call."""
    try:
        call = invocation(cfg, "irag provider check")
    except SystemExit as exc:
        return False, str(exc)
    try:
        executable = call.argv[0]
        if shutil.which(executable) is None:
            return False, f"executable not found: {executable}"
        model = configured_model(cfg) or "provider default"
        return True, f"{call.provider} ready ({executable}; model {model})"
    finally:
        if call.temp_path:
            try:
                os.unlink(call.temp_path)
            except OSError:
                pass


def _record(cfg: dict, result: Result | None, purpose: str, status: str,
            input_tokens: int, output_tokens: int, error: str | None,
            duration_ms: int, attempts: int) -> None:
    runtime = cfg.get("_runtime", {})
    root_text = runtime.get("root") if isinstance(runtime, dict) else None
    if not root_text:
        return
    db_path = Path(str(root_text)) / ".irag" / "memory.db"
    if not db_path.exists():
        return
    llm = cfg.get("llm", {})
    in_rate = float(llm.get("input_cost_per_million") or 0)
    out_rate = float(llm.get("output_cost_per_million") or 0)
    cost = None
    if in_rate or out_rate:
        cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000
    provider = result.provider if result else str(llm.get("provider") or "custom")
    model = result.model if result else configured_model(cfg)
    try:
        from . import db
        conn = db.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO llm_runs(provider,model,purpose,status," 
                "input_tokens,output_tokens,estimated_cost_usd,duration_ms," 
                "attempt_count,error) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (provider, model, purpose[:500], status, input_tokens,
                 output_tokens, cost, duration_ms, attempts,
                 error[:1000] if error else None))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass  # telemetry must never turn a successful model call into failure


def run(cfg: dict, prompt: str, *, purpose: str = "",
        validator: Callable[[str], str | None] | None = None,
        max_output: int = 400_000) -> Result:
    """Execute the selected provider and normalize its failure modes."""
    from . import tokens
    llm = cfg.get("llm", {})
    retries = int(llm.get("retries", 0))
    timeout = int(llm.get("timeout", 300))
    started = time.monotonic()
    last_error = "model call failed"
    total_attempts = retries + 1
    attempts_made = 0
    last_provider = str(llm.get("provider") or "custom")
    for attempt in range(1, total_attempts + 1):
        attempts_made = attempt
        call = invocation(cfg, prompt)
        last_provider = call.provider
        try:
            try:
                completed = subprocess.run(
                    call.argv,
                    input=prompt if call.prompt_mode == "stdin" else "",
                    capture_output=True, text=True, errors="replace",
                    timeout=timeout)
            except FileNotFoundError:
                last_error = (f"LLM executable not found: {call.argv[0]!r}; "
                              "set [llm].provider or [llm].command")
                break
            except subprocess.TimeoutExpired:
                last_error = f"LLM provider timed out after {timeout}s"
            else:
                if completed.returncode != 0:
                    detail = completed.stderr.strip()[:500] or "no stderr"
                    last_error = (f"LLM provider failed (exit "
                                  f"{completed.returncode}): {detail}")
                else:
                    text = completed.stdout
                    if len(text) > max_output:
                        text = text[:max_output]
                    text = text.strip()
                    if not text:
                        last_error = "LLM provider produced no output"
                    else:
                        validation = validator(text) if validator else None
                        if validation:
                            last_error = "invalid model output: " + validation
                        else:
                            duration = int((time.monotonic() - started) * 1000)
                            model = configured_model(cfg)
                            result = Result(text, call.provider, model, attempt,
                                            duration)
                            _record(cfg, result, purpose, "completed",
                                    tokens.count(prompt), tokens.count(text),
                                    None, duration, attempt)
                            return result
        finally:
            if call.temp_path:
                try:
                    os.unlink(call.temp_path)
                except OSError:
                    pass
        if attempt < total_attempts:
            time.sleep(min(0.25 * (2 ** (attempt - 1)), 2.0))
    duration = int((time.monotonic() - started) * 1000)
    failed = Result("", last_provider, configured_model(cfg), attempts_made,
                    duration)
    _record(cfg, failed, purpose, "failed", tokens.count(prompt), 0,
            last_error, duration, attempts_made)
    raise SystemExit(f"irag: {last_error} after {attempts_made} "
                     f"attempt{'s' if attempts_made != 1 else ''}")
