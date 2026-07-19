#!/usr/bin/env python3
"""Build the irag documentation site (stdlib only, zero dependencies).

Reads the repo's markdown docs, converts them with a purpose-sized
markdown renderer, and emits a static site into _site/ — a landing page
plus one pretty-URL page per doc, with sidebar navigation, per-page
table of contents, client-side search, and relative links throughout
(so the site works under any base path, e.g. GitHub Pages' /iRag/).

Usage:  python3 site/build.py            # writes ./_site
        python3 site/build.py --out DIR
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = Path(__file__).resolve().parent

GITHUB = "https://github.com/Karang1908/iRag"
BASE = "https://karang1908.github.io/iRag/"

# (slug, sidebar title, source path relative to repo root)
PAGES = [
    ("quickstart", "Quickstart", "irag/assets/docs/QUICKSTART.md"),
    ("setup", "Setup Guide", "docs/SETUP.md"),
    ("architecture", "Architecture", "docs/ARCHITECTURE.md"),
    ("cli-reference", "CLI Reference", "docs/CLI_REFERENCE.md"),
    ("comparison", "Comparison", "docs/COMPARISON.md"),
    ("story", "The Story", "docs/STORY.md"),
    ("development", "Development", "docs/DEVELOPMENT.md"),
    ("changelog", "Changelog", "CHANGELOG.md"),
]
# NOTE: docs/HANDOFF.md is deliberately NOT published — it is the
# internal handoff for AI agents developing irag itself, not user docs.
SECTIONS = [
    ("Using irag", ["quickstart", "setup", "cli-reference"]),
    ("Understanding it", ["architecture", "comparison", "story"]),
    ("Contributing", ["development", "changelog"]),
]
# markdown links to these sources get rewritten to site URLs
MD_LINK_MAP = {Path(src).name: slug for slug, _t, src in PAGES}


# ---------------------------------------------------------------------
# markdown → html (headings/anchors, fences, tables, lists, quotes,
# hr, bold/italic/code/links/images — the subset our docs actually use)
# ---------------------------------------------------------------------
def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;"))


# build-time syntax highlighting for the languages the docs actually use
# (GitHub-dark token colors; unknown languages stay plain)
TOKEN_DEFS = {
    "bash": [
        ("c", r"#[^\n]*"),
        ("s", r'"(?:\\.|[^"\\\n])*"|\'[^\'\n]*\''),
        ("v", r"\$\{[^}\n]+\}|\$\w+"),
        ("k", r"^ *(?:pip|irag|git|python3?|cd|sh|curl|gh|npm|node|export|"
              r"rm|cp|mkdir|grep|cat|uses|run)\b"),
    ],
    "toml": [
        ("c", r"#[^\n]*"),
        ("f", r"^\[[^\]\n]+\]"),
        ("s", r'"(?:\\.|[^"\\\n])*"'),
        ("k", r"^[A-Za-z0-9_.-]+(?= *=)"),
        ("n", r"\b\d+\b|\btrue\b|\bfalse\b"),
    ],
    "yaml": [
        ("c", r"#[^\n]*"),
        ("s", r'"(?:\\.|[^"\\\n])*"'),
        ("k", r"^ *-? *[\w.{}$ /-]+(?=:)"),
        ("n", r"\b\d+\b"),
    ],
    "sql": [
        ("c", r"--[^\n]*"),
        ("s", r"'[^'\n]*'"),
        ("k", r"\b(?:SELECT|FROM|WHERE|JOIN|ON|AND|OR|ORDER|BY|GROUP|"
              r"LIMIT|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|"
              r"NOT|NULL|AS|COUNT|IS|DESC|ASC)\b"),
        ("n", r"\b\d+\b"),
    ],
}


def highlight(code: str, lang: str) -> str:
    """Earliest-match-wins token scanner; everything HTML-escaped."""
    defs = TOKEN_DEFS.get(lang)
    if not defs:
        return esc(code)
    pats = [(cls, re.compile(p, re.M)) for cls, p in defs]
    out: list[str] = []
    i = 0
    while i < len(code):
        best, best_cls = None, None
        for cls, rx in pats:
            m = rx.search(code, i)
            if m and m.group(0) and (best is None or m.start() < best.start()):
                best, best_cls = m, cls
        if best is None:
            out.append(esc(code[i:]))
            break
        out.append(esc(code[i:best.start()]))
        out.append(f'<span class="tk-{best_cls}">{esc(best.group(0))}</span>')
        i = best.end()
    return "".join(out)


def slugify(text: str, used: set) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"
    base, n = s, 2
    while s in used:
        s = f"{base}-{n}"
        n += 1
    used.add(s)
    return s


def _inline(s: str, rel: str) -> str:
    # protect code spans first so nothing inside them is styled
    spans: list[str] = []

    def stash(m):
        spans.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(spans) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)",
               lambda m: f'<img src="{_href(m.group(2), rel)}" '
                         f'alt="{m.group(1)}" loading="lazy">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
               lambda m: f'<a href="{_href(m.group(2), rel)}"'
                         f'{_ext(m.group(2))}>{m.group(1)}</a>', s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\s][^*]*)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], s)
    return s


def _ext(url: str) -> str:
    return ' target="_blank" rel="noopener"' if url.startswith("http") else ""


def _href(url: str, rel: str) -> str:
    """Rewrite repo-relative markdown links to site-relative ones."""
    if url.startswith(("http://", "https://", "#", "mailto:")):
        return url
    name = Path(url.split("#")[0]).name
    frag = ("#" + url.split("#", 1)[1]) if "#" in url else ""
    if name in MD_LINK_MAP:
        return f"{rel}docs/{MD_LINK_MAP[name]}/{frag}"
    if "assets/" in url:  # doc images live in _site/assets/
        return f"{rel}assets/{Path(url).name}"
    return url


def md_to_html(src: str, rel: str) -> tuple[str, list, str]:
    """Returns (html, [(level, text, slug), ...] for the TOC, plain_text)."""
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    plain: list[str] = []
    used: set = set()
    lines = src.split("\n")
    i, n = 0, len(lines)
    in_ul = in_ol = in_quote = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def close_quote():
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    while i < n:
        ln = lines[i]

        # fenced code
        m = re.match(r"^```(\w*)\s*$", ln)
        if m:
            lang = m.group(1)
            body: list[str] = []
            i += 1
            while i < n and not lines[i].startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            close_lists(); close_quote()
            label = f'<span class="code-lang">{lang}</span>' if lang else ""
            out.append(
                f'<div class="code-block">{label}'
                f'<button class="code-copy" type="button" '
                f'aria-label="Copy code">copy</button>'
                f"<pre><code>{highlight(chr(10).join(body), lang)}"
                f"</code></pre></div>")
            continue

        # table
        if re.match(r"^\|.*\|\s*$", ln):
            rows = []
            while i < n and re.match(r"^\|.*\|\s*$", lines[i]):
                rows.append(lines[i].strip())
                i += 1
            close_lists(); close_quote()
            html_rows = []
            header_done = False
            for r in rows:
                if re.match(r"^\|[\s:|-]+\|$", r):
                    header_done = True
                    continue
                cells = [c.strip() for c in r[1:-1].split("|")]
                tag = "td" if header_done or html_rows else "th"
                html_rows.append(
                    "<tr>" + "".join(
                        f"<{tag}>{_inline(esc(c), rel)}</{tag}>"
                        for c in cells) + "</tr>")
                plain.append(" ".join(cells))
            out.append('<div class="table-wrap"><table>'
                       + "".join(html_rows) + "</table></div>")
            continue

        # heading
        m = re.match(r"^(#{1,4})\s+(.*)$", ln)
        if m:
            close_lists(); close_quote()
            level = len(m.group(1))
            text = m.group(2).strip()
            anchor = slugify(re.sub(r"[`*]", "", text), used)
            if level >= 2:
                toc.append((level, re.sub(r"[`*]", "", text), anchor))
            out.append(
                f'<h{level} id="{anchor}">{_inline(esc(text), rel)}'
                f'<a class="hlink" href="#{anchor}" '
                f'aria-label="Link to section">#</a></h{level}>')
            plain.append(text)
            i += 1
            continue

        # hr
        if re.match(r"^---+\s*$", ln):
            close_lists(); close_quote()
            out.append("<hr>")
            i += 1
            continue

        # blockquote (consecutive '>' lines merge into one paragraph so
        # inline formatting can span soft-wrapped lines)
        if ln.startswith(">"):
            close_lists()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            quote = [ln.lstrip("> ")]
            j = i + 1
            while j < n and lines[j].startswith(">") and lines[j].lstrip("> "):
                quote.append(lines[j].lstrip("> "))
                j += 1
            text = " ".join(quote)
            out.append(f"<p>{_inline(esc(text), rel)}</p>")
            plain.append(text)
            i = j
            continue
        close_quote()

        # list items (with hanging-indent continuation lines)
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+(.*)$", ln)
        if m and len(m.group(1)) < 4:
            ordered = m.group(2)[0].isdigit()
            item = m.group(3)
            j = i + 1
            while j < n and re.match(r"^\s{2,}\S", lines[j]) and \
                    not re.match(r"^\s*([-*]|\d+\.)\s+", lines[j]):
                item += " " + lines[j].strip()
                j += 1
            if ordered and not in_ol:
                close_lists()
                out.append("<ol>")
                in_ol = True
            elif not ordered and not in_ul:
                close_lists()
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(esc(item), rel)}</li>")
            plain.append(item)
            i = j
            continue

        # blank line
        if not ln.strip():
            close_lists()
            i += 1
            continue

        # paragraph (merge soft-wrapped lines)
        para = [ln.strip()]
        j = i + 1
        while j < n and lines[j].strip() and not re.match(
                r"^(#{1,4}\s|```|\||>|---+\s*$|\s*([-*]|\d+\.)\s)", lines[j]):
            para.append(lines[j].strip())
            j += 1
        close_lists()
        text = " ".join(para)
        out.append(f"<p>{_inline(esc(text), rel)}</p>")
        plain.append(text)
        i = j

    close_lists(); close_quote()
    return "\n".join(out), toc, " ".join(plain)


# ---------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------
LOGO = """<svg class="logo" viewBox="0 0 26 26" aria-hidden="true">
<line x1="13" y1="13" x2="6" y2="6"/><line x1="13" y1="13" x2="21" y2="8"/>
<line x1="13" y1="13" x2="13" y2="21"/>
<circle cx="6" cy="6" r="2.4" fill="#58a6ff" stroke="none"/>
<circle cx="21" cy="8" r="2.4" fill="#c8a2ff" stroke="none"/>
<circle cx="13" cy="21" r="2.4" fill="#3fb950" stroke="none"/>
<circle cx="13" cy="13" r="3.4" fill="#e6edf3" stroke="none"/></svg>"""

FAVICON = ("data:image/svg+xml," +
           "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 26 26'%3E"
           "%3Crect width='26' height='26' rx='6' fill='%230b0f14'/%3E"
           "%3Cline x1='13' y1='13' x2='6' y2='6' stroke='%23444c56'/%3E"
           "%3Cline x1='13' y1='13' x2='21' y2='8' stroke='%23444c56'/%3E"
           "%3Cline x1='13' y1='13' x2='13' y2='21' stroke='%23444c56'/%3E"
           "%3Ccircle cx='6' cy='6' r='2.6' fill='%2358a6ff'/%3E"
           "%3Ccircle cx='21' cy='8' r='2.6' fill='%23c8a2ff'/%3E"
           "%3Ccircle cx='13' cy='21' r='2.6' fill='%233fb950'/%3E"
           "%3Ccircle cx='13' cy='13' r='3.6' fill='%23e6edf3'/%3E%3C/svg%3E")


def sidebar(rel: str, active: str) -> str:
    head = [f'<a class="side-brand" href="{rel}index.html">{LOGO}'
            f"<b>irag</b><span class=\"side-docs\">docs</span></a>",
            '<button class="menu-btn" type="button" aria-expanded="false" '
            'aria-controls="side-links">Menu</button>',
            '<div class="search-box"><input id="search" type="search" '
            'placeholder="Search docs…" autocomplete="off" '
            'aria-label="Search documentation"><kbd class="skbd">/</kbd>'
            '<div id="search-results" hidden></div></div>']
    links: list[str] = []
    for section, slugs in SECTIONS:
        links.append(f'<div class="side-sec">{section}</div>')
        for slug in slugs:
            title = next(t for s, t, _p in PAGES if s == slug)
            cur = ' aria-current="page"' if slug == active else ""
            links.append(f'<a class="side-link" href="{rel}docs/{slug}/"'
                         f"{cur}>{title}</a>")
    links.append(f'<div class="side-sec">Project</div>'
                 f'<a class="side-link" href="{GITHUB}" target="_blank" '
                 f'rel="noopener">GitHub ↗</a>'
                 f'<a class="side-link" href="{GITHUB}/releases" '
                 f'target="_blank" rel="noopener">Releases ↗</a>')
    return ("<nav class=\"side\" aria-label=\"Documentation\">"
            + "".join(head)
            + f'<div class="side-links" id="side-links">{"".join(links)}'
              "</div></nav>")


FONTS = ('<link rel="preconnect" href="https://api.fontshare.com">\n'
         '<link rel="preconnect" href="https://cdn.fontshare.com" '
         'crossorigin>\n'
         '<link rel="stylesheet" href="https://api.fontshare.com/v2/css'
         '?f[]=satoshi@400,500,700&f[]=clash-display@500,600,700'
         '&display=swap">')


def page_shell(title: str, desc: str, body: str, rel: str,
               extra_class: str = "", path: str = "",
               anime: bool = False) -> str:
    anime_tag = (f'<script src="{rel}anime.min.js" defer></script>\n'
                 if anime else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#08090c">
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{BASE}{path}">
<meta property="og:type" content="website">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{BASE}{path}">
<meta property="og:image" content="{BASE}assets/dashboard-overview.jpg">
<meta name="twitter:card" content="summary_large_image">
<title>{esc(title)}</title>
<link rel="icon" href="{FAVICON}">
{FONTS}
<link rel="stylesheet" href="{rel}style.css">
</head>
<body class="{extra_class}">
<a class="skip" href="#main">Skip to content</a>
{body}
{anime_tag}<script src="{rel}app.js" defer></script>
</body>
</html>"""


