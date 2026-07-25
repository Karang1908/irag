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
# init installs the agent guide (CLAUDE.md + AGENTS.md) immediately
[ -f CLAUDE.md ] && grep -q "irag agent guide" CLAUDE.md \
  || { echo "FAIL: irag init should install the CLAUDE.md agent guide"; exit 1; }
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

# the git diff reaches the synthesis prompt, and the model's own
# CHANGE-SUMMARY replaces the old "synthesis from N event(s)" boilerplate
python3 - << 'EOF'
import sqlite3
rows = [r[0] or "" for r in sqlite3.connect(".irag/memory.db").execute(
    "SELECT change_summary FROM revisions")]
assert any("from a code diff" in s for s in rows), \
    f"git diff never reached the synthesis prompt: {rows[:6]}"
assert not any("synthesis from" in s and "event(s)" in s for s in rows), \
    f"boilerplate change_summary still in use: {rows[:6]}"
print("change_summary is diff-derived; boilerplate gone")
EOF

python3 -m irag scan
python3 -m irag map
python3 -m irag map src/auth/login.py | grep -q "login" || { echo "FAIL: map missing symbol"; exit 1; }
python3 -m irag impact src/auth/login.py
python3 -m irag learn "test lesson" --module src/auth/login.py
python3 -m irag context | grep -q "test lesson" || { echo "FAIL: lesson not served"; exit 1; }
python3 -m irag claude-setup
grep -q "irag context" .claude/settings.json || { echo "FAIL: hook not installed"; exit 1; }
grep -q "manage this project's memory with irag" CLAUDE.md || { echo "FAIL: agent guide missing"; exit 1; }

python3 -m irag status
python3 -m irag status --json | python3 -c "import json,sys; json.load(sys.stdin)"
python3 -m irag doctor || { echo "FAIL: doctor should pass"; exit 1; }
python3 -m irag diff src/auth/login.py
python3 -m irag backup
python3 -m irag map --json | python3 -c "import json,sys; json.load(sys.stdin)"

# read commands that were previously never exercised
python3 -m irag search login | grep -qi "login" \
  || { echo "FAIL: search should find the login page"; exit 1; }
python3 -m irag ask "how does login work?" >/dev/null \
  || { echo "FAIL: ask pipeline errored"; exit 1; }
python3 -m irag recap | grep -qi "previous sessions" \
  || { echo "FAIL: recap should list previous sessions"; exit 1; }
python3 -m irag asof 2099-01-01 | grep -q "login" \
  || { echo "FAIL: asof should show current pages as of a future date"; exit 1; }
python3 -m irag pin src/auth/login.py
python3 -m irag status --json | python3 -c "import json,sys; assert json.load(sys.stdin)['pages']>0"
python3 -m irag unpin src/auth/login.py

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

def chat(body):
    req = urllib.request.Request(
        "http://127.0.0.1:7911/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)

# forced SQL mode must stay local even on a miss (never fall through to AI)
miss = chat({"message": "zzznomatch", "mode": "sql"})
assert miss["mode"] == "sql" and miss["results"] == [], f"forced-sql miss leaked to AI: {miss}"
hit = chat({"message": "login", "mode": "sql"})
assert hit["mode"] == "sql" and hit["results"], f"forced-sql hit returned nothing: {hit}"
print("dashboard smoke ok (chat forced-sql honored)")
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

# --- widened structural graph: symbols + import edges for more languages
# (Java/Ruby/C in addition to Python/JS/Go/Rust) ---
POLY="$(mktemp -d)"
mkdir -p "$POLY/lib"
( cd "$POLY" && git init -q && git config user.email t@t \
  && git config user.name t )
printf 'module Helper\n  def self.go; end\nend\n' > "$POLY/lib/helper.rb"
printf 'require_relative "./helper"\nclass App\n  def run; Helper.go; end\nend\n' \
  > "$POLY/lib/app.rb"
printf 'int util(void){return 1;}\n' > "$POLY/lib/util.h"
printf '#include "util.h"\nint main(void){return util();}\n' > "$POLY/lib/main.c"
printf 'public class Box { public int size() { return 1; } }\n' \
  > "$POLY/lib/Box.java"
( cd "$POLY" && git add -A && git commit -qm poly >/dev/null \
  && python3 -m irag init >/dev/null 2>&1 && python3 -m irag scan >/dev/null )
( cd "$POLY" && python3 -m irag map ) | grep -q "lib/Box.java" \
  || { echo "FAIL: Java symbols missing from the map"; exit 1; }
( cd "$POLY" && python3 -m irag impact lib/helper.rb ) | grep -q "lib/app.rb" \
  || { echo "FAIL: ruby require_relative edge not resolved"; exit 1; }
( cd "$POLY" && python3 -m irag impact lib/util.h ) | grep -q "lib/main.c" \
  || { echo "FAIL: C #include edge not resolved"; exit 1; }
# `from . import x` is the dominant intra-package Python style and must
# create a real edge (it used to resolve to the package dir only)
mkdir -p "$POLY/pkg"
printf 'def helper(): pass\n' > "$POLY/pkg/util.py"
printf 'from . import util\ndef run(): return util.helper()\n' > "$POLY/pkg/main.py"
printf '' > "$POLY/pkg/__init__.py"
( cd "$POLY" && git add -A && git commit -qm rel >/dev/null && python3 -m irag scan >/dev/null )
( cd "$POLY" && python3 -m irag impact pkg/util.py ) | grep -q "pkg/main.py" \
  || { echo "FAIL: 'from . import util' did not create a dependency edge"; exit 1; }
echo "relative-import edges ok"
rm -rf "$POLY"
echo "poly-language graph ok"

# --- opt-in transcript capture: the verbatim conversation in the diary ---
TR="$(mktemp -d)"
( cd "$TR" && git init -q && git config user.email t@t && git config user.name t \
  && printf 'x=1\n' > a.py && git add -A && git commit -qm init >/dev/null \
  && python3 -m irag init >/dev/null 2>&1 )
python3 - "$TR" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[1]) / ".irag" / "config.toml"
cfg.write_text(cfg.read_text().replace("capture_transcript = false",
                                        "capture_transcript = true"))
