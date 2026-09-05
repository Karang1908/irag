"""Self-contained contradiction repair briefs for humans and coding agents."""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import html
import json
import sqlite3
from pathlib import Path

from . import structure


def contradiction_rows(conn: sqlite3.Connection,
                       contradiction_ids: list[int] | None = None) -> list[dict]:
    where = "c.resolved_at IS NULL AND COALESCE(p.deleted_at,'')=''"
    params: list[object] = []
    if contradiction_ids is not None:
        if not contradiction_ids:
            return []
        marks = ",".join("?" for _ in contradiction_ids)
        where += f" AND c.contradiction_id IN ({marks})"
        params.extend(contradiction_ids)
    rows = conn.execute(
        f"""SELECT c.*, p.subject_id, p.page_type, p.current_revision_id,
                   r.body_markdown current_page, r.version_number
            FROM contradictions c
            JOIN pages p ON p.page_id=c.page_id
            LEFT JOIN revisions r ON r.revision_id=p.current_revision_id
            WHERE {where}
            ORDER BY CASE c.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1
                         ELSE 2 END, c.detected_at DESC, c.contradiction_id""",
        params).fetchall()
    out = []
    facts_by_subject: dict[str, dict] = {}
    for row in rows:
        item = dict(row)
        subject = row["subject_id"]
        if subject not in facts_by_subject:
            facts_by_subject[subject] = structure.module_facts(conn, subject)
        item["structure"] = facts_by_subject[subject]
        out.append(item)
    return out