def doc_page(slug: str, title: str, html: str,
             toc: list, prev_next: tuple, src: str) -> str:
    rel = "../../"
    toc_html = ""
    h2s = [(t, a) for lvl, t, a in toc if lvl == 2]
    if len(h2s) >= 2:
        toc_html = ('<aside class="toc" aria-label="On this page">'
                    "<div class=\"toc-t\">On this page</div>"
                    + "".join(f'<a href="#{a}">{esc(t)}</a>'
                              for t, a in h2s) + "</aside>")
    prev_html, next_html = "", ""
    if prev_next[0]:
        ps, pt = prev_next[0]
        prev_html = (f'<a class="pager prev" href="../{ps}/">'
                     f"<span>Previous</span><b>{pt}</b></a>")
    if prev_next[1]:
        ns, nt = prev_next[1]
        next_html = (f'<a class="pager next" href="../{ns}/">'
                     f"<span>Next</span><b>{nt}</b></a>")
    body = f"""<div class="wrap">
{sidebar(rel, slug)}
<main class="doc" id="main">
<article>{html}</article>
<div class="pagers">{prev_html}{next_html}</div>
<footer class="foot">MIT licensed · built from
<a href="{GITHUB}" target="_blank" rel="noopener">Karang1908/iRag</a>
· <a href="{GITHUB}/blob/main/{src}" target="_blank"
rel="noopener">Edit this page on GitHub</a></footer>
</main>
{toc_html}
</div>"""
    return page_shell(f"{title} — irag docs",
                      f"irag documentation — {title}", body, rel,
                      path=f"docs/{slug}/")