PYEOF
cat > "$TR/t.jsonl" << 'JEOF'
{"type":"user","message":{"role":"user","content":"hello there"}}
{"type":"user","isMeta":true,"message":{"role":"user","content":"META skip me"}}
{"type":"user","isSidechain":true,"message":{"role":"user","content":"SIDE skip me"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"SECRET reasoning"},{"type":"text","text":"hi back"},{"type":"tool_use","name":"Edit","input":{}}]}}
JEOF
( cd "$TR" && python3 -m irag session-begin >/dev/null \
  && python3 -m irag session-end --no-narrate --transcript t.jsonl >/dev/null )
TRID=$( cd "$TR" && python3 -c "import sqlite3; print(sqlite3.connect('.irag/memory.db').execute('SELECT MAX(session_id) FROM sessions').fetchone()[0])" )
( cd "$TR" && python3 -m irag transcript "$TRID" ) > "$TR/out.txt"
grep -q "hello there" "$TR/out.txt" \
  || { echo "FAIL: transcript missing the user message"; exit 1; }
grep -q "\[tool: Edit\]" "$TR/out.txt" \
  || { echo "FAIL: transcript missing the tool_use note"; exit 1; }
if grep -qE "META skip me|SIDE skip me|SECRET reasoning" "$TR/out.txt"; then
  echo "FAIL: transcript leaked meta/sidechain/thinking content"; exit 1
fi
rm -rf "$TR"
echo "transcript capture ok"

# --- TS/JS indexing + linter false positives (regression guard) -------------
# Five defects found on a real Vite/React/zustand project: the symbol regex
# only matched a 2015 subset of JS, and the path/symbol checks flagged four
# things that were never wrong. Each fix added a skip path, so this also
# re-proves the checker still catches genuine lies.
TS=$(mktemp -d)
mkdir -p "$TS/src"
( cd "$TS" && git init -q . )
echo '{"dependencies":{"three":"^0.160.0","zustand":"^4.5.0"}}' > "$TS/package.json"
cat > "$TS/src/store.ts" << 'TSEOF'
import { create } from 'zustand';
export const useApp = create((set) => ({ launch: () => set({}) }));
export const API_URL: string = 'https://x';
export interface AppState { ready: boolean }
export type Handler = (e: Event) => void;
export enum Mode { Idle, Busy }
TSEOF
cat > "$TS/src/main.tsx" << 'TSEOF'
import { useFrame } from '@react-three/fiber';
export function App() { return null }
TSEOF
( cd "$TS" && git add -A && git -c user.email=t@t -c user.name=t commit -qm i   && python3 -m irag init >/dev/null && python3 -m irag scan >/dev/null )
SYMS=$( cd "$TS" && python3 - << 'PYEOF'
import sqlite3
q = "SELECT name FROM symbols WHERE file LIKE '%store.ts'"
print(",".join(r[0] for r in sqlite3.connect(".irag/memory.db").execute(q)))
PYEOF
)
for want in useApp API_URL AppState Handler Mode; do
  case ",$SYMS," in *",$want,"*) ;; *)
    echo "FAIL: store.ts symbol '$want' not indexed (got: $SYMS)"; exit 1;; esac
