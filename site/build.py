#!/usr/bin/env python3
"""Build the irag documentation site (stdlib only, zero dependencies).

Reads the repo's markdown docs, converts them with a purpose-sized
markdown renderer, and emits a static site into _site/ — a landing page
plus one pretty-URL page per doc, with sidebar navigation, per-page
table of contents, client-side search, and relative links throughout
(so the site works under any base path, e.g. GitHub Pages' /irag/).

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

GITHUB = "https://github.com/Karang1908/irag"
BUILD_MARKER = ".irag-site-build"


def _version() -> str:
    """The package's own version — never hardcode it here; the footer went
    six releases stale that way."""
    import re as _re
    m = _re.search(r'__version__\s*=\s*"([^"]+)"',
                   (ROOT / "irag" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1) if m else "dev"


VERSION = _version()
BASE = "https://karang1908.github.io/irag/"

# (slug, sidebar title, source path relative to repo root)
PAGES = [
    ("quickstart", "Quickstart", "irag/assets/docs/QUICKSTART.md"),
    ("mcp", "Connect with MCP", "docs/MCP.md"),
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
    ("Using irag", ["quickstart", "mcp", "setup", "cli-reference"]),
    ("Understanding it", ["architecture", "comparison", "story"]),
    ("Contributing", ["development", "changelog"]),
]
# How many releases the changelog page shows before folding the rest away.
# Every entry stays on the page - the older ones are one click behind a
# disclosure rather than absent, so a long history costs a scroll, not
# information. CHANGELOG.md itself is never touched.
CHANGELOG_KEEP = 8


def _collapse_changelog(html: str, toc: list, keep: int = CHANGELOG_KEEP):
    """Fold every release past the newest `keep` into a <details> block.

    Applied to the RENDERED html: the markdown renderer escapes raw HTML, so
    the disclosure cannot be injected into the source text. Returns the
    rewritten html and a table of contents covering only what is visible -
    an anchor pointing inside a collapsed <details> does not reliably scroll
    to its target.
    """
    starts = [m.start() for m in re.finditer(r'<h2 id="', html)]
    if len(starts) <= keep:
        return html, toc
    cut = starts[keep]
    older = len(starts) - keep
    folded = (
        html[:cut]
        + '<details class="older-releases">'
        + f"<summary>Earlier releases ({older})</summary>"
        + html[cut:]
        + "</details>"
    )
    visible, seen = [], 0
    for entry in toc:
        if entry[0] == 2:
            seen += 1
            if seen > keep:
                break
        visible.append(entry)
    return folded, visible


# markdown links to these sources get rewritten to site URLs
MD_LINK_MAP = {Path(src).name: slug for slug, _t, src in PAGES}


# ---------------------------------------------------------------------
# markdown → html (headings/anchors, fences, tables, lists, quotes,
# hr, bold/italic/code/links/images — the subset our docs actually use)
# ---------------------------------------------------------------------
def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;")
             .replace("'", "&#39;"))


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
    # neutralise any other explicit scheme (javascript:, data:, vbscript:)
    # so a stray/copied link can't become an active URL in the built page
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", url):
        return "#"
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
            close_lists()
            close_quote()
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
            close_lists()
            close_quote()
            html_rows: list[str] = []
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
            close_lists()
            close_quote()
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
            close_lists()
            close_quote()
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

    close_lists()
    close_quote()
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


# One stylesheet request per family, deliberately. Fontshare's v2 CSS API
# honours only the FIRST f[] parameter and silently drops the rest, so the
# combined "?f[]=satoshi…&f[]=clash-display…" request returned Satoshi alone
# and every Clash Display heading fell back to the body font with no error.
FONTS = ('<link rel="preconnect" href="https://api.fontshare.com">\n'
         '<link rel="preconnect" href="https://cdn.fontshare.com" '
         'crossorigin>\n'
         '<link rel="stylesheet" href="https://api.fontshare.com/v2/css'
         '?f[]=satoshi@400,500,700&display=swap">\n'
         '<link rel="stylesheet" href="https://api.fontshare.com/v2/css'
         '?f[]=clash-display@500,600,700&display=swap">')


def page_shell(title: str, desc: str, body: str, rel: str,
               extra_class: str = "", path: str = "",
               anime: bool = False) -> str:
    anime_tag = (f'<script src="{rel}anime.min.js" defer></script>\n'
                 if anime else "")
    # Lenis momentum scrolling site-wide (vendored, MIT) — app.js inits it
    lenis_tag = f'<script src="{rel}lenis.min.js" defer></script>\n'
    anime_tag = lenis_tag + anime_tag
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
    body = f"""<canvas class="scene ambient" id="scene" aria-hidden="true"></canvas>