# ---------------------------------------------------------------------
# landing page
# ---------------------------------------------------------------------
GRAPH_NODES = [
    # (cx, cy, r, label, lx, ly)
    (296, 64, 3.5, "api.py", 306, 60),
    (138, 84, 3, "auth.py", 88, 76),
    (218, 178, 5, "db.py", 234, 172),
    (352, 150, 3, "models.py", 362, 146),
    (92, 190, 3, "worker.py", 28, 180),
    (172, 272, 3, "cache.py", 116, 288),
    (334, 262, 3, "cli.py", 344, 258),
    (64, 306, 2.5, "tests/", 40, 326),
    (252, 332, 2.5, "config.toml", 262, 340),
]
GRAPH_EDGES = [(0, 1), (0, 2), (0, 3), (1, 2), (2, 3), (2, 4), (4, 5),
               (2, 5), (0, 6), (2, 6), (0, 7), (6, 8), (4, 8)]


def hero_graph() -> str:
    n = GRAPH_NODES
    edges = "".join(
        f'<path class="gp" d="M{n[a][0]} {n[a][1]} L{n[b][0]} {n[b][1]}"/>'
        for a, b in GRAPH_EDGES)
    nodes = "".join(
        f'<circle class="gn" cx="{cx}" cy="{cy}" r="{r}" '
        f'fill="{"#58a6ff" if r >= 5 else "#dfe3e8"}"/>'
        for cx, cy, r, _l, _x, _y in n)
    labels = "".join(
        f'<text x="{lx}" y="{ly}">{label}</text>'
        for _cx, _cy, _r, label, lx, ly in n)
    return (
        '<svg class="ggraph" viewBox="0 0 440 380" fill="none" '
        'role="presentation">'
        f'<g stroke="rgba(242,243,245,.15)" stroke-width="1">{edges}</g>'
        '<circle class="gn" cx="218" cy="178" r="11" fill="none" '
        'stroke="#58a6ff" stroke-opacity=".35"/>'
        f"<g>{nodes}</g>"
        '<g class="glabels" font-family="ui-monospace,Menlo,monospace" '
        f'font-size="10.5" fill="#707885">{labels}</g></svg>')