done
# true statements must NOT be flagged; false ones MUST be
( cd "$TS" && python3 - << 'PYEOF'
import sqlite3, pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, linter
conn = db.connect(".irag/memory.db"); cfg = config.load(pathlib.Path.cwd())
def page(subject, body):
    c = conn.execute("INSERT INTO pages(page_type,title,subject_type,subject_id)"
                     " VALUES('module',?,'module',?)", (subject, subject))
    r = conn.execute("INSERT INTO revisions(page_id,version_number,body_markdown,"
                     "change_summary) VALUES(?,1,?,'x')", (c.lastrowid, body))
    conn.execute("UPDATE pages SET current_revision_id=? WHERE page_id=?",
                 (r.lastrowid, c.lastrowid))
page("src/main.tsx", "True: `/src/main.tsx` `three.js` `store.ts` `useFrame()`.")
conn.commit(); linter.lint(conn, cfg, pathlib.Path.cwd())
bogus = conn.execute("SELECT COUNT(*) FROM contradictions "
                     "WHERE resolved_at IS NULL").fetchone()[0]
assert bogus == 0, f"false positives on true claims: {bogus}"
page("src/store.ts", "False: `src/nope.tsx` `/src/ghost.tsx` `ghost.ts` "
                     "`fake.js` `totallyFake()`.")
conn.commit(); linter.lint(conn, cfg, pathlib.Path.cwd())
real = conn.execute("SELECT COUNT(*) FROM contradictions "
                    "WHERE resolved_at IS NULL").fetchone()[0]
assert real == 5, f"fact-checker went blind: caught {real}/5 real problems"
PYEOF
) || { echo "FAIL: linter regression on TS project"; exit 1; }
rm -rf "$TS"
echo "ts indexing + linter precision ok"

# --- linter precision round 2 (regression guard) ---------------------------
# Eight more defects found auditing irag against a real project: an
# extension allowlist that exempted css/html/svg, scoped npm packages never
# version-checked, manifest ranges compared as literal pins, PEP 508 markers
# leaking into versions, a grep fallback where a call site read as a
# definition (a real hallucination passed), indented locals indexed as
# module symbols, and markdown table rows silently dropped.
python3 - << 'PYEOF' || { echo "FAIL: linter precision round 2"; exit 1; }
import json, pathlib, re, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import linter, structure

for ext in ("css", "html", "svg", "vue", "kt"):
    assert linter.PATH_RE.search(f"`a.{ext}`"), f"PATH_RE ignores .{ext}"
assert linter.VERSION_AT_RE.search(
    "@react-three/fiber@9.6.1").group(1) == "@react-three/fiber"

d = pathlib.Path(tempfile.mkdtemp())
(d / "package.json").write_text(json.dumps({"dependencies": {
    "lodash": "4.x", "pkg": "workspace:*", "react": "^18.2.0"}}))
(d / "requirements.txt").write_text(
    'requests==2.28.0; python_version >= "3.8"\n'
    'flask==2.0.1  # c\nuvicorn[standard]==0.30.0\n')
man = linter._read_manifest_versions(d)
assert "lodash" not in man and "pkg" not in man, f"unpinned spec kept: {man}"
assert man.get("react") == "18.2.0", man
assert man.get("requests") == "2.28.0", man     # marker stripped
assert man.get("flask") == "2.0.1", man         # comment stripped
assert man.get("uvicorn") == "0.30.0", man      # extras stripped from key

pat = re.compile(r"subtract\s*\([^;=()\n]*?\)\s*(?:\{|throws\b)")
assert not pat.search("if (subtract(a, b) > 0) {\n"), "call site reads as def"
assert pat.search("function subtract(a, b) {\n"), "real def no longer matches"

syms = []
for m in structure.JS_SYMBOL_RE.finditer(
        "export const useApp = create(() => ({}));\n"
        "function draw() {\n  const t = (a + b);\n}\n"
        "export interface Props { x: number }\n"):
    g = m.groupdict()
    syms.append(g["fn"] or g["cls"] or g["iface"] or g["ty"] or g["var"])
assert syms == ["useApp", "draw", "Props"], f"top-level only: got {syms}"
PYEOF
echo "linter precision round 2 ok"

echo "SMOKE TEST PASSED"
