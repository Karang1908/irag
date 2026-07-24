"""irag.tokens — one place to estimate token counts.

irag is dependency-free by design, and its default summarizer is Claude,
whose exact tokenizer isn't public — so a *truly* exact count is neither
free nor meaningful here. This module gives one honest, code-aware
estimate used everywhere (write-path accounting, the dashboard burn
chart), and sharpens it with `tiktoken` *if the user happens to have it
installed* — but the number is always reported as an estimate, never as
ground truth, because the model that actually tokenized the text may not
match the tokenizer we have.

`chars // 4` (the old estimate) undercounts code, which is dense with
short punctuation tokens. The heuristic here splits text into word and
punctuation pieces — closer to how BPE actually segments — landing much
nearer real counts on source-heavy prompts.
"""
from __future__ import annotations

import re

# word runs vs individual punctuation: BPE keeps words mostly whole and
# emits punctuation as its own tokens, so this split tracks it well enough
_PIECE = re.compile(r"\w+|[^\w\s]")

_ENC = None          # cached tiktoken encoding, if available
_TRIED = False       # only attempt the optional import once


def _encoder():
    """Return a tiktoken encoder if the optional dependency is installed,
    else None. Never raises — a missing tiktoken is the normal case."""
    global _ENC, _TRIED
    if _TRIED:
        return _ENC
    _TRIED = True
    try:
        import tiktoken
        _ENC = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENC = None
    return _ENC


def _estimate(text: str) -> int:
    """Dependency-free, code-aware token estimate."""
    n = 0
    for piece in _PIECE.findall(text):
        if piece.isalnum():
            # ~4 characters per sub-word token within a word run
            n += max(1, (len(piece) + 3) // 4)
        else:
            n += 1
    return n


def count(text: str) -> int:
    """Estimated token count for ``text``. Uses tiktoken when present
    (a sharper estimate), otherwise the code-aware heuristic. Always an
    estimate — label it as such in output."""
    if not text:
        return 0
    enc = _encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    return _estimate(text)