def landing() -> str:
    rel = ""
    body = f"""<header class="hero-nav">
<a class="side-brand" href="index.html">{LOGO}<b>irag</b></a>
<nav aria-label="Site">
<a class="nav-sec" href="#why">Why</a>
<a class="nav-sec" href="#how">How</a>
<a href="docs/quickstart/">Docs</a>
<a href="docs/changelog/">Changelog</a>
<a href="{GITHUB}" target="_blank" rel="noopener">GitHub ↗</a>
</nav>
</header>
<main class="landing" id="main">
<div class="spine" aria-hidden="true"><svg preserveAspectRatio="none">
<line class="sp-track"/><line class="sp-fill"/></svg></div>
<section class="hero">
<div class="hero-copy">
<p class="eyebrow hero-eyebrow">Pluggable memory for AI coding agents</p>
<h1 class="hero-name" aria-label="irag"><span>i</span><span>r</span><span>a</span><span>g</span><span class="hn-dot">.</span></h1>
<p class="lede">Plug a memory into any coding agent — Claude Code, Cursor,
Codex — and it stops re-learning your codebase every session.
<b>irag remembers what every file does, what changed and why, and what
each session decided</b>: one SQLite file inside your repo, fact-checked
against the real code, so it can never quietly lie.</p>
<div class="cta">
<a class="btn primary" href="docs/quickstart/">Get started</a>
<a class="btn ghost" href="{GITHUB}" target="_blank" rel="noopener">GitHub ↗</a>
</div>
<div class="install"><code>pip install -e ./irag && irag init</code><button class="code-copy" type="button" aria-label="Copy install command">copy</button></div>
</div>
<figure class="hero-graph" aria-hidden="true">
{hero_graph()}
<figcaption class="mono-cap">your codebase, as irag maps it — parsed, never guessed</figcaption>
</figure>
</section>
<section class="stats" aria-label="irag in numbers">
<div class="stat"><b data-cnt="3" data-suf="k">3k</b><span>tokens to
brief a fresh session — instead of 20–100k of re-exploration</span></div>
<div class="stat"><b data-cnt="150" data-pre="~">~150</b><span>tokens to
resume any past conversation, with its per-file changes</span></div>
<div class="stat"><b data-cnt="1" data-suf=" file">1 file</b><span>the
entire memory — SQLite, inside your repo, mounts anywhere</span></div>
<div class="stat"><b data-cnt="0">0</b><span>runtime dependencies,
services, or API keys</span></div>
</section>
<section class="sec" id="why">
<p class="eyebrow">01 — Why it exists</p>
<h2 class="sec-t">Context files rot. Databases don't.</h2>
<div class="why-cols">
<p>Every coding agent ships the same fix for amnesia: a markdown file it
re-reads at startup. Claims go stale, nobody notices, and the agent keeps
trusting them. irag treats memory as a <b>storage problem, not a prompt
problem</b> — version every claim, fact-check it against the code
mechanically, and let the expensive model read instead of re-explore.</p>
<p>It began as a database-systems assignment: a law-firm knowledge base,
where a wrong court date is a real failure, not a bad chatbot answer.
That discipline stuck when it was pointed at code —
<a href="docs/story/">the full story</a>.</p>
</div>
<div class="table-wrap cmp reveal">
<table>
<tr><th></th><th>Graphify</th><th>claude-mem</th><th class="hl">irag</th></tr>
<tr><td>Remembers</td><td>structure</td><td>conversations</td>
<td class="hl">structure + meaning + history</td></tr>
<tr><td>Can it be wrong?</td><td>rarely — it's a parse</td>
<td>yes — silently, forever</td>
<td class="hl">yes — and it detects, records, and gates CI on it</td></tr>
<tr><td>Cost</td><td>free</td><td>scales with chat volume</td>
<td class="hl">reads free · writes on any cheap model</td></tr>
</table>
</div>
<p class="fineprint"><b>irag's trade-offs, honestly:</b> writes cost LLM
calls (pluggable — point them at a free-quota model), it needs Python
3.11+, and the linter verifies checkable claims — paths, symbols,
versions — not opinions. <a href="docs/comparison/">Full comparison →</a></p>
</section>
<section class="sec" id="how">
<p class="eyebrow">02 — How it runs</p>
<h2 class="sec-t">Three actors. One loop.</h2>
<div class="pipe">
<em class="pd1" aria-hidden="true"></em><em class="pd2" aria-hidden="true"></em>
<div class="pipe-col reveal">
<p class="mono-cap">reads · zero tokens</p>
<h3>Your agent</h3>
<p>Claude Code, Cursor, Codex — starts every session already briefed,
through SQL. It never greps to remember.</p>
</div>
<div class="pipe-col mid reveal">
<p class="mono-cap">the memory</p>
<h3>irag</h3>
<p>One SQLite file in your repo. Versions every page, logs every session,
fact-checks every claim. Never calls a model itself.</p>
</div>
<div class="pipe-col reveal">
<p class="mono-cap">writes · cheap quota</p>
<h3>The scribe</h3>
<p>Any LLM CLI you configure writes the summaries — on a free-quota
model, so bookkeeping never touches your agent's limits.</p>
</div>
</div>
<div class="term reveal tilt" role="img" aria-label="Terminal demo: irag init creates the memory, irag update fact-checks it, and a new agent session starts fully briefed">
<div class="term-bar" aria-hidden="true"><span class="tb"></span><span class="tb"></span><span class="tb"></span><span class="term-title">~/your-project</span></div>
<pre class="term-body" id="term-body" aria-hidden="true"></pre>
</div>
</section>
<section class="sec">
<p class="eyebrow">03 — What you get</p>
<h2 class="sec-t">Built like a database, because it is one.</h2>
<ol class="featlist">
<li class="reveal"><span>01</span><h3>Verified, not just remembered</h3>
<p>A deterministic linter checks every claim against the code. Wrong
memory becomes a visible contradiction — and <code>irag check</code>
fails CI until it's resolved.</p></li>
<li class="reveal"><span>02</span><h3>Reads cost nothing</h3>
<p>Search, code map, blast radius, recaps — pure SQL. A session starts
briefed in ~3k tokens instead of re-reading the tree.</p></li>
<li class="reveal"><span>03</span><h3>Writes go on a cheap model</h3>
<p>The scribe is one pluggable CLI command. Point it at a free quota;
your agent's rate limits stay untouched.</p></li>
<li class="reveal"><span>04</span><h3>A diary with receipts</h3>
<p>Every session is logged with the exact per-file changes it made.
Any new chat resumes in ~150 tokens.</p></li>
<li class="reveal"><span>05</span><h3>History you can query</h3>
<p><code>why</code> traces a claim to the commit that caused it.
<code>asof</code> time-travels. Rollback never rewrites.</p></li>
<li class="reveal"><span>06</span><h3>One file, zero dependencies</h3>
<p>The whole memory is <code>.irag/memory.db</code>. Pure-stdlib Python,
works with or without git, any agent can mount it.</p></li>
</ol>
</section>
<section class="sec">
<p class="eyebrow">04 — The dashboard</p>
<h2 class="sec-t">See what it knows.</h2>
<div class="shots">
<figure class="tilt reveal"><img src="assets/dashboard-overview.jpg" alt="irag dashboard overview" loading="lazy">
<figcaption>live dashboard — token burn, health, activity</figcaption></figure>
<figure class="tilt reveal"><img src="assets/dependency-graph.jpg" alt="Interactive dependency graph" loading="lazy">
<figcaption>dependency graph — pan, zoom, trace imports</figcaption></figure>
</div>
</section>
<section class="quote reveal">
<blockquote>"A <b>database system with an AI layer</b> — not an AI system
with a database attached."</blockquote>
<span class="mono-cap">the design rule every table follows ·
<a href="docs/story/">read the story</a></span>
</section>
<section class="closing reveal">
<h2 class="sec-t">Give your agent a memory.</h2>
<div class="cta">
<a class="btn primary" href="docs/quickstart/">Get started</a>
<a class="btn ghost" href="docs/architecture/">Read the architecture</a>
</div>
</section>
<footer class="foot land">
<span>MIT · <a href="{GITHUB}" target="_blank"
rel="noopener">Karang1908/iRag</a></span>
<span>v4.2.0 · zero dependencies</span>
</footer>
</main>"""
    return page_shell(
        "irag — pluggable memory for AI coding agents",
        "Pluggable, fact-checked memory for AI coding agents — one "
        "SQLite file in your repo. Reads are free SQL; writes run on "
        "any cheap model.",
        body, rel, extra_class="is-landing", anime=True)


