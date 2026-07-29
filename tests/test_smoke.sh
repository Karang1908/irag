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

# --- token estimate + ignore casing (regression guard) ---------------------
python3 - << 'PYEOF' || { echo "FAIL: tokens/ignore regression"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import tokens, ingest, config
# \w includes '_', so "my_var".isalnum() is False; snake_case identifiers
# must not collapse to a single token
assert tokens._estimate("some_very_long_identifier_name") >= 4, \
    "snake_case identifier counted as ~1 token again"
assert tokens._estimate("") == 0
# ignore matching is case-insensitive: macOS/Windows filesystems are, and a
# scanned node_modules costs one LLM call per dependency file
d = pathlib.Path(tempfile.mkdtemp())
(d / ".irag").mkdir()
cfg = config.load(d)
for p in ("node_modules/react/i.js", "Node_Modules/react/i.js", "NODE_MODULES/x.js"):
    assert ingest.is_ignored(p, cfg, d), f"{p} must be ignored"
for p in ("src/main.tsx", "src/Node.tsx"):
    assert not ingest.is_ignored(p, cfg, d), f"{p} must NOT be ignored"
PYEOF
echo "token estimate + ignore casing ok"

# --- flags must demote, never erase (regression guard) ---------------------
# P_CONTRADICTED (40) once pushed a page under min_score (20), which dropped
# it out of the briefing AND suppressed the very warning meant to say
# "verify this" - so flagging a page silently deleted the agent's memory of
# that file. Eligibility is now judged on relevance; warnings are ungated.
python3 - << 'PYEOF' || { echo "FAIL: contradiction must not erase context"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, retrieval
cd = pathlib.Path(tempfile.mkdtemp()); (cd / ".irag").mkdir()
cfg = config.load(cd)
BODY = "Summary of this module. " * 40

def build(flag):
    p = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
    db.ensure_db(p); conn = db.connect(str(p))
    for i in range(24):
        c = conn.execute("INSERT INTO pages(page_type,title,subject_type,"
                         "subject_id) VALUES('module',?,'module',?)",
                         (f"src/m{i}.py", f"src/m{i}.py"))
        r = conn.execute("INSERT INTO revisions(page_id,version_number,"
                         "body_markdown,change_summary) VALUES(?,1,?,'x')",
                         (c.lastrowid, BODY))
        conn.execute("UPDATE pages SET current_revision_id=? WHERE page_id=?",
                     (r.lastrowid, c.lastrowid))
        if flag:
            conn.execute("INSERT INTO contradictions(page_id,revision_id,claim,"
                         "truth,ctype,severity,detected_by) VALUES(?,?,'c','t',"
                         "'missing_path','high','static:t')",
                         (c.lastrowid, r.lastrowid))
    conn.commit(); return conn

opens = [f"src/m{i}.py" for i in range(24)]
_, clean = retrieval.serve(build(False), cfg, opens, "module", budget_tokens=3000)
md, flagged = retrieval.serve(build(True), cfg, opens, "module", budget_tokens=3000)
assert len(flagged["full"]) == len(clean["full"]), \
    f"flagging changed the full tier: {len(clean['full'])} -> {len(flagged['full'])}"
assert len(flagged["digest"]) == len(clean["digest"]), \
    f"flagging collapsed the digest tier: {len(clean['digest'])} -> {len(flagged['digest'])}"
warn = [l for l in md.splitlines() if l.startswith("\u26a0")]
assert warn, "flagged pages produced no warning at all"
assert len(warn) <= 13, f"warning section unbounded: {len(warn)} lines"
PYEOF
echo "contradictions demote, not erase, ok"

# --- symbol precision round 3 + check gate (regression guard) --------------
python3 - << 'PYEOF' || { echo "FAIL: symbol precision round 3"; exit 1; }
import io, contextlib, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, check, retrieval, structure

# destructured exports bind several names in one statement
names = []
for m in structure.JS_SYMBOL_RE.finditer(
        "export const { a, b } = obj;\nexport const [x, y] = arr;\n"):
    g = m.groupdict()
    if g["destr"]:
        for part in g["destr"].strip("{}[]").split(","):
            part = part.split("=")[0].split(":")[-1].strip()
            if part.isidentifier():
                names.append(part)
assert names == ["a", "b", "x", "y"], f"destructured exports: {names}"

# a page with no version at all means synthesis never ran; staleness_score is
# 0 so fail_on_staleness cannot see it, and the gate used to exit 0
d = pathlib.Path(tempfile.mkdtemp()); (d / ".irag").mkdir()
dbp = d / ".irag" / "memory.db"; db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(d)
conn.execute("INSERT INTO pages(page_type,title,subject_type,subject_id)"
             " VALUES('module','a.py','module','a.py')")
conn.commit()
with contextlib.redirect_stdout(io.StringIO()):
    rc = check.run(conn, cfg)
assert rc == 1, "irag check passed with a never-synthesized page"
cfg["check"]["fail_on_unsynthesized"] = False
with contextlib.redirect_stdout(io.StringIO()):
    assert check.run(conn, cfg) == 0, "the gate must stay opt-out"

# search excerpts are read by agents: no markers injected into real text
c = conn.execute("INSERT INTO pages(page_type,title,subject_type,subject_id)"
                 " VALUES('module','s','module','src/store.ts')")
r = conn.execute("INSERT INTO revisions(page_id,version_number,body_markdown,"
                 "change_summary) VALUES(?,1,'state lives in src/store.ts','x')",
                 (c.lastrowid,))
conn.execute("UPDATE pages SET current_revision_id=? WHERE page_id=?",
             (r.lastrowid, c.lastrowid))
conn.commit()
hits = retrieval.search(conn, "store")
assert hits, "FTS search returned nothing"
assert "[" not in hits[0]["snippet"], f"markers in excerpt: {hits[0]['snippet']!r}"
PYEOF
echo "symbol precision round 3 + check gate ok"

# --- concurrent sessions + map kinds (regression guard) --------------------
python3 - << 'PYEOF' || { echo "FAIL: concurrent sessions / map kinds"; exit 1; }
import pathlib, sqlite3, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, sessions, structure, retrieval

# two agents on one repo: begin() force-closed the other's live session and
# end() closed whichever row was open, so one agent's work was narrated into
# the other's diary entry and the other's was never recorded at all
sp = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(sp)
conn = db.connect(str(sp)); cfg = config.load(sp.parent.parent)
a = sessions.begin(conn, agent="claude-code", key="conv-A")
b = sessions.begin(conn, agent="agy", key="conv-B")
ra = sessions.end(conn, cfg, narrate=False, key="conv-A")
rb = sessions.end(conn, cfg, narrate=False, key="conv-B")
assert ra and ra["session_id"] == a, "agent A closed someone else's session"
assert rb and rb["session_id"] == b, "agent B's session was never closed"
rows = conn.execute("SELECT status FROM sessions ORDER BY session_id").fetchall()
assert all(r["status"] == "closed" for r in rows), \
    f"a live session was force-interrupted: {[r['status'] for r in rows]}"

# irag map labelled every const/let/var "function", including numbers,
# objects and arrays - and CLAUDE.md tells agents to trust that map
root = pathlib.Path(tempfile.mkdtemp()) / "p"
(root / "src").mkdir(parents=True)
subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
(root / "src" / "g.ts").write_text(
    "export const RADIUS = 12.5;\nexport function draw(){}\n"
    "export class Node {}\nexport interface P { a: 1 }\n")
subprocess.run(["git", "add", "-A"], cwd=root, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "i"], cwd=root, check=True)
(root / ".irag").mkdir(exist_ok=True)
mp = root / ".irag" / "memory.db"; db.ensure_db(mp)
c2 = db.connect(str(mp)); cfg2 = config.load(root)
structure.scan(c2, cfg2, root, force=True)
kinds = dict(c2.execute("SELECT name, kind FROM symbols").fetchall())
assert kinds.get("RADIUS") == "const", f"constant mislabelled: {kinds}"
assert kinds.get("draw") == "function", kinds
assert kinds.get("Node") == "class", kinds
assert kinds.get("P") == "type", kinds

# a locked or broken database must not be indistinguishable from "no matches"
assert retrieval.search(c2, 'bad "quote AND (') == [], "FTS syntax error should be swallowed"
c2.execute("DROP TABLE revisions_fts")
try:
    retrieval.search(c2, "anything")
    raise AssertionError("a missing table still masqueraded as no matches")
except sqlite3.OperationalError:
    pass
PYEOF
echo "concurrent sessions + map kinds ok"

# --- asof validation + per-event attribution (regression guard) ------------
python3 - << 'PYEOF' || { echo "FAIL: asof / attribution"; exit 1; }
import json, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, sessions
from irag.cli import _iso_date

# the date is compared lexically in SQL, so an unvalidated string returned the
# PRESENT state under a historical banner instead of failing
assert _iso_date("2026-06-01") == "2026-06-01"
assert _iso_date("2026-6-1") == "2026-06-01", "unpadded date must normalise"
for bad in ("June 1 2026", "yesterday", "src/store.ts", ""):
    try:
        _iso_date(bad)
        raise AssertionError(f"{bad!r} accepted as a date")
    except SystemExit:
        pass

# ownership fixed WHO owns a session; this covers WHICH events were theirs.
# The window was "everything since I started" - unbounded and unowned - so
# each session claimed the other's files and wrote false history into the
# diary that irag recap feeds forward.
dbp = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(dbp.parent.parent)
a = sessions.begin(conn, agent="claude-code", key="conv-A")
b = sessions.begin(conn, agent="agy", key="conv-B")
c = conn.execute("INSERT INTO pages(page_type,title,subject_type,subject_id)"
                 " VALUES('module','b_only.py','module','b_only.py')")
conn.execute("INSERT INTO events(event_type,source_ref,subject_id,payload,"
             "session_key) VALUES('snapshot','r','b_only.py','{}','conv-B')")
conn.execute("INSERT INTO revisions(page_id,version_number,body_markdown,"
             "change_summary,session_key) VALUES(?,1,'b','changed','conv-B')",
             (c.lastrowid,))
conn.commit()
ra = sessions.end(conn, cfg, narrate=False, key="conv-A")
rb = sessions.end(conn, cfg, narrate=False, key="conv-B")
a_files = json.loads(conn.execute(
    "SELECT files_changed FROM sessions WHERE session_id=?",
    (ra["session_id"],)).fetchone()[0] or "[]")
b_files = json.loads(conn.execute(
    "SELECT files_changed FROM sessions WHERE session_id=?",
    (rb["session_id"],)).fetchone()[0] or "[]")
assert "b_only.py" not in a_files, f"A claimed B's work: {a_files}"
assert "b_only.py" in b_files, f"B lost its own work: {b_files}"
PYEOF
echo "asof validation + attribution ok"

# --- attribution across agent kinds (regression guard) ---------------------
python3 - << 'PYEOF' || { echo "FAIL: cross-agent attribution"; exit 1; }
import json, pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, sessions

dbp = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(dbp.parent.parent)

def write(subject, key):
    c = conn.execute("INSERT INTO pages(page_type,title,subject_type,subject_id)"
                     " VALUES('module',?,'module',?)", (subject, subject))
    conn.execute("INSERT INTO events(event_type,source_ref,subject_id,payload,"
                 "session_key) VALUES('snapshot','r',?,'{}',?)", (subject, key))
    conn.execute("INSERT INTO revisions(page_id,version_number,body_markdown,"
                 "change_summary,session_key) VALUES(?,1,'b','c',?)",
                 (c.lastrowid, key))
    conn.commit()

# a keyless session still gets a key minted, so "no key" is never the normal
# state - an agent that supplies no hook payload used to write unstamped rows
# that every other keyed session then claimed
lone = sessions.begin(conn, agent="agy")
lone_key = conn.execute("SELECT session_key FROM sessions WHERE session_id=?",
                        (lone,)).fetchone()["session_key"]
assert lone_key, "a keyless session must still be given a key"
write("agy_file.py", lone_key)
rec = sessions.end(conn, cfg, narrate=False)      # no --id: must still close it
assert rec and rec["session_id"] == lone, "a keyless agent could not close its own session"
files = json.loads(conn.execute(
    "SELECT files_changed FROM sessions WHERE session_id=?", (lone,)
).fetchone()[0] or "[]")
assert files == ["agy_file.py"], f"lone agent lost its own work: {files}"

# concurrent: a keyed session must not absorb another agent's unstamped work
keyed = sessions.begin(conn, agent="claude-code", key="conv-C")
other = sessions.begin(conn, agent="agy")
write("not_mine.py", "proc-999-abcdef")           # attributable to neither
sessions.end(conn, cfg, narrate=False, key="conv-C")
kf = json.loads(conn.execute(
    "SELECT files_changed FROM sessions WHERE session_id=?", (keyed,)
).fetchone()[0] or "[]")
assert "not_mine.py" not in kf, f"keyed session claimed foreign work: {kf}"
PYEOF
echo "cross-agent attribution ok"

# --- the active key must never be cleared (regression guard) ---------------
# cmd_update called set_active_key with an optional --id, which is None
# without a hook payload, wiping the key _open() had just resolved: with
# strict matching every write became unattributable and the diary reported
# that nothing had happened. Set-only removes the whole class.
python3 - << 'PYEOF' || { echo "FAIL: active key regression"; exit 1; }
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db
db.set_active_key("resolved-key")
db.set_active_key(None)
assert db.active_key() == "resolved-key", "a falsy key cleared a resolved one"
db.set_active_key("")
assert db.active_key() == "resolved-key", "an empty key cleared a resolved one"
db.set_active_key("explicit")
assert db.active_key() == "explicit", "an explicit key must win"
PYEOF
# and the flag exists on every write path, so an agent can always say who it is
for c in update sync synthesize ingest-commit session-begin session-end; do
  python3 -m irag "$c" --help 2>&1 | grep -q -- "--id" \
    || { echo "FAIL: 'irag $c' has no --id, so a concurrent agent cannot identify itself"; exit 1; }
done
echo "active key + --id coverage ok"

# --- unidentified concurrent agents (regression guard) ---------------------
python3 - << 'PYEOF' || { echo "FAIL: unidentified concurrent agents"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, sessions

dbp = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(dbp.parent.parent)

# begin() must not reap a LIVE session it cannot prove is dead: force-closing
# the keyless lineage meant a second unidentified agent killed the first one's
# running session, which then reported 'interrupted' despite completing
one = sessions.begin(conn, agent="agy")
two = sessions.begin(conn, agent="cursor")
states = dict(conn.execute("SELECT session_id, status FROM sessions").fetchall())
assert states[one] == "open", f"a live keyless session was reaped: {states}"
assert states[two] == "open", states

# and end() must refuse rather than close a stranger's
try:
    sessions.end(conn, cfg, narrate=False)
    raise AssertionError("end() guessed between two unidentified sessions")
except SystemExit:
    pass

# each closes cleanly by its own key - the key session-begin now prints
k1 = conn.execute("SELECT session_key FROM sessions WHERE session_id=?",
                  (one,)).fetchone()["session_key"]
k2 = conn.execute("SELECT session_key FROM sessions WHERE session_id=?",
                  (two,)).fetchone()["session_key"]
assert k1 and k2 and k1 != k2, "sessions must get distinct keys"
assert sessions.end(conn, cfg, narrate=False, key=k1)["session_id"] == one
assert sessions.end(conn, cfg, narrate=False, key=k2)["session_id"] == two

# a lone keyless agent still needs no id at all
solo = sessions.begin(conn, agent="agy")
rec = sessions.end(conn, cfg, narrate=False)
assert rec and rec["session_id"] == solo, "the sole open session must still close"
PYEOF
echo "unidentified concurrent agents ok"

# --- stranded sessions must be escapable (regression guard) ----------------
# A refused session-end left rows open forever, after which _resolve_key could
# never again see "exactly one open" - so one refusal silently cost every
# later agent its attribution, with no way out but editing SQLite by hand.
python3 - << 'PYEOF' || { echo "FAIL: stranded session recovery"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, sessions

dbp = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(dbp.parent.parent)
a = sessions.begin(conn, agent="agy")
b = sessions.begin(conn, agent="cursor")
try:
    sessions.end(conn, cfg, narrate=False)
    raise AssertionError("end() guessed between unidentified sessions")
except SystemExit as exc:
    text = str(exc)
    # the message has to be followable: it must name each session AND its key
    assert "--id" in text, text
    for sid in (a, b):
        assert f"--id {sid}" in text, f"session {sid} not offered: {text}"
    assert "--stale" in text, "no escape hatch offered"

# --id must accept the session id the message printed, not only the key
rec = sessions.end(conn, cfg, narrate=False, key=str(a))
assert rec and rec["session_id"] == a, "--id did not accept a session id"

# and --all clears whatever is left. (--stale is age-filtered now, so it
# deliberately spares a session that started seconds ago; see the
# cross-attribution block below.)
left = sessions.end_stale(conn, cfg, everything=True)
assert left == [b], f"--all should have closed {b}, closed {left}"
assert not conn.execute("SELECT 1 FROM sessions WHERE status='open'").fetchone(), \
    "sessions still open after --stale"
PYEOF
echo "stranded session recovery ok"

# --- no cross-attribution to a foreign session (regression guard) ----------
python3 - << 'PYEOF' || { echo "FAIL: cross-attribution"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, sessions
from irag.cli import _resolve_key

dbp = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(dbp.parent.parent)

# A session opened with a REAL id belongs to an agent whose every call carries
# that id (Claude Code hooks). So an unkeyed write cannot be theirs, and
# adopting their key would file one agent's commits in another's diary.
sessions.begin(conn, agent="claude-code", key="CC-live")
assert _resolve_key(conn) != "CC-live", \
    "an unkeyed write adopted a keyed session - cross-attribution"
sessions.end(conn, cfg, narrate=False, key="CC-live")

# but a lone UNIDENTIFIED session is plausibly the writer's own, and must
# still be adopted or a keyless agent loses its own work
solo = sessions.begin(conn, agent="agy")
solo_key = conn.execute("SELECT session_key FROM sessions WHERE session_id=?",
                        (solo,)).fetchone()["session_key"]
assert _resolve_key(conn) == solo_key, "a lone keyless agent lost attribution"

# --stale means stale: it must not end a conversation that started seconds ago
assert sessions.end_stale(conn, cfg) == [], "--stale closed a live session"
conn.execute("UPDATE sessions SET started_at=datetime('now','-30 hours') "
             "WHERE session_id=?", (solo,))
conn.commit()
assert sessions.end_stale(conn, cfg) == [solo], "--stale skipped an aged session"
# --all is the blunt one, and says so
live = sessions.begin(conn, agent="cursor")
assert sessions.end_stale(conn, cfg, everything=True) == [live]
PYEOF
echo "cross-attribution + stale semantics ok"

# --- why must not pass off history as current (regression guard) -----------
# `why` searches every revision, which is right for provenance - but it is
# also the command an agent uses to check whether memory asserts something,
# and it answered "yes, here it is" from a superseded revision with no hint.
python3 - << 'PYEOF' || { echo "FAIL: why current/historical"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, provenance

p = pathlib.Path(tempfile.mkdtemp()) / ".irag" / "memory.db"
db.ensure_db(p); conn = db.connect(str(p))
c = conn.execute("INSERT INTO pages(page_type,title,subject_type,subject_id)"
                 " VALUES('module','s','module','src/store.ts')")
pid = c.lastrowid
def rev(n, body, summary):
    r = conn.execute("INSERT INTO revisions(page_id,version_number,"
                     "body_markdown,change_summary,llm_model_used) "
                     "VALUES(?,?,?,?,'test')", (pid, n, body, summary))
    conn.execute("UPDATE pages SET current_revision_id=? WHERE page_id=?",
                 (r.lastrowid, pid))
    conn.commit()
rev(1, "the store holds state", "initial")
rev(2, "the store holds state and flibbertigibbet", "added it")
rev(3, "the store holds state", "removed it")

gone = provenance.why_data(conn, "flibbertigibbet")
assert gone, "why found nothing for a claim that did exist"
assert gone["is_current"] is False, "a v2 match reported as current"
assert gone["still_holds"] is False, \
    "a claim deleted in v3 reported as still holding"
assert gone["current_version"] == 3, gone["current_version"]

kept = provenance.why_data(conn, "state")
assert kept and kept["still_holds"] is True, \
    "a claim present in the current revision reported as gone"
PYEOF
echo "why current/historical ok"

# --- vault note names must be unique (regression guard) --------------------
# _slug collapsed every run of non-[\w.-] to "-", so src/a.py and src-a.py
# both became src-a.py: one page overwrote the other in the vault, silently,
# leaving the survivor's content under a name matching the file it is NOT.
python3 - << 'PYEOF' || { echo "FAIL: vault slug collision"; exit 1; }
import pathlib, re, subprocess, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, obsidian

root = pathlib.Path(tempfile.mkdtemp()) / "p"
(root / "src").mkdir(parents=True)
subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
(root / ".irag").mkdir()
dbp = root / ".irag" / "memory.db"; db.ensure_db(dbp)
conn = db.connect(str(dbp)); cfg = config.load(root)
cases = ["src/a.py", "src-a.py", "a/b/c.py", "a-b/c.py", "a/b-c.py",
         "src/clean.py"]
for sid in cases:
    c = conn.execute("INSERT INTO pages(page_type,title,subject_type,"
                     "subject_id) VALUES('module',?,'module',?)", (sid, sid))
    r = conn.execute("INSERT INTO revisions(page_id,version_number,"
                     "body_markdown,change_summary) VALUES(?,1,?,'x')",
                     (c.lastrowid, f"body of {sid}"))
    conn.execute("UPDATE pages SET current_revision_id=? WHERE page_id=?",
                 (r.lastrowid, c.lastrowid))
conn.commit()
vault = obsidian.export_vault(conn, cfg, root)

mods = sorted((vault / "Modules").glob("*.md"))
assert len(mods) == len(cases), \
    f"vault lost pages to name collisions: {len(mods)} notes for {len(cases)} pages"
# each note must carry ITS OWN subject's body, not a colliding page's
for m in mods:
    text = m.read_text()
    subject = [l.split(": ", 1)[1] for l in text.splitlines()
               if l.startswith("subject: ")][0]
    assert f"body of {subject}" in text, \
        f"{m.name} holds the wrong page's content (subject {subject})"
# a subject that collides with nothing keeps a clean, unsuffixed name
assert (vault / "Modules" / "src-clean.py.md").exists(), \
    "a non-colliding subject was needlessly suffixed"
# and every wikilink still resolves
notes = {f.stem for f in vault.rglob("*.md")}
links = set()
for f in vault.rglob("*.md"):
    links |= set(re.findall(r"\[\[([^\]]+)\]\]", f.read_text()))
dangling = sorted(l for l in links if l not in notes)
assert not dangling, f"dangling wikilinks after disambiguation: {dangling}"
PYEOF
echo "vault note uniqueness ok"

# --- binary content must not be synthesized as source (regression guard) ---
# Detection was extension-only, so a .py holding a pickle, a misnamed
# artifact, or a UTF-16 source earned a page and had its raw bytes pasted into
# the synthesis prompt: one LLM call spent, and a junk page agents then read.
python3 - << 'PYEOF' || { echo "FAIL: binary content detection"; exit 1; }
import pathlib, sys, tempfile
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import ingest, config

d = pathlib.Path(tempfile.mkdtemp())
(d / ".irag").mkdir(); (d / "src").mkdir()
cfg = config.load(d)
(d / "src" / "binary.py").write_bytes(b"\x00\x01\x02binary\xff garbage")
(d / "src" / "utf16.py").write_bytes("def beta(): pass\n".encode("utf-16"))
(d / "src" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
(d / "src" / "real.py").write_text("def alpha(): return 1\n")
(d / "src" / "unicode.py").write_text("# cafe 日本語 🚀\ndef g(): pass\n")
(d / "src" / "empty.py").write_text("")

for name in ("src/binary.py", "src/utf16.py", "src/image.png"):
    assert ingest.file_subject(name, cfg, d) is None, \
        f"{name} was accepted as source and would be sent to the LLM"
for name in ("src/real.py", "src/unicode.py", "src/empty.py"):
    assert ingest.file_subject(name, cfg, d) == name, \
        f"{name} is real source but was rejected as binary"
PYEOF
echo "binary content detection ok"

# --- source that turns binary must not leave a stale page ------------------
# file_subject now returns None for binary content, which makes such a file
# invisible to the snapshot diff: no event, no staleness, no tombstone. The
# page kept asserting functions that no longer exist, in a file that is no
# longer code, and at staleness 0 would never be re-synthesized to correct
# itself. Deletion must STILL tombstone rather than purge.
BF=$(mktemp -d)
mkdir -p "$BF/src"
( cd "$BF" && git init -q . )
printf 'def alpha(): return 1\n' > "$BF/src/keep.py"
printf 'def beta(): return 2\n'  > "$BF/src/gone.py"
printf 'def gamma(): return 3\n' > "$BF/src/turns_binary.py"
( cd "$BF" && git add -A && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
( cd "$BF" && python3 - << 'PYEOF'
import pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, ingest
root = pathlib.Path(".").resolve()
conn = db.connect(str(root / ".irag" / "memory.db")); cfg = config.load(root)
ingest.sync(conn, cfg, root)
base = {r[0] for r in conn.execute(
    "SELECT subject_id FROM pages WHERE subject_type='file'")}
assert "src/turns_binary.py" in base, base
(root / "src" / "gone.py").unlink()
(root / "src" / "turns_binary.py").write_bytes(b"\x00\x01compiled")
subprocess.run(["git", "add", "-A"], cwd=root, check=True)
subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                "commit", "-qm", "c"], cwd=root, check=True)
ingest.sync(conn, cfg, root)
now = {r[0] for r in conn.execute(
    "SELECT subject_id FROM pages WHERE subject_type='file'")}
assert "src/keep.py" in now, f"a live source page was purged: {now}"
assert "src/gone.py" in now, f"a deleted file lost its tombstone: {now}"
assert "src/turns_binary.py" not in now, \
    f"a file that turned binary kept its stale page: {now}"
PYEOF
) || { echo "FAIL: source-turns-binary purge"; rm -rf "$BF"; exit 1; }
rm -rf "$BF"
echo "source-turns-binary purge ok"

# --- web dep edges: HTML/CSS wiring and dynamic imports -----------------
# `irag impact` answering "change is contained" about a file the whole page
# loads is worse than no answer: it licenses the unsafe edit. Guards that
# <script src>, <link href>, root-absolute specifiers and dynamic import()
# all become edges, and that CDN/bare specifiers still do NOT.
WD=$(mktemp -d); mkdir -p "$WD/static"
cat > "$WD/index.html" <<'HTMLEOF'
<link rel="stylesheet" href="/static/styles.css">
<link rel="stylesheet" href="https://cdn.example.com/lib.css">
<img src="/static/logo.png">
<script src="/static/app.js" type="module"></script>
<script src="//cdn.example.com/a.js"></script>
HTMLEOF
cat > "$WD/static/app.js" <<'JSEOF'
import { helper } from "./helper.js";
import * as THREE from "three";
import "three/addons/controls/OrbitControls.js";
const mod = await import("/static/topology3d.js");
export function boot(){ return helper(mod, THREE); }
JSEOF
printf 'export function helper(m){ return m; }\n' > "$WD/static/helper.js"
printf 'export function render(){ return 1; }\n' > "$WD/static/topology3d.js"
printf '@import "./base.css";\nbody{color:red}\n' > "$WD/static/styles.css"
printf 'body{margin:0}\n' > "$WD/static/base.css"
( cd "$WD" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 \
  && python3 -m irag scan >/dev/null 2>&1 )
( cd "$WD" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config
root = pathlib.Path(".").resolve()
conn = db.connect(str(root / ".irag" / "memory.db")); config.load(root)
edges = {(r["source_subject"], r["target_subject"]) for r in conn.execute(
    "SELECT source_subject, target_subject FROM deps")}
must = {("index.html", "static/app.js"),        # <script src>, root-absolute
        ("index.html", "static/styles.css"),    # <link href>
        ("static/app.js", "static/helper.js"),  # relative ESM
        ("static/app.js", "static/topology3d.js"),  # dynamic import()
        ("static/styles.css", "static/base.css")}   # css @import
missing = must - edges
assert not missing, f"missing web dep edges: {missing}"
bad = [e for e in edges if "cdn" in e[1] or e[1].startswith("three")
       or e[1].endswith(".png")]
assert not bad, f"invented edges for non-repo targets: {bad}"
PYEOF
) || { echo "FAIL: web dep edges"; rm -rf "$WD"; exit 1; }
rm -rf "$WD"
echo "web dep edges (html/css/dynamic import) ok"

# --- stuck pages must not report success --------------------------------
# A page holding a queued change with zero staleness can never become due:
# the change is recorded, then zeroed, and 'irag update' would print
# "update done: 0" and exit 0 forever while memory rots. CLAUDE.md tells
# agents a redundant update is free and to move on, so this must exit 1.
SK=$(mktemp -d); mkdir -p "$SK/src"
printf 'def a(): return 1\n' > "$SK/src/a.py"
( cd "$SK" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
python3 - "$IRAG_SRC" "$SK" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[2], ".irag/config.toml"); t = cfg.read_text()
cfg.write_text(t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"'))
PYEOF
( cd "$SK" && python3 -m irag update >/dev/null 2>&1 ) || true
# a healthy repo must still exit 0 (guards against a false positive)
( cd "$SK" && python3 -m irag update >/dev/null 2>&1 ) \
  || { echo "FAIL: update exits non-zero on a healthy repo"; rm -rf "$SK"; exit 1; }
( cd "$SK" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db
conn = db.connect(pathlib.Path(".irag/memory.db"))
conn.execute("INSERT INTO events(event_type, source_ref, subject_id, payload)"
             " VALUES('commit','stuckref','src/a.py','{}')")
conn.execute("UPDATE pages SET staleness_score=0 WHERE subject_id='src/a.py'")
conn.commit()
PYEOF
)
if ( cd "$SK" && python3 -m irag update >/dev/null 2>&1 ); then
  echo "FAIL: update reported success while a page was stuck"; rm -rf "$SK"; exit 1
fi
( cd "$SK" && python3 -m irag update 2>&1 | grep -q "STUCK" ) \
  || { echo "FAIL: stuck page not named in output"; rm -rf "$SK"; exit 1; }
# the recovery it recommends must actually clear the state
( cd "$SK" && python3 -m irag synthesize --subject src/a.py >/dev/null 2>&1 \
  && python3 -m irag update >/dev/null 2>&1 ) \
  || { echo "FAIL: recommended recovery did not clear the stuck page"; \
       rm -rf "$SK"; exit 1; }
rm -rf "$SK"
echo "stuck-page detection + recovery ok"

# --- resolutions survive re-synthesis -----------------------------------
# Dedup only matched OPEN rows, so resolving a contradiction hid it from
# the check and the next lint re-raised the identical claim under a new id.
# An agent whose judgement is overturned every update learns to ignore the
# linter wholesale. Manual dismissals must stick; auto-resolutions must NOT,
# or a real regression would be silently suppressed.
RS=$(mktemp -d); mkdir -p "$RS/src"
printf 'def login():\n    pass\n' > "$RS/src/auth.py"
( cd "$RS" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
python3 - "$IRAG_SRC" "$RS" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[2], ".irag/config.toml"); t = cfg.read_text()
cfg.write_text(t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"'))
PYEOF
( cd "$RS" && python3 -m irag update >/dev/null 2>&1 ) || true
( cd "$RS" && python3 -m irag contradictions | grep -q "OPEN" ) \
  || { echo "FAIL: expected a contradiction to lint against"; rm -rf "$RS"; exit 1; }
CID=$( cd "$RS" && python3 -m irag contradictions --json \
       | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["contradiction_id"])' )
( cd "$RS" && python3 -m irag resolve "$CID" --notes "false positive" >/dev/null 2>&1 )
# Assert on what lint() RETURNS (contradictions added). Checking the open
# set instead would be masked by the connect-time upgrade cleanup in db.py,
# which closes duplicates on the next connect — that made an earlier version
# of this guard pass even with the suppression removed.
( cd "$RS" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, linter
root = pathlib.Path.cwd()
conn = db.connect(root / ".irag" / "memory.db"); cfg = config.load(root)
row = conn.execute(
    "SELECT page_id, claim, ctype FROM contradictions "
    "WHERE resolution_kind='manual' LIMIT 1").fetchone()
assert row, "test setup: expected a manually resolved contradiction"
added = linter.lint(conn, cfg, root)
assert added == 0, (
    f"re-lint re-raised {added} contradiction(s) after a human dismissed "
    "them; per-ID resolution is being defeated by re-synthesis")
assert linter._dismissed_claim(conn, row["page_id"], row["claim"],
                               row["ctype"]), "manual dismissal not honoured"
# an AUTO-resolved claim must stay re-raisable, or real regressions hide
conn.execute("UPDATE contradictions SET resolution_kind='auto' "
             "WHERE page_id=? AND claim=?", (row["page_id"], row["claim"]))
conn.commit()
assert not linter._dismissed_claim(conn, row["page_id"], row["claim"],
                                   row["ctype"]), \
    "an auto-resolved claim was treated as dismissed — regressions would hide"
PYEOF
) || { echo "FAIL: resolutions must survive re-synthesis"; rm -rf "$RS"; exit 1; }
rm -rf "$RS"
echo "resolutions survive re-synthesis ok"

# --- drift / bare specifiers / orphan attribution -----------------------
DR=$(mktemp -d); mkdir -p "$DR/src" "$DR/node_modules/lodash"
printf 'def a(): return 1\n' > "$DR/src/a.py"
printf '{"dependencies":{"three":"^0.160.0"},"devDependencies":{"@types/node":"^20"}}\n' \
  > "$DR/package.json"
cat > "$DR/index.html" <<'HTMLEOF'
<script type="importmap">
{"imports":{"three":"https://cdn.skypack.dev/three",
            "three/addons/":"https://cdn.skypack.dev/three/examples/jsm/"}}
</script>
HTMLEOF
printf 'requests==2.31.0\nflask>=2.0\n' > "$DR/requirements.txt"
( cd "$DR" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
python3 - "$IRAG_SRC" "$DR" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[2], ".irag/config.toml"); t = cfg.read_text()
cfg.write_text(t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"'))
PYEOF
# a session whose work arrives WITHOUT its id (manual update / dashboard)
( cd "$DR" && python3 -m irag session-begin --id cc-xyz --agent claude-code \
    >/dev/null 2>&1 )
sleep 1
printf 'def a(): return 999\ndef b(): pass\n' > "$DR/src/a.py"
( cd "$DR" && python3 -m irag update >/dev/null 2>&1 ) || true
( cd "$DR" && python3 -m irag session-end --id cc-xyz >/dev/null 2>&1 ) || true
( cd "$DR" && python3 - << 'PYEOF'
import json, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
out = subprocess.run([sys.executable, "-m", "irag", "sessions", "--json"],
                     capture_output=True, text=True).stdout
rows = json.loads(out)
assert rows and rows[0]["versions_written"] > 0, (
    "a lone session got zero attribution for work done without its id: "
    f"{rows}")
from irag import db, config, linter
root = pathlib.Path.cwd()
conn = db.connect(root / ".irag" / "memory.db"); cfg = config.load(root)
# bare specifiers must not be reported missing; real misses still must be
db.get_or_create_page(conn, "src/a.py", subject_type="file", page_type="file")
pid = conn.execute("SELECT page_id FROM pages WHERE subject_id='src/a.py'"
                   ).fetchone()["page_id"]
nxt = conn.execute("SELECT COALESCE(MAX(version_number),0)+1 v FROM revisions "
                   "WHERE page_id=?", (pid,)).fetchone()["v"]
conn.execute("INSERT INTO revisions(page_id, version_number, body_markdown,"
             " tokens_used) VALUES(?,?,?,0)", (pid, nxt,
             "Uses `three/addons/controls/OrbitControls.js`, "
             "`@types/node/index.d.ts`, `lodash/debounce.js`, `flask/app.py` "
             "and a missing `src/ghost.js`."))
conn.commit()
linter.lint(conn, cfg, root)
open_claims = {r["claim"] for r in conn.execute(
    "SELECT claim FROM contradictions WHERE resolved_at IS NULL")}
bad = [c for c in open_claims if "three/" in c or "@types/" in c
       or "lodash/" in c or "flask/" in c]
assert not bad, f"bare package specifiers reported as missing files: {bad}"
assert any("src/ghost.js" in c for c in open_claims), \
    f"a genuinely missing path stopped being flagged: {open_claims}"
# status must surface files edited since their page was written
import time; time.sleep(1)
(root / "src" / "a.py").write_text("def a(): return 0\n# drifted\n")
from irag import ingest
drifted = ingest.drifted_files(conn, cfg, root)
assert "src/a.py" in drifted, f"edited file not reported as drifted: {drifted}"
PYEOF
) || { echo "FAIL: drift / specifiers / attribution"; rm -rf "$DR"; exit 1; }
rm -rf "$DR"
echo "drift + bare specifiers + orphan attribution ok"

# --- executable memory, anti-facts, topics, brief, trivial-skip ---------
FT=$(mktemp -d); mkdir -p "$FT/src"
printf 'import sys\ndef scan(root=False):\n    if not root:\n        print("QUITTING")\n        sys.exit(1)\n    return 1\nscan()\n' \
  > "$FT/src/scanner.py"
printf 'def netcheck():\n    return 2\n' > "$FT/src/net.py"
( cd "$FT" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
python3 - "$IRAG_SRC" "$FT" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[2], ".irag/config.toml"); t = cfg.read_text()
t = t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"')
# facts are opt-in (they shell out); this block is exercising them
t = t.replace("fail_on_facts = false", "fail_on_facts = true")
cfg.write_text(t)
PYEOF
( cd "$FT" && python3 -m irag update >/dev/null 2>&1 ) || true

# executable memory: record a proof, then break the behaviour
( cd "$FT" && python3 -m irag verify "scan aborts without root" \
    --cmd "python3 src/scanner.py" --expect "QUITTING" \
    --module src/scanner.py >/dev/null 2>&1 ) \
  || { echo "FAIL: a true behavioural claim did not verify when recorded"; \
       rm -rf "$FT"; exit 1; }
printf 'import sys\nsys.exit(0)\n' > "$FT/src/scanner.py"
if ( cd "$FT" && python3 -m irag verify >/dev/null 2>&1 ); then
  echo "FAIL: a broken behavioural claim still verified"; rm -rf "$FT"; exit 1
fi
( cd "$FT" && python3 -m irag check 2>&1 | grep -q "behavioural claims no longer hold" ) \
  || { echo "FAIL: check did not report the broken fact"; rm -rf "$FT"; exit 1; }
# ...and check must pass it again once the behaviour is restored
printf 'import sys\ndef scan(root=False):\n    if not root:\n        print("QUITTING")\n        sys.exit(1)\n    return 1\nscan()\n' \
  > "$FT/src/scanner.py"
( cd "$FT" && python3 -m irag verify >/dev/null 2>&1 ) \
  || { echo "FAIL: restored behaviour did not re-verify"; rm -rf "$FT"; exit 1; }

# anti-facts + brief
( cd "$FT" && python3 -m irag tried "position:sticky" \
    --because "only catches after you scroll past" --module src/scanner.py \
    >/dev/null 2>&1 )
( cd "$FT" && python3 -m irag brief src/scanner.py | grep -q "already ruled out" ) \
  || { echo "FAIL: brief did not surface a recorded dead end"; rm -rf "$FT"; exit 1; }
( cd "$FT" && echo '{"tool_input":{"file_path":"src/scanner.py"}}' \
    | python3 -m irag brief - | grep -q "ruled out" ) \
  || { echo "FAIL: brief did not read the hook payload"; rm -rf "$FT"; exit 1; }

# topics: a concept page spanning files that share no folder
( cd "$FT" && python3 -m irag topic "root privilege" \
    --files src/scanner.py,src/net.py >/dev/null 2>&1 )
( cd "$FT" && python3 -m irag update >/dev/null 2>&1 ) || true
( cd "$FT" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db
conn = db.connect(pathlib.Path(".irag/memory.db"))
row = conn.execute("SELECT current_revision_id FROM pages "
                   "WHERE subject_type='topic' AND subject_id='root privilege'"
                   ).fetchone()
assert row and row["current_revision_id"], "topic page was never synthesized"
PYEOF
) || { echo "FAIL: topic page"; rm -rf "$FT"; exit 1; }

# candidate lessons are drafted from failures only
( cd "$FT" && echo '{"tool_input":{"command":"nmap -O"},"tool_response":{"exit_code":1,"stderr":"QUITTING"}}' \
    | python3 -m irag capture --quiet >/dev/null 2>&1 )
( cd "$FT" && echo '{"tool_input":{"command":"ls"},"tool_response":{"exit_code":0}}' \
    | python3 -m irag capture --quiet >/dev/null 2>&1 )
n=$( cd "$FT" && python3 -m irag candidates | grep -c CANDIDATE || true )
[ "$n" = "1" ] \
  || { echo "FAIL: expected exactly 1 candidate from a failure, got $n"; \
       rm -rf "$FT"; exit 1; }

# trivial-change skip must not swallow a real change
sleep 1
printf 'import sys\ndef scan(root=False):\n    if not root:\n        print("QUITTING")\n        sys.exit(1)\n    return 1\nscan()\n# reflowed comment\n' \
  > "$FT/src/scanner.py"
( cd "$FT" && python3 -m irag update 2>&1 | grep -q "unchanged" ) \
  || { echo "FAIL: comment-only edit still called the model"; rm -rf "$FT"; exit 1; }
sleep 1
printf 'import sys\ndef scan(root=False):\n    return 1\ndef brand_new():\n    return 9\n' \
  > "$FT/src/scanner.py"
( cd "$FT" && python3 -m irag update 2>&1 | grep -q "synthesized: src/scanner.py" ) \
  || { echo "FAIL: a real change was skipped as trivial"; rm -rf "$FT"; exit 1; }
rm -rf "$FT"
echo "executable memory + anti-facts + topics + brief + trivial-skip ok"

# --- verify: shell operators and missing --cmd --------------------------
# `--cmd "echo hi | grep hi"` was shlex-split into `echo` with the literal
# arguments `hi | grep hi`, whose output contains "hi" — so the fact PASSED
# while testing something the user never wrote. A false pass is the worst
# possible defect in the one feature whose whole claim is self-proof.
SH=$(mktemp -d)
printf 'hello world\n' > "$SH/data.txt"
( cd "$SH" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
( cd "$SH" && python3 -m irag verify "pipeline true" \
    --cmd "grep hello data.txt | wc -l" --expect 1 >/dev/null 2>&1 ) \
  || { echo "FAIL: a true pipeline claim did not verify"; rm -rf "$SH"; exit 1; }
if ( cd "$SH" && python3 -m irag verify "pipeline false" \
       --cmd "grep absent data.txt | wc -l" --expect 42 >/dev/null 2>&1 ); then
  echo "FAIL: a false pipeline claim passed — shell operators not honoured"
  rm -rf "$SH"; exit 1
fi
( cd "$SH" && python3 -m irag verify "chained" \
    --cmd "test -f data.txt && echo FOUND" --expect FOUND >/dev/null 2>&1 ) \
  || { echo "FAIL: && chaining not honoured"; rm -rf "$SH"; exit 1; }
# a claim with no proof must be a clean error, not a traceback
out=$( cd "$SH" && python3 -m irag verify "no proof" 2>&1 || true )
case "$out" in
  *Traceback*) echo "FAIL: 'verify' with no --cmd crashed:"; echo "$out" | tail -3
               rm -rf "$SH"; exit 1 ;;
  *"needs --cmd"*) : ;;
  *) echo "FAIL: unexpected output for a claim with no --cmd: $out"
     rm -rf "$SH"; exit 1 ;;
esac
rm -rf "$SH"
echo "verify shell operators + missing --cmd ok"

# --- impact must not claim "contained" about a path it does not track ---
# A typo, the wrong case, or an ignored file all produced the identical
# confident "change is contained" as a genuinely safe file, so a mistyped
# path read as permission to edit. Unknown subjects fail closed.
IM=$(mktemp -d); mkdir -p "$IM/src" "$IM/node_modules"
printf 'def a(): return 1\n' > "$IM/src/a.py"
printf 'x\n' > "$IM/node_modules/lib.js"
( cd "$IM" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 && python3 -m irag scan >/dev/null 2>&1 )
# a tracked file with no importers is the ONLY case that may say "contained"
( cd "$IM" && python3 -m irag impact src/a.py | grep -q "change is contained" ) \
  || { echo "FAIL: a tracked file lost its contained answer"; rm -rf "$IM"; exit 1; }
( cd "$IM" && python3 -m irag impact src/a.py >/dev/null 2>&1 ) \
  || { echo "FAIL: impact on a tracked file should exit 0"; rm -rf "$IM"; exit 1; }
for bad in src/nope.py src/a.pyy node_modules/lib.js; do
  if ( cd "$IM" && python3 -m irag impact "$bad" 2>&1 | grep -q "change is contained" ); then
    echo "FAIL: impact claimed containment for untracked path $bad"
    rm -rf "$IM"; exit 1
  fi
  if ( cd "$IM" && python3 -m irag impact "$bad" >/dev/null 2>&1 ); then
    echo "FAIL: impact exited 0 for untracked path $bad"; rm -rf "$IM"; exit 1
  fi
done
rm -rf "$IM"
echo "impact fails closed on untracked paths ok"

# --- non-Python symbol coverage ----------------------------------------
# `irag map` is documented to agents as parsed from the code and always
# current, so a whole category missing from it is a silent lie. Go had no
# type extraction at all; Rust skipped async/const/static/type-alias; Ruby
# skipped `private def` and constants; PHP and C skipped const/#define;
# C# skipped properties AND captured a phantom symbol named `int`.
LG=$(mktemp -d); mkdir -p "$LG/src"
cat > "$LG/src/main.go" <<'GOEOF'
package main
type Server struct { port int }
type Handler interface { Serve() error }
const MaxConn = 100
func NewServer(p int) *Server { return nil }
func generic[T any](x T) {}
GOEOF
cat > "$LG/src/lib.rs" <<'RSEOF'
pub struct Config { pub name: String }
pub const LIMIT: u32 = 10;
pub type Alias = String;
impl Config {
    pub async fn fetch(&self) -> u32 { 0 }
}
RSEOF
cat > "$LG/src/app.rb" <<'RBEOF'
class Session
  MAX_AGE = 3600
  private def hidden; end
end
RBEOF
cat > "$LG/src/svc.php" <<'PHPEOF'
<?php
class Service {
    const VERSION = "1.0";
    public function run() {}
}
PHPEOF
cat > "$LG/src/core.c" <<'CEOF'
#define MACRO_THING 42
int add(int a, int b) { return a + b; }
CEOF
cat > "$LG/src/Svc.cs" <<'CSEOF'
public class Service {
    public int Count { get; set; }
    public int this[int i] { get { return 0; } }
    public static implicit operator int(Service s) { return 0; }
}
CSEOF
cat > "$LG/src/App.java" <<'JAVAEOF'
public abstract class App {
    public abstract void mustImpl();
    native void nat();
    protected abstract String compute(int a) throws Exception;
    public void real() {
        helper();
        return;
    }
}
interface Greeter { void greet(); }
JAVAEOF
cat > "$LG/src/ops.rb" <<'OPSEOF'
class Ops
  def <=>(other); end
  def [](i); end
  def []=(i, v); end
  private def hidden; end
end
OPSEOF
( cd "$LG" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 \
  && python3 -m irag scan >/dev/null 2>&1 )
( cd "$LG" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db
conn = db.connect(pathlib.Path(".irag/memory.db"))
def names(f):
    return {r["name"] for r in conn.execute(
        "SELECT name FROM symbols WHERE file=?", (f,))}
want = {
    "src/main.go": {"Server", "Handler", "MaxConn", "NewServer", "generic"},
    "src/lib.rs": {"Config", "LIMIT", "Alias", "fetch"},
    "src/app.rb": {"Session", "MAX_AGE", "hidden"},
    "src/svc.php": {"Service", "VERSION", "run"},
    "src/core.c": {"MACRO_THING", "add"},
    "src/Svc.cs": {"Service", "Count", "this[]"},
    # bodiless declarations: interface, abstract and native methods
    "src/App.java": {"App", "Greeter", "mustImpl", "nat", "compute",
                     "greet", "real"},
    "src/ops.rb": {"Ops", "<=>", "[]", "[]=", "hidden"},
}
for f, expected in want.items():
    got = names(f)
    missing = expected - got
    assert not missing, f"{f}: symbols not indexed: {sorted(missing)} (got {sorted(got)})"
# a primitive type name is never a symbol
bad = names("src/Svc.cs") & {"int", "operator", "void", "string"}
assert not bad, f"phantom symbols captured from C#: {sorted(bad)}"
# a call statement is not a declaration
bad = names("src/App.java") & {"helper", "return", "if", "for"}
assert not bad, f"call statements captured as Java declarations: {sorted(bad)}"
PYEOF
) || { echo "FAIL: non-Python symbol coverage"; rm -rf "$LG"; exit 1; }
rm -rf "$LG"
echo "non-python symbol coverage (go/rust/ruby/php/c/c#) ok"

# --- dismissing a flag never edits the page, and is reversible ----------
# `resolve` marks the CHECK wrong, not the page — and since a manual
# dismissal permanently suppresses the claim, dismissing a genuine error
# would silence it forever with the wrong sentence still in the summary.
# So: the body must be untouched, suppression must hold, and undo must
# restore the claim to raisable.
RV=$(mktemp -d); mkdir -p "$RV/src"
printf 'def login():\n    pass\n' > "$RV/src/auth.py"
( cd "$RV" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
python3 - "$IRAG_SRC" "$RV" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[2], ".irag/config.toml"); t = cfg.read_text()
cfg.write_text(t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"'))
PYEOF
( cd "$RV" && python3 -m irag update >/dev/null 2>&1 ) || true
( cd "$RV" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, config, linter
root = pathlib.Path.cwd()
conn = db.connect(root / ".irag" / "memory.db"); cfg = config.load(root)

def body():
    r = conn.execute(
        "SELECT r.body_markdown b FROM pages p "
        "JOIN revisions r ON r.revision_id = p.current_revision_id "
        "WHERE p.subject_id='src/auth.py'").fetchone()
    return r["b"] if r else ""

def versions():
    return conn.execute(
        "SELECT COUNT(*) n FROM revisions r JOIN pages p "
        "ON p.page_id=r.page_id WHERE p.subject_id='src/auth.py'"
    ).fetchone()["n"]

row = conn.execute("SELECT contradiction_id, claim FROM contradictions "
                   "WHERE resolved_at IS NULL LIMIT 1").fetchone()
assert row, "test setup: expected an open contradiction"
cid = row["contradiction_id"]
before, nbefore = body(), versions()
linter.resolve(conn, cid, notes="spurious")
assert body() == before, "dismissing a flag rewrote the page body"
assert versions() == nbefore, "dismissing a flag wrote a new revision"
assert linter._dismissed_claim(
    conn, conn.execute("SELECT page_id FROM contradictions WHERE "
                       "contradiction_id=?", (cid,)).fetchone()["page_id"],
    row["claim"],
    conn.execute("SELECT ctype FROM contradictions WHERE contradiction_id=?",
                 (cid,)).fetchone()["ctype"]), "dismissal did not suppress"
# undo restores it to raisable
linter.undo_resolve(conn, cid)
pid = conn.execute("SELECT page_id, ctype FROM contradictions WHERE "
                   "contradiction_id=?", (cid,)).fetchone()
assert not linter._dismissed_claim(conn, pid["page_id"], row["claim"],
                                   pid["ctype"]), \
    "undo left the claim suppressed — a mis-click would be unrecoverable"
assert conn.execute("SELECT resolved_at FROM contradictions WHERE "
                    "contradiction_id=?", (cid,)).fetchone()["resolved_at"] is None
try:
    linter.undo_resolve(conn, cid)
    raise AssertionError("undo on an already-open contradiction should fail")
except SystemExit:
    pass
PYEOF
) || { echo "FAIL: dismiss/undo semantics"; rm -rf "$RV"; exit 1; }
rm -rf "$RV"
echo "dismiss never edits the page + undo restores ok"

# --- ingest mode is reported, and --subject really forces ---------------
# A project inside a versioned home dir looks git-managed; until doctor and
# status printed the live mode, nothing told you which was running. And the
# "nothing pending" message recommends `synthesize --subject` as the escape
# hatch, so the trivial-change skip must not short-circuit it.
MD=$(mktemp -d); mkdir -p "$MD/parent/proj"
( cd "$MD/parent" && git init -q . && printf 'x\n' > README.md && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm parent >/dev/null 2>&1 )
printf 'def a(): return 1\n' > "$MD/parent/proj/app.py"
( cd "$MD/parent/proj" && python3 -m irag init >/dev/null 2>&1 )
python3 - "$IRAG_SRC" "$MD/parent/proj" << 'PYEOF'
import sys, pathlib
cfg = pathlib.Path(sys.argv[2], ".irag/config.toml"); t = cfg.read_text()
cfg.write_text(t.replace(
    'command = "claude -p"       # prompt on stdin, markdown on stdout',
    f'command = "python3 {sys.argv[1]}/tests/mock_llm.py"'))
PYEOF
# an untracked project inside a foreign repo must run on fingerprints, and
# must SAY so in both places
( cd "$MD/parent/proj" && python3 -m irag status | grep -q "change detection" ) \
  || { echo "FAIL: status does not report the change-detection mode"; rm -rf "$MD"; exit 1; }
( cd "$MD/parent/proj" && python3 -m irag doctor 2>&1 | grep -q "ingest mode" ) \
  || { echo "FAIL: doctor does not report the ingest mode"; rm -rf "$MD"; exit 1; }
( cd "$MD/parent/proj" && python3 -m irag status | grep "change detection" \
    | grep -q "snapshot" ) \
  || { echo "FAIL: untracked subdir did not resolve to snapshot"; rm -rf "$MD"; exit 1; }
# and edits there must actually be detected (the reported blind spot)
( cd "$MD/parent/proj" && python3 -m irag update >/dev/null 2>&1 ) || true
sleep 1
printf 'def a(): return 999\ndef added(): pass\n' > "$MD/parent/proj/app.py"
( cd "$MD/parent/proj" && python3 -m irag update 2>&1 | grep -q "synthesized: app.py" ) \
  || { echo "FAIL: edits in an untracked subdir were not detected"; rm -rf "$MD"; exit 1; }
# --subject must force even when nothing semantic changed
( cd "$MD/parent/proj" && python3 - << 'PYEOF'
import pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db
conn = db.connect(pathlib.Path(".irag/memory.db"))
def n():
    return conn.execute(
        "SELECT COUNT(*) c FROM revisions r JOIN pages p ON p.page_id=r.page_id "
        "WHERE p.subject_id='app.py'").fetchone()["c"]
before = n()
subprocess.run([sys.executable, "-m", "irag", "synthesize", "--subject",
                "app.py"], capture_output=True)
conn2 = db.connect(pathlib.Path(".irag/memory.db"))
after = conn2.execute(
    "SELECT COUNT(*) c FROM revisions r JOIN pages p ON p.page_id=r.page_id "
    "WHERE p.subject_id='app.py'").fetchone()["c"]
assert after > before, (
    "synthesize --subject did not force a rewrite; the escape hatch the "
    "'nothing pending' message recommends does nothing")
PYEOF
) || { echo "FAIL: --subject does not force"; rm -rf "$MD"; exit 1; }
rm -rf "$MD"
echo "ingest mode reported + --subject forces ok"

# --- executable facts must not be a code-execution vector --------------
# The memory database is meant to be committed, and facts are shell
# commands stored in it. Running them from `check` by default meant
# `git clone && irag check` executed whatever a contributor registered.
FX=$(mktemp -d); mkdir -p "$FX/up"
printf 'def a(): return 1\n' > "$FX/up/a.py"
( cd "$FX/up" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 \
  && python3 -m irag verify "payload" \
       --cmd "sh -c 'echo PWNED > $FX/pwned.txt'" >/dev/null 2>&1 \
  && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm mem >/dev/null 2>&1 )
rm -f "$FX/pwned.txt"
( cd "$FX" && git clone -q up victim )
# Strip the key entirely so the built-in DEFAULT decides — this is the
# real-world case: a project initialised before facts existed has no such
# line, and an upgraded irag must still not execute on its behalf.
python3 - "$FX/victim" << 'PYEOF'
import sys, pathlib, re
p = pathlib.Path(sys.argv[1], ".irag/config.toml")
p.write_text(re.sub(r"^fail_on_facts.*\n(?:\s{2,}#.*\n)*", "",
                    p.read_text(), flags=re.M))
assert "fail_on_facts" not in p.read_text(), "key not removed"
PYEOF
( cd "$FX/victim" && python3 -m irag check >/dev/null 2>&1 ) || true
if [ -f "$FX/pwned.txt" ]; then
  echo "FAIL: cloning + 'irag check' executed a registered command"
  rm -rf "$FX"; exit 1
fi
# and with the key absent, opting in must be an explicit act
python3 - "$FX/victim" << 'PYEOF'
import sys, pathlib
p = pathlib.Path(sys.argv[1], ".irag/config.toml")
p.write_text(p.read_text().replace("[check]", "[check]\nfail_on_facts = true"))
PYEOF
# opt-in must still work, and must announce what it runs
( cd "$FX/victim" && python3 -m irag check 2>&1 | grep -q "registered command" ) \
  || { echo "FAIL: opt-in run did not announce the commands"; rm -rf "$FX"; exit 1; }
[ -f "$FX/pwned.txt" ] \
  || { echo "FAIL: opt-in did not actually run the fact"; rm -rf "$FX"; exit 1; }
rm -rf "$FX"
echo "facts are opt-in, not executed on clone, ok"

# --- fact judgement: both expectations, and error != disproved ---------
FJ=$(mktemp -d)
printf 'def a(): return 1\n' > "$FJ/a.py"
( cd "$FJ" && git init -q . && git add -A \
  && git -c user.email=t@t -c user.name=t commit -qm i \
  && python3 -m irag init >/dev/null 2>&1 )
# --expect satisfied but --expect-exit not: must FAIL, not silently pass
if ( cd "$FJ" && python3 -m irag verify "conflicting" --cmd "echo hi" \
       --expect hi --expect-exit 99 >/dev/null 2>&1 ); then
  echo "FAIL: --expect-exit was ignored when --expect was given"
  rm -rf "$FJ"; exit 1
fi
( cd "$FJ" && python3 -m irag verify "both ok" --cmd "echo hi" \
    --expect hi --expect-exit 0 >/dev/null 2>&1 ) \
  || { echo "FAIL: a fact satisfying both expectations did not pass"; \
       rm -rf "$FJ"; exit 1; }
# a command that cannot run here says nothing about the claim
( cd "$FJ" && python3 - << 'PYEOF'
import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from irag import db, facts
conn = db.connect(pathlib.Path(".irag/memory.db"))
fid = facts.record(conn, "needs a missing tool",
                   "definitely-not-installed-xyz --version", expect="X")
row = conn.execute("SELECT * FROM facts WHERE fact_id=?", (fid,)).fetchone()
status, _ = facts.run_one(conn, row, pathlib.Path.cwd())
assert status == "error", f"expected 'error', got {status!r}"
assert not any(r["fact_id"] == fid for r in facts.failing(conn)), \
    "a missing tool was counted as a disproved claim — CI would break on "\
    "any machine without it"
assert any(r["fact_id"] == fid for r in facts.unrunnable(conn)), \
    "an unrunnable fact was not reported as unrunnable"
PYEOF
) || { echo "FAIL: error/disproved conflation"; rm -rf "$FJ"; exit 1; }
rm -rf "$FJ"
echo "fact judgement (both expectations, error != disproved) ok"

# --- every write command can be attributed -----------------------------
# CLAUDE.md tells non-Claude agents to pass --id to every command that
# writes; five of them did not accept it, so their writes were unattributable.
for c in learn tried record-decision resolve capture update synthesize verify \
         session-begin session-end topic; do
  python3 -m irag "$c" --help 2>&1 | grep -q -- "--id" \
    || { echo "FAIL: '$c' writes but does not accept --id"; exit 1; }
done
echo "every write command accepts --id ok"

echo "SMOKE TEST PASSED"
