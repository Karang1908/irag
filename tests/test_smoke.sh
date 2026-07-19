#!/bin/sh
# irag end-to-end smoke test. Run from anywhere:
#   sh tests/test_smoke.sh
# Requires: python3 >= 3.11, git. Uses the deterministic mock LLM.
set -e

IRAG_SRC="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONPATH="$IRAG_SRC"
DEMO="$(mktemp -d)"
trap 'rm -rf "$DEMO"' EXIT

cd "$DEMO"
git init -q && git config user.email t@t && git config user.name t
mkdir -p src/auth src/api
printf 'def login():\n    pass\n' > src/auth/login.py
printf 'def routes():\n    pass\n' > src/api/routes.py
echo 'requests==2.31.0' > requirements.txt
git add -A && git commit -qm "initial"

python3 -m irag init
# point the LLM at the mock (reads stdin, deterministic markdown incl. a
# fake `src/ghost.py` to trigger a missing_path contradiction)
python3 - "$IRAG_SRC" << 'EOF'
import sys, pathlib
src = sys.argv[1]
cfg = pathlib.Path(".irag/config.toml")
text = cfg.read_text()
text = text.replace('command = "claude -p"       # prompt on stdin, markdown on stdout',
                    f'command = "python3 {src}/tests/mock_llm.py"')
cfg.write_text(text)
EOF

python3 -m irag synthesize
python3 -m irag lint
python3 -m irag contradictions

set +e
python3 -m irag check
rc=$?
set -e
[ "$rc" -eq 1 ] || { echo "FAIL: check should exit 1 with open contradictions"; exit 1; }

python3 -m irag context --open src/auth/login.py --query "auth login"
python3 -m irag why "login"
python3 -m irag export && head -3 CLAUDE.md
[ -f AGENTS.md ] && cmp -s CLAUDE.md AGENTS.md \
  || { echo "FAIL: AGENTS.md should exist and mirror CLAUDE.md"; exit 1; }

# resolve every open contradiction
for id in $(python3 -m irag contradictions | sed -n 's/^\[\([0-9]*\)\].*/\1/p'); do
  python3 -m irag resolve "$id" --notes "smoke test"
done

python3 -m irag check || { echo "FAIL: check should exit 0 after resolution"; exit 1; }

python3 -m irag rollback src/auth/login.py 1
python3 -m irag record-decision "using JWT" --module src/auth

echo "# change" >> src/auth/login.py && git add -A && git commit -qm "tweak"
sync_out="$(python3 -m irag sync)"
echo "$sync_out"
echo "$sync_out" | grep -q "run 'irag update'" \
  || { echo "FAIL: sync should hint at 'irag update' when events are queued"; exit 1; }
python3 -m irag stale

# conversation logger: begin, synthesize inside the window, end, verify the
# redundant per-file changes_detail lands on the session row
python3 -m irag session-begin --agent smoke-test
python3 -m irag synthesize
python3 -m irag session-end
python3 -m irag sessions --json | python3 -c "
import json, sys
rows = json.load(sys.stdin)
last = rows[0]
assert last['agent'] == 'smoke-test', f\"agent mismatch: {last['agent']!r}\"
assert last['changes_detail'], 'changes_detail should be non-empty after a synthesize inside the session window'
assert 'subject_id' in last['changes_detail'][0]
print('session changes_detail ok:', last['changes_detail'][0]['subject_id'])
" || { echo "FAIL: session changes_detail check"; exit 1; }

python3 -m irag scan
python3 -m irag map
python3 -m irag map src/auth/login.py | grep -q "login" || { echo "FAIL: map missing symbol"; exit 1; }
python3 -m irag impact src/auth/login.py
python3 -m irag learn "test lesson" --module src/auth/login.py
python3 -m irag context | grep -q "test lesson" || { echo "FAIL: lesson not served"; exit 1; }
python3 -m irag claude-setup
grep -q "irag context" .claude/settings.json || { echo "FAIL: hook not installed"; exit 1; }
grep -q "Working with irag" CLAUDE.md || { echo "FAIL: agent footer missing"; exit 1; }

python3 -m irag status
python3 -m irag status --json | python3 -c "import json,sys; json.load(sys.stdin)"
python3 -m irag doctor || { echo "FAIL: doctor should pass"; exit 1; }
python3 -m irag diff src/auth/login.py
python3 -m irag backup
python3 -m irag map --json | python3 -c "import json,sys; json.load(sys.stdin)"

python3 - << 'DASHEOF'
import threading, time, json, urllib.request, sys, pathlib
sys.path.insert(0, "")
from irag import dashboard
threading.Thread(target=dashboard.serve, args=(pathlib.Path.cwd(),),
                 kwargs={"port": 7911, "open_browser": False}, daemon=True).start()
time.sleep(0.7)
with urllib.request.urlopen("http://127.0.0.1:7911/api/status") as r:
    assert json.load(r)["pages"] > 0
with urllib.request.urlopen("http://127.0.0.1:7911/") as r:
    assert b"irag" in r.read()
with urllib.request.urlopen("http://127.0.0.1:7911/api/sessions") as r:
    sess = json.load(r)
    assert isinstance(sess, list) and len(sess) >= 1
    assert "changes_detail" in sess[0] and "files_changed" in sess[0]
print("dashboard smoke ok")
DASHEOF

python3 -m irag obsidian
[ -f irag_vault/README.md ] && [ -f "irag_vault/_versions/v1 src-auth-login.py.md" ] \
  || { echo "FAIL: obsidian vault incomplete"; exit 1; }
python3 -m irag obsidian   # rerun exercises the wipe-and-rebuild path

# --- directory-wise scoping: init inside a subdir of a larger git repo
# must scope to that subdir (snapshot mode), never adopt the parent ---
MEGA="$(mktemp -d)"
mkdir -p "$MEGA/other" "$MEGA/proj/src"
( cd "$MEGA" && git init -q && git config user.email t@t \
  && git config user.name t \
  && echo "junk=1" > other/junk.py \
  && printf 'def inner(): pass\n' > proj/src/inner.py \
  && git add -A && git commit -qm mega )
( cd "$MEGA/proj" && python3 -m irag init > "$MEGA/init_out.txt" 2>&1 )
grep -q "scoping the project to THIS directory" "$MEGA/init_out.txt" \
  || { echo "FAIL: nested init should print the scoping note"; exit 1; }
[ -d "$MEGA/proj/.irag" ] || { echo "FAIL: .irag missing in subproject"; exit 1; }
[ ! -d "$MEGA/.irag" ] || { echo "FAIL: .irag leaked to enclosing repo root"; exit 1; }
[ ! -f "$MEGA/.git/hooks/post-commit" ] \
  || { echo "FAIL: hooks installed into enclosing repo"; exit 1; }
( cd "$MEGA/proj" && python3 -m irag status --json ) \
  | python3 -c "
import json, sys
s = json.load(sys.stdin)
assert s['file_pages'] == 1, f\"nested project should track 1 file, got {s['file_pages']}\"
print('nested scoping ok')
" || { echo "FAIL: nested project page scoping"; exit 1; }
rm -rf "$MEGA"

echo "SMOKE TEST PASSED"
