"""irag.tokens — one place to estimate token counts.

irag is dependency-free by design, and its default summarizer is Claude,
whose exact tokenizer isn't public — so a *truly* exact count is neither
free nor meaningful here. This module gives one honest, code-aware
estimate used everywhere (write-path accounting, the dashboard burn
chart), and sharpens it with `tiktoken` *if the user happens to have it
installed* — but the number is always reported as an estimate, never as
ground truth, because the model that actually tokenized the text may not
match the tokenizer we have.

The heuristic splits text into word and punctuation pieces — closer to
how BPE actually segments — and weights each by a factor measured against
cl100k_base on real source. See `_estimate` for the calibration and the
worst-case error it achieves.
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
    """Dependency-free, code-aware token estimate.

    Two rules, both calibrated against cl100k_base over ~135k tokens of
    real Python, JS, HTML, markdown and CSS:

    - a ``\\w+`` run costs roughly one token per 6 characters. The test is
      on the run's FIRST character rather than ``piece.isalnum()``:
      ``\\w`` includes ``_``, and ``"my_var".isalnum()`` is False, so every
      snake_case identifier - the most common shape in source - used to
      fall through to the punctuation branch and count as ONE token no
      matter how long it was.
    - punctuation costs ~0.6 tokens apiece, not 1, because BPE merges runs
      like ``);``, ``=>`` and ``),`` into single tokens instead of emitting
      one per character.

    Worst-case error across those five corpora: 9%. The previous version
    was 53%, and plain ``chars // 4`` is 19% - so the older docstring had
    it backwards: chars//4 was the better estimator, not the worse one.
    Still an estimate; Claude's tokenizer is not public, so this is
    calibrated against the closest available proxy.
    """
    words = punct = 0
    for match in _PIECE.finditer(text):
        piece = match.group()
        if piece[0].isalnum() or piece[0] == "_":     # a \\w+ run
            words += max(1, (len(piece) + 5) // 6)
        else:
            punct += 1
    return words + (punct * 3) // 5                  # 0.6 per punctuation


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