# ---------------------------------------------------------------------
# build
# ---------------------------------------------------------------------
def build(out: Path) -> int:
    if out.exists():
        shutil.rmtree(out)
    (out / "docs").mkdir(parents=True)
    (out / "assets").mkdir()

    for asset in (ROOT / "docs" / "assets").glob("*"):
        shutil.copy(asset, out / "assets" / asset.name)
    for static in ("style.css", "app.js"):
        shutil.copy(SITE / static, out / static)
    for vend in (SITE / "vendor").glob("*"):
        shutil.copy(vend, out / vend.name)

    search_index = []
    titles = {s: t for s, t, _p in PAGES}
    order = [s for s, _t, _p in PAGES]
    for idx, (slug, title, src) in enumerate(PAGES):
        source = (ROOT / src).read_text(encoding="utf-8")
        html, toc, plain = md_to_html(source, "../../")
        prev_slug = order[idx - 1] if idx > 0 else None
        next_slug = order[idx + 1] if idx + 1 < len(order) else None
        page = doc_page(
            slug, title, html, toc,
            ((prev_slug, titles[prev_slug]) if prev_slug else None,
             (next_slug, titles[next_slug]) if next_slug else None),
            src)
        d = out / "docs" / slug
        d.mkdir()
        (d / "index.html").write_text(page, encoding="utf-8")
        # search index: one entry per h2 section
        words = plain.split()
        search_index.append({
            "t": title, "u": f"docs/{slug}/", "h": "",
            "x": " ".join(words[:60])})
        for lvl, text, anchor in toc:
            if lvl == 2:
                pos = plain.find(text)
                snippet = plain[pos:pos + 240] if pos >= 0 else ""
                search_index.append({
                    "t": title, "u": f"docs/{slug}/#{anchor}",
                    "h": text, "x": snippet})

    (out / "index.html").write_text(landing(), encoding="utf-8")
    (out / "search-index.json").write_text(
        json.dumps(search_index), encoding="utf-8")
    (out / ".nojekyll").write_text("")

    # 404 (served by GitHub Pages at any depth -> absolute links)
    body_404 = f"""<main class="landing" id="main">
<section class="hero"><div class="hero-copy">
<p class="eyebrow">Error 404</p>
<h1 class="sec-t">This page doesn't exist.</h1>
<p class="lede">It may have moved when the docs were regenerated.</p>
<div class="cta">
<a class="btn primary" href="{BASE}">Home</a>
<a class="btn ghost" href="{BASE}docs/quickstart/">Docs</a>
</div></div></section></main>"""
    (out / "404.html").write_text(
        page_shell("Page not found — irag", "Page not found",
                   body_404, BASE, extra_class="is-landing",
                   path="404.html"),
        encoding="utf-8")

    # sitemap + robots
    urls = [BASE] + [f"{BASE}docs/{slug}/" for slug, _t, _s in PAGES]
    sitemap = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               + "".join(f"<url><loc>{u}</loc></url>" for u in urls)
               + "</urlset>")
    (out / "sitemap.xml").write_text(sitemap, encoding="utf-8")
    (out / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {BASE}sitemap.xml\n",
        encoding="utf-8")
    n = len(list(out.rglob("*.html")))
    print(f"built {n} page(s) -> {out}")
    return n


if __name__ == "__main__":
    out = Path(sys.argv[sys.argv.index("--out") + 1]) \
        if "--out" in sys.argv else ROOT / "_site"
    build(out)
