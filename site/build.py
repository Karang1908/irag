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
    ("development", "Development", "docs/DEVELOPMENT.md"),
    ("handoff", "AI Handoff", "docs/HANDOFF.md"),
    ("changelog", "Changelog", "CHANGELOG.md"),
]
SECTIONS = [
    ("Using irag", ["quickstart", "setup", "cli-reference"]),
    ("Understanding it", ["architecture", "comparison"]),
    ("Contributing", ["development", "handoff", "changelog"]),
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

        # blockquote
        if ln.startswith(">"):
            close_lists()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{_inline(esc(ln.lstrip('> ')), rel)}</p>")
            plain.append(ln.lstrip("> "))
            i += 1
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


def page_shell(title: str, desc: str, body: str, rel: str,
               extra_class: str = "", path: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#0b0f14">
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
<link rel="stylesheet" href="{rel}style.css">
</head>
<body class="{extra_class}">
<a class="skip" href="#main">Skip to content</a>
{body}
<script src="{rel}app.js" defer></script>
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
def landing() -> str:
    rel = ""
    features = [
        ("Verified, not just remembered",
         "A deterministic linter checks every LLM claim against the "
         "filesystem, manifests, and symbol table. Wrong claims become "
         "queryable contradiction rows — and <code>irag check</code> "
         "fails CI while memory and code disagree."),
        ("Zero-token reads",
         "Search, code map, blast radius, provenance, and session recaps "
         "are pure SQL. A fresh agent session starts with a ~3k-token "
         "briefing instead of 20–100k tokens of re-exploration."),
        ("Bring a cheap scribe",
         "The write path is one pluggable CLI command. Point it at a "
         "free-quota model and bookkeeping stops competing with your "
         "coding agent's rate limits entirely."),
        ("A diary with receipts",
         "Every agent conversation is logged with the exact per-file "
         "changes it made. A brand-new chat resumes for ~150 tokens "
         "with <code>irag recap</code>."),
        ("History as a first-class object",
         "Append-only page versions. <code>why</code> traces any claim "
         "to the commit that caused it; <code>asof</code> time-travels; "
         "rollback never rewrites history."),
        ("One SQLite file, zero deps",
         "The entire memory is <code>.irag/memory.db</code> — query it, "
         "back it up, mount it anywhere. Pure-stdlib Python, no runtime "
         "dependencies, works with or without git."),
    ]
    cards = "".join(
        f'<div class="feat"><h3>{t}</h3><p>{d}</p></div>'
        for t, d in features)
    body = f"""<header class="hero-nav">
<a class="side-brand" href="index.html">{LOGO}<b>irag</b></a>
<nav aria-label="Site">
<a href="docs/quickstart/">Docs</a>
<a href="docs/changelog/">Changelog</a>
<a href="{GITHUB}" target="_blank" rel="noopener">GitHub ↗</a>
</nav>
</header>
<main class="landing" id="main">
<section class="hero">
<h1>Verified memory for<br>AI coding agents</h1>
<p class="tag">irag replaces the flat context file with a relational
knowledge base that lives in your repo — LLM-written, versioned,
<b>mechanically fact-checked against the code</b>, and served back to
any agent for near-zero tokens.</p>
<div class="cta">
<a class="btn primary" href="docs/quickstart/">Get started</a>
<a class="btn" href="{GITHUB}" target="_blank" rel="noopener">Star on GitHub</a>
</div>
<div class="install"><code>pip install -e ./irag &nbsp;&&&nbsp; irag init</code><button class="code-copy" type="button" aria-label="Copy install command">copy</button></div>
<ul class="sell" aria-label="Why irag">
<li><b>Pluggable memory</b> — one SQLite file in your repo; no service, no cloud, no keys</li>
<li><b>Your agent never greps again</b> — map, search &amp; context are SQL, not model calls</li>
<li><b>Memory that can't quietly lie</b> — fact-checked against the code, gated in CI</li>
<li><b>Reads cost ~nothing</b> — a 3k-token briefing instead of 100k of re-exploration</li>
<li><b>Every chat resumes instantly</b> — a ~150-token recap of what past sessions did</li>
<li><b>Any agent</b> — Claude Code hooks, <code>AGENTS.md</code> for Codex / Antigravity / Cursor</li>
</ul>
</section>
<section class="chain" aria-label="How the pieces relate">
<div class="chain-box"><b>Claude Code</b><span>the operator</span><i>reads memory for free</i></div>
<div class="chain-arrow">→</div>
<div class="chain-box"><b>irag</b><span>the memory</span><i>SQL, never thinks</i></div>
<div class="chain-arrow">→</div>
<div class="chain-box"><b>any LLM CLI</b><span>the scribe</span><i>writes on a cheap quota</i></div>
</section>
<section class="feats">{cards}</section>
<section class="shots">
<figure><img src="assets/dashboard-overview.jpg" alt="irag dashboard overview" loading="lazy">
<figcaption>Live dashboard: token burn, health, activity</figcaption></figure>
<figure><img src="assets/dependency-graph.jpg" alt="Interactive dependency graph" loading="lazy">
<figcaption>Obsidian-style dependency graph — pan, zoom, trace imports</figcaption></figure>
</section>
<section class="closing">
<p>The database is the only source of truth. <code>CLAUDE.md</code> and
<code>AGENTS.md</code> are generated projections. The model never does
bookkeeping.</p>
<a class="btn primary" href="docs/quickstart/">Read the docs</a>
</section>
<footer class="foot center">MIT licensed ·
<a href="{GITHUB}" target="_blank" rel="noopener">Karang1908/iRag</a></footer>
</main>"""
    return page_shell(
        "irag — verified memory for AI coding agents",
        "A local, zero-dependency knowledge base that gives AI coding "
        "agents persistent, fact-checked memory of your codebase.",
        body, rel, extra_class="is-landing")


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
    body_404 = f"""<main class="landing" id="main"><section class="hero">
<h1>404</h1>
<p class="tag">That page doesn't exist (or moved when the docs were
regenerated).</p>
<div class="cta">
<a class="btn primary" href="{BASE}">Home</a>
<a class="btn" href="{BASE}docs/quickstart/">Docs</a>
</div></section></main>"""
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