<div class="wrap">
{sidebar(rel, slug)}
<main class="doc doc-shell" id="main">
<article>{html}</article>
<div class="pagers">{prev_html}{next_html}</div>
<footer class="foot">MIT licensed · built from
<a href="{GITHUB}" target="_blank" rel="noopener">Karang1908/irag</a>
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
<a class="nav-sec" href="#connect">Connect</a>
<a class="nav-sec" href="#intelligence">Intelligence</a>
<a class="nav-sec" href="#how">How</a>
<a href="docs/quickstart/">Docs</a>
<a href="docs/changelog/">Changelog</a>
<a href="{GITHUB}" target="_blank" rel="noopener">GitHub ↗</a>
</nav>
</header>
<canvas class="scene" id="scene" aria-hidden="true"></canvas>
<main class="landing" id="main">
<nav class="rail" id="rail" aria-label="Sections"></nav>
<section class="hero">
<div class="hero-copy">
<h1 class="hero-name" aria-label="irag"><span>i</span><span>r</span><span>a</span><span>g</span><span class="hn-dot">.</span></h1>
<p class="lede"><b>Give every coding agent the same second brain — and give
yourself a command center for the entire project.</b> irag remembers the whole
codebase, proves its checkable claims against the real source, proves the live
full-stack application instead of trusting silent wiring, audits the code,
and turns live market evidence into grounded product thinking. Codex today,
Claude tomorrow, Cursor after lunch — the project never forgets.</p>
<div class="cta">
<a class="btn primary" href="docs/quickstart/">Get started</a>
<a class="btn ghost" href="{GITHUB}" target="_blank" rel="noopener">GitHub ↗</a>
</div>
<div class="install"><code>pip install git+https://github.com/Karang1908/irag.git</code><button class="code-copy" type="button" aria-label="Copy install command">copy</button></div>
</div>
<figure class="hero-graph" aria-hidden="true">
{hero_graph()}
<figcaption class="mono-cap">your codebase, as irag maps it: parsed, never guessed</figcaption>
</figure>
</section>
<section class="stats" aria-label="irag in numbers">
<div class="stat"><b data-cnt="0">0</b><span>tokens to search, map,
or trace a claim: those paths never call a model</span></div>
<div class="stat"><b data-cnt="87" data-pre="~">~87</b><span>tokens to
resume any past conversation, with its per-file changes</span></div>
<div class="stat"><b data-cnt="22" data-suf=" tools">22 tools</b><span>one
standard MCP contract, identical in every connected coding agent</span></div>
<div class="stat"><b data-cnt="0">0</b><span>runtime dependencies,
services, or required API keys</span></div>
</section>
<section class="sec" id="why">
<h2 class="sec-t">Your agent forgets. Your project does not have to.</h2>
<div class="why-cols">
<p>Every coding agent ships the same fix for amnesia: a markdown file it
re-reads at startup. Claims go stale, nobody notices, and the agent keeps
trusting them. irag treats memory as a <b>storage problem, not a prompt
problem</b>. Version every claim, fact-check it against the code
mechanically, and let the expensive model read instead of re-explore.</p>
<p>It began as a database-systems assignment: a law-firm knowledge base,
where a wrong court date is a real failure, not a bad chatbot answer.
That discipline stuck when it was pointed at code.
<a href="docs/story/">Read the full story</a>.</p>
</div>
<div class="table-wrap cmp reveal">
<table>
<tr><th></th><th>Graphify</th><th>claude-mem</th><th class="hl">irag</th></tr>
<tr><td>Remembers</td><td>structure</td><td>conversations</td>
<td class="hl">structure + meaning + history</td></tr>
<tr><td>Can it be wrong?</td><td>rarely (it's a parse)</td>
<td>yes, silently, forever</td>
<td class="hl">yes, and it detects, records, and gates CI on it</td></tr>
<tr><td>Cost</td><td>free</td><td>scales with chat volume</td>
<td class="hl">reads free · writes on any cheap model</td></tr>
</table>
</div>
<p class="fineprint"><b>irag's trade-offs, honestly:</b> writes cost LLM
calls (pluggable: point them at a free-quota model), it needs Python
3.11+, and the linter verifies checkable claims (paths, symbols,
versions), not opinions. <a href="docs/comparison/">Full comparison →</a></p>
</section>
<section class="sec" id="connect">
<h2 class="sec-t">One project brain. Every coding agent.</h2>
<div class="why-cols">
<p><b><code>irag mcp</code> is a real Model Context Protocol server, not a
Claude-specific adapter.</b> Every client gets the same context, search, map,
impact, provenance, contradiction, update, lesson, decision, and session
tools — backed by the same SQLite file.</p>
<p>Switch tools without starting over. Run Codex and Claude in parallel
without mixing their session history. Read operations never call a model;
mutations share one repository lock so two agents cannot publish half an
update.</p>
</div>
<div class="term reveal tilt" role="img" aria-label="Connect Codex and Claude Code to the same irag MCP server">
<div class="term-bar" aria-hidden="true"><span class="tb"></span><span class="tb"></span><span class="tb"></span><span class="term-title">connect once</span></div>
<pre class="term-body">codex mcp add irag -- irag mcp --root /absolute/project
claude mcp add --scope project irag -- irag mcp --root /absolute/project

✓ 22 tools · same memory · live evidence · isolated sessions</pre>
</div>
<p class="fineprint"><a href="docs/mcp/">Connect Cursor, Windsurf, VS Code, or any MCP client →</a></p>
</section>
<section class="sec" id="intelligence">
<h2 class="sec-t">One machine remembers. One audits. One helps you decide what to build next.</h2>
<div class="why-cols">
<p><b>Main Summary is the whole project on one living page.</b> Not a vague
executive abstract: every current file, folder, decision, and lesson in full,
beside drift, contradictions, sessions, and audit state. Structural truth
refreshes automatically, stale prose says that it is stale, and Memory Trust
explains which evidence is lowering confidence instead of hiding it in a score.</p>
<p><b>Code Audit turns review risk into evidence.</b> It maps APIs, finds
dependency cycles, checks exact package versions against OSV, redacts suspected
secrets, and attaches file, line, confidence, and remediation to every finding.
The live API checker cannot wander beyond loopback or follow a redirect. Review
decisions survive harmless line shifts, risk acceptances can expire, and open
work exports as SARIF. Stale inventories fail closed.</p>
</div>
<div class="why-cols">
<p><b>App Proof catches the bugs that stay silent between layers.</b> It traces
frontend requests into discovered backend routes, then verifies the running
application: reachable pages, internal and external links, forms, visible
controls, safe API calls, optional Playwright behavior, and your project's real
test suites.</p>
<p>Then it pressure-tests read-only loopback routes with bounded concurrency.
There is no fake “all green”: each discovered check is passed, failed, blocked,
untested, or excluded. A run becomes a durable HTML/JSON repair packet you can
hand directly to any coding agent.</p>
</div>
<div class="term reveal tilt" role="img" aria-label="irag developer intelligence workspace">
<div class="term-bar" aria-hidden="true"><span class="tb"></span><span class="tb"></span><span class="tb"></span><span class="term-title">thinking studio</span></div>
<pre class="term-body">REPO   verified memory · current diff · latest audit
WEB    Brave / Tavily / SearXNG / DuckDuckGo · URL + retrieval time
MODES  code review · product · business · marketing · creative

→ sourced conversation + measured experiments + change-aware watchlists</pre>
</div>
<p class="fineprint">Current trend claims require live sources. Search failure
is disclosed. Repository text and web snippets are treated as untrusted data,
never as instructions.</p>
</section>
<section class="showcase" id="showcase" aria-label="irag turns your files into a graph">
<div class="sc-sticky">
<div class="sc-cap">
<h2 class="sc-cap-t" id="sc-cap-t">So your agent can look it up &mdash; for almost no tokens.</h2>
<p class="sc-cap-p" id="sc-cap-p">A node for every file, an edge for every import: one map your
agent queries instead of re-reading your tree, instantly and for zero tokens.</p>
</div>
<div class="sc-stage">
<svg class="sc-edges" id="sc-edges" aria-hidden="true"></svg>
<div class="sc-nodes" id="sc-nodes" aria-hidden="true"></div>
</div>
<p class="sc-hint" aria-hidden="true">scroll</p>
</div>
</section>
<section class="sec" id="how">
<h2 class="sec-t">Memory in. Work happens. Truth comes back.</h2>
<div class="pipe">
<em class="pd1" aria-hidden="true"></em><em class="pd2" aria-hidden="true"></em>
<div class="pipe-col reveal">
<p class="mono-cap">reads · zero tokens</p>
<h3>Your agent</h3>
<p>Codex, Claude Code, Cursor, Windsurf, or the next tool: starts with the
same ranked context through MCP. No fresh-chat amnesia.</p>
</div>
<div class="pipe-col mid reveal">
<p class="mono-cap">the memory</p>
<h3>irag</h3>
<p>One SQLite file in your repo. Versions every page, logs isolated sessions,
fact-checks checkable claims, and remembers renames. Reads call no model.</p>
</div>
<div class="pipe-col reveal">
<p class="mono-cap">writes · cheap quota</p>
<h3>The scribe</h3>
<p>Any LLM CLI you configure writes the summaries, on a free-quota
model, so bookkeeping never touches your agent's limits.</p>
</div>
</div>
<div class="term reveal tilt" role="img" aria-label="Terminal demo: irag init creates the memory, irag update fact-checks it, and a new agent session starts fully briefed">
<div class="term-bar" aria-hidden="true"><span class="tb"></span><span class="tb"></span><span class="tb"></span><span class="term-title">~/your-project</span></div>
<pre class="term-body" id="term-body" aria-hidden="true"></pre>
</div>
</section>
<section class="sec" id="built">
<h2 class="sec-t">This is infrastructure, not another prompt file.</h2>
<ol class="featlist">
<li class="reveal"><h3>One MCP brain for every agent</h3>
<p>Twenty-two standard tools. The same names, schemas, results, lock, and
database in Codex, Claude, Cursor, Windsurf, VS Code, and any MCP client.</p></li>
<li class="reveal"><h3>Full-stack proof, not frontend/backend hope</h3>
<p>One run connects static contracts to live HTTP, browser controls, real test
suites, and bounded stress—then keeps every unknown visible and produces the
repair brief.</p></li>
<li class="reveal"><h3>Your agent never greps again</h3>
<p>Search, code map, blast radius, and context come from SQL, not from
the model re-reading your tree.</p></li>
<li class="reveal"><h3>Contradictions arrive ready to fix</h3>
<p>Open one issue or a master HTML repair packet: claim, truth, source-grounded
context, and a complete prompt you can hand straight to a coding agent.</p></li>
<li class="reveal"><h3>Choose the cheapest writer</h3>
<p>Explicit adapters for Claude, Codex, agy, Ollama, and custom CLIs normalize
retries and output while recording model, latency, tokens, and optional cost.</p></li>
<li class="reveal"><h3>Upgrades that respect the only copy of your memory</h3>
<p>Ordered migrations create an automatic backup first, record their history,
and refuse a downgrade instead of gambling with newer data.</p></li>
<li class="reveal"><h3>Large repositories stop paying the full-scan tax</h3>
<p>Content edits reparse only changed source files. Topology changes stay
conservative, and renames keep their complete revision lineage.</p></li>
<li class="reveal"><h3>Live updates that survive the tab</h3>
<p>Every dashboard run has a durable job ID, SQLite-backed progress, and an SSE
stream that reconnects after refresh instead of losing the operation.</p></li>
<li class="reveal"><h3>Every chat resumes where the last ended</h3>
<p>Isolated session diaries retain changed behavior, decisions, invariants,
failures, validation, and the exact per-file revision trail.</p></li>
<li class="reveal"><h3>Live trends without pretending the model is current</h3>
<p>Every external result keeps its URL and retrieval time. Search failure stays
visible, and current claims never quietly fall back to model memory.</p></li>
<li class="reveal"><h3>A complete summary that refuses to summarize things away</h3>
<p>Every live memory page appears in full beside drift, audit, contradictions,
and sessions — searchable, filterable, continuously refreshed, and scored by
an explained evidence-based Memory Trust signal.</p></li>
<li class="reveal"><h3>From diff to defensible release</h3>
<p>Delivery maps contract removals, blast radius, related tests, native checks,
and release gates into one brief a person or coding agent can execute.</p></li>
<li class="reveal"><h3>Team memory without leaking the repository</h3>
<p>Reviewable, idempotent bundles carry decisions, lessons, experiments,
watchlists, topics, and audit triage—not code, transcripts, prompts, or commands.</p></li>
</ol>
</section>
<section class="sec" id="proof">
<h2 class="sec-t">Measured, not claimed.</h2>
<p class="lede">Run on irag's own source. The read
path costs nothing because nothing on it calls a model, which is a
property of the architecture rather than a benchmark you have to trust.</p>
<div class="proof">
<div class="pf"><b>0 model calls</b><span>for search, context, code maps,
blast radius, provenance, and contradiction reads</span></div>
<div class="pf"><b>27 modules</b><span>blast radius of irag/db.py,
transitive, parsed from the code</span></div>
<div class="pf"><b>~87 tokens</b><span>to resume a past conversation with
its per-file changes</span></div>
<div class="pf"><b>3 CI platforms</b><span>configured protocol and storage
checks for Linux, macOS, and Windows</span></div>
</div>
<p class="lede">One command runs the whole lifecycle end-to-end: ingest,
synthesize, fact-check, CI gate, rollback, sessions, dashboard API,
multi-language graph. The mock model in the suite deliberately
hallucinates a missing file, so the fact-checker is proven to catch it
rather than assumed to.</p>
</section>
<section class="sec" id="dash">
<h2 class="sec-t">See what it knows.</h2>
<p class="lede"><code>irag dashboard</code> is a local, zero-dependency web UI
&mdash; and a full peer of the CLI, not a read-only viewer. Browse every page and
diff its history, roll one back, trace a claim to the commit that created it, read
a past conversation verbatim, preview the exact briefing your agent receives,
and run every maintenance operation. App Proof adds a complete runtime lab for
contracts, pages, links, controls, APIs, browser journeys, tests, and stress.</p>
<div class="shots">
<figure class="tilt px"><img src="assets/pages-memory.jpg" alt="One page: summary, structure and history" loading="lazy">
<figcaption>one page, one object: summary, structure, history, problems</figcaption></figure>
<figure class="tilt px"><img src="assets/command-palette.jpg" alt="Command palette searching page text" loading="lazy">
<figcaption>⌘K searches page text, not just names &mdash; zero tokens</figcaption></figure>
</div>
</section>
<div class="marq" aria-hidden="true"><div class="marq-in">
<span>init</span><span>update</span><span>search</span><span>ask</span><span>map</span><span>impact</span><span>context</span><span>recap</span><span>why</span><span>asof</span><span>lint</span><span>check</span><span>proof</span><span>sessions</span><span>transcript</span><span>rollback</span><span>learn</span><span>record-decision</span><span>dashboard</span>
<span>init</span><span>update</span><span>search</span><span>ask</span><span>map</span><span>impact</span><span>context</span><span>recap</span><span>why</span><span>asof</span><span>lint</span><span>check</span><span>proof</span><span>sessions</span><span>transcript</span><span>rollback</span><span>learn</span><span>record-decision</span><span>dashboard</span>
</div></div>
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
<div class="install"><code>pip install git+https://github.com/Karang1908/irag.git</code><button class="code-copy" type="button" aria-label="Copy install command">copy</button></div>
</section>
<footer class="foot land">
<span>MIT · <a href="{GITHUB}" target="_blank"
rel="noopener">Karang1908/irag</a></span>
<span>v{VERSION} · zero dependencies</span>
</footer>
</main>"""
    return page_shell(
        "irag — pluggable memory for AI coding agents",
        "Pluggable, fact-checked memory and full-stack application proof for AI coding agents. One "
        "SQLite file in your repo. Reads are free SQL; writes run on "
        "any cheap model.",
        body, rel, extra_class="is-landing", anime=True)


# ---------------------------------------------------------------------
# build
# ---------------------------------------------------------------------
def build(out: Path) -> int:
    # Path.exists() is false for a broken symlink. Check links first so a
    # stale link never falls through to mkdir() as an opaque FileExistsError
    # (or gets followed if its target later reappears).
    if out.is_symlink():
        raise SystemExit(
            f"refusing to replace {out}: output must be a real directory")
    if out.exists():
        if not out.is_dir():
            raise SystemExit(
                f"refusing to replace {out}: output must be a real directory")
        generated = (out / BUILD_MARKER).is_file() or all(
            (out / name).is_file()
            for name in (".nojekyll", "index.html", "search-index.json"))
        if any(out.iterdir()) and not generated:
            raise SystemExit(
                f"refusing to replace {out}: it is not an irag-generated "
                f"site directory (missing {BUILD_MARKER})")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # Written before the rest so an interrupted build remains safely
    # rebuildable rather than becoming an unrecognized half-built directory.
    (out / BUILD_MARKER).write_text("generated by site/build.py\n")
    (out / "docs").mkdir()
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
        if slug == "changelog":
            html, toc = _collapse_changelog(html, toc)
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