def agent_brief(root: Path, rows: list[dict]) -> str:
    """A deterministic repair prompt with enough context to act safely."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# irag contradiction repair brief",
        "",
        f"Repository: `{root}`",
        f"Generated: {now}",
        f"Open contradictions in scope: {len(rows)}",
        "",
        "## Mission",
        "",
        "Reconcile every item below against the current repository. The code "
        "and manifests are ground truth; irag's summary is memory that may be "
        "stale. Inspect the named source before deciding what to change.",
        "",
        "For each item: (1) reproduce the mismatch, (2) fix the source only if "
        "the source is actually wrong, otherwise refresh the memory with "
        "`irag update`, (3) run the smallest relevant tests, (4) run "
        "`irag lint`, and (5) dismiss with `irag resolve <id> --notes ...` "
        "only when the detector itself is demonstrably a false positive. "
        "Never dismiss a real stale or incorrect claim just to make the gate "
        "green.",
        "",
    ]
    if not rows:
        lines += ["## Result", "", "There are no open contradictions in scope."]
        return "\n".join(lines)
    shown_pages: set[str] = set()
    for i, row in enumerate(rows, 1):
        facts = row.get("structure") or {}
        symbols = ", ".join(f"`{s['name']}`" for s in
                            (facts.get("symbols") or [])[:20]) or "none indexed"
        imports = ", ".join(f"`{s}`" for s in
                            (facts.get("imports") or [])[:20]) or "none indexed"
        imported_by = ", ".join(f"`{s}`" for s in
                                (facts.get("imported_by") or [])[:20]) or "none indexed"
        subject = str(row["subject_id"])
        if subject in shown_pages:
            page = "_(Same current page as the earlier item for this path.)_"
        else:
            page = str(row.get("current_page") or "(no current summary)")
            if len(page) > 6000:
                page = page[:6000] + "\n\n_[summary excerpt truncated]_"
            shown_pages.add(subject)
        lines += [
            f"## {i}. Contradiction #{row['contradiction_id']} — "
            f"`{row['subject_id']}`",
            "",
            f"- Severity: **{row.get('severity') or 'medium'}**",
            f"- Type: `{row.get('ctype') or 'unknown'}`",
            f"- Detected by: `{row.get('detected_by') or 'unknown'}`",
            f"- Detected at: {row.get('detected_at') or 'unknown'}",
            f"- Memory claim: {row.get('claim') or '(empty)' }",
            f"- Observed truth: {row.get('truth') or '(empty)' }",
            "",
            "### Verified structural context",
            "",
            f"- Defines: {symbols}",
            f"- Imports: {imports}",
            f"- Imported by: {imported_by}",
            "",
            f"### Current irag page (v{row.get('version_number') or '?'})",
            "",
            page,
            "",
            "### Required outcome",
            "",
            f"Explain whether contradiction #{row['contradiction_id']} came "
            "from stale memory, incorrect code, or a detector false positive. "
            f"Make the smallest correct repair, validate `{row['subject_id']}`, "
            "then report the commands run and the evidence that the claim and "
            "current truth now agree.",
            "",
        ]
    lines += [
        "## Completion gate",
        "",
        "Run `irag update`, `irag lint`, and the repository's relevant tests. "
        "Do not claim completion while any in-scope contradiction remains open "
        "unless you explain why it requires a human decision.",
    ]
    return "\n".join(lines)


def contradiction_html(conn: sqlite3.Connection, root: Path,
                       contradiction_ids: list[int] | None = None) -> str:
    rows = contradiction_rows(conn, contradiction_ids)
    brief = agent_brief(root, rows)
    single_id = (contradiction_ids[0]
                 if contradiction_ids is not None
                 and len(contradiction_ids) == 1 else None)
    scope = (f"contradiction-{single_id}"
             if single_id is not None else "all-open-contradictions")
    cards = []
    for row in rows:
        cards.append(
            '<article class="card">'
            f'<div class="card-head"><span class="severity {html.escape(str(row.get("severity") or "medium"))}">'
            f'{html.escape(str(row.get("severity") or "medium"))}</span>'
            f'<span>#{row["contradiction_id"]}</span></div>'
            f'<h2>{html.escape(str(row["subject_id"]))}</h2>'
            '<dl>'
            f'<dt>Memory says</dt><dd>{html.escape(str(row.get("claim") or "(empty)"))}</dd>'
            f'<dt>Code says</dt><dd>{html.escape(str(row.get("truth") or "(empty)"))}</dd>'
            f'<dt>Detector</dt><dd>{html.escape(str(row.get("detected_by") or row.get("ctype") or "unknown"))}</dd>'
            '</dl></article>')
    empty = ('<div class="empty">No open contradictions in this scope. '
             'The generated agent brief records that clean result.</div>')
    title = (f"Contradiction #{single_id}"
             if single_id is not None else "Master contradiction repair brief")
    brief_base64 = base64.b64encode(brief.encode("utf-8")).decode("ascii")
    filename_json = json.dumps(f"irag-{scope}.html")
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>{html.escape(title)} · irag</title>
<style>
:root{{--ink:#17191d;--muted:#626a76;--paper:#f7f4ed;--card:#fff;--line:#d9d5cc;--blue:#145dff;--red:#b42318;--orange:#b54708;--green:#087443}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 Inter,ui-sans-serif,system-ui,sans-serif}}
main{{max-width:1040px;margin:auto;padding:64px 24px 96px}}.eyebrow{{font:700 12px/1.2 ui-monospace,monospace;letter-spacing:.14em;text-transform:uppercase;color:var(--blue)}}
h1{{font-size:clamp(38px,7vw,76px);line-height:.95;letter-spacing:-.055em;max-width:850px;margin:16px 0 24px}}.lede{{font-size:20px;color:var(--muted);max-width:760px}}
.actions{{display:flex;gap:10px;flex-wrap:wrap;margin:28px 0 48px}}button{{border:1px solid var(--ink);background:var(--ink);color:#fff;border-radius:7px;padding:11px 16px;font-weight:700;cursor:pointer}}button.secondary{{background:transparent;color:var(--ink)}}button:focus-visible{{outline:3px solid #8ab4ff;outline-offset:2px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,360px),1fr));gap:16px;margin:0 0 48px}}.card{{background:var(--card);border:1px solid var(--line);padding:24px;border-radius:12px;box-shadow:0 12px 35px #26221a0a}}.card-head{{display:flex;justify-content:space-between;color:var(--muted);font:700 12px ui-monospace,monospace}}.card h2{{font:650 21px ui-monospace,monospace;overflow-wrap:anywhere}}dl{{margin:0}}dt{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin-top:14px}}dd{{margin:2px 0;overflow-wrap:anywhere}}.severity{{color:var(--orange)}}.severity.high{{color:var(--red)}}.severity.low{{color:var(--green)}}
.brief{{background:#101318;color:#e8ecf2;border-radius:12px;padding:28px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.65 ui-monospace,SFMono-Regular,Menlo,monospace}}.empty{{padding:28px;background:#fff;border:1px solid var(--line);border-radius:12px;color:var(--muted)}}
@media(max-width:600px){{main{{padding-top:36px}}h1{{font-size:42px}}}}
</style></head><body><main>
<p class="eyebrow">irag · coding-agent handoff</p><h1>{html.escape(title)}</h1>
<p class="lede">A source-grounded repair packet. Copy the prompt into any coding agent, or save this self-contained HTML file with the evidence attached.</p>
<div class="actions"><button id="copy" type="button">Copy agent brief</button><button class="secondary" id="download" type="button">Download HTML</button><span id="status" role="status" aria-live="polite"></span></div>
<section class="grid">{''.join(cards) if cards else empty}</section>
<pre class="brief" id="brief">{html.escape(brief)}</pre>
</main><script>
const brief=new TextDecoder().decode(Uint8Array.from(atob({json.dumps(brief_base64)}),c=>c.charCodeAt(0)));
document.getElementById('copy').onclick=async function(){{try{{if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(brief);else{{const area=document.createElement('textarea');area.value=brief;area.style.position='fixed';area.style.opacity='0';document.body.appendChild(area);area.select();if(!document.execCommand('copy'))throw new Error('copy unavailable');area.remove();}}this.textContent='Copied';document.getElementById('status').textContent='Agent brief copied.';}}catch(_error){{document.getElementById('status').textContent='Copy was blocked. Select the formatted brief below and copy it manually.';}}}};
document.getElementById('download').onclick=function(){{const blob=new Blob([document.documentElement.outerHTML],{{type:'text/html'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download={filename_json};a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}};
</script></body></html>'''
