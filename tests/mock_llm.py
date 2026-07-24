#!/usr/bin/env python3
"""Deterministic mock LLM for testing (file + folder prompts).

Emits fixed-shape pages; file pages include a fake `src/ghost.py`
reference so the static linter has a missing_path to catch. Every page
ends with the CHANGE-SUMMARY line the real instructions demand, and says
whether a CODE DIFF reached the prompt — so the tests can prove both the
diff plumbing and the summary parsing work.
"""
import re
import sys

prompt = sys.stdin.read()
saw_diff = "CODE DIFF since the last synthesis" in prompt
if "SESSION LOG TASK" in prompt:
    print("The session extended the auth module and refreshed its pages. Mock narrative.")
    sys.exit(0)
m = re.search(r"^(FILE|FOLDER): (.+)$", prompt, re.M)
kind, subject = (m.group(1), m.group(2).strip()) if m else ("FILE", "unknown")

if kind == "FOLDER":
    block = re.search(r"DIRECT CHILDREN AND THEIR SUMMARIES:\n((?:- .+\n)+)",
                      prompt)
    kids = [m.group(1) for m in
            (re.search(r"`([^`]+)`", ln) for ln in
             (block.group(1).splitlines() if block else []))
            if m]
    lines = "\n".join(f"- `{k}` — child of this folder" for k in kids[:12])
    print(f"""# {subject}

Overview of `{subject}` written by the mock LLM.

## Contents
{lines or '- (none)'}

## How it fits together
- children collaborate (mock).

## Recent changes
- mock folder synthesis.
CHANGE-SUMMARY: mock rollup of `{subject}`\
{' from a code diff' if saw_diff else ''}""")
else:
    print(f"""# {subject}

Mock summary of `{subject}`.

## Public interface
- `login()` — mock entry point.

## Behavior & gotchas
- see `src/ghost.py` (planted for the linter).

## Connections & blast radius
- mock blast statement.

## Recent changes
- mock file synthesis.
CHANGE-SUMMARY: mock edit to `{subject}`\
{' from a code diff' if saw_diff else ''}""")
