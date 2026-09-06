"""Provider-neutral live web search for the Thinking Studio.

Search is the only open-world read path in iRAG. It is explicit, bounded,
source-preserving, and separate from repository evidence. API keys are read
from environment variables and are never returned to the dashboard or stored
in SQLite.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote_plus, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


PROVIDERS = ("auto", "brave", "tavily", "searxng", "duckduckgo")
USER_AGENT = "iRAG/4 (+https://github.com/Karang1908/irag)"
MAX_RESPONSE_BYTES = 2_000_000


def availability(cfg: dict) -> dict[str, Any]:
    web = cfg.get("web", {})
    enabled = bool(web.get("enabled", True))
    configured = str(web.get("provider") or "auto").lower()
    endpoint = str(web.get("endpoint") or "").strip()
    selected = _select_provider(configured, endpoint)
    missing = ""
    if selected == "brave" and not os.environ.get("BRAVE_SEARCH_API_KEY"):
        missing = "BRAVE_SEARCH_API_KEY"
    elif selected == "tavily" and not os.environ.get("TAVILY_API_KEY"):
        missing = "TAVILY_API_KEY"
    elif selected == "searxng" and not endpoint:
        missing = "SearXNG endpoint"
    ready = enabled and not missing
    if not enabled:
        detail = "live web search is disabled"
    elif missing:
        detail = f"missing {missing}"
    elif selected == "duckduckgo":
        detail = "ready through DuckDuckGo HTML (no key; network checked on search)"
    elif selected == "searxng":
        detail = f"ready through SearXNG at {endpoint}"
    else:
        detail = f"ready through {selected.title()} (credential found in environment)"
    return {
        "enabled": enabled,
        "configured_provider": configured,
        "selected_provider": selected,
        "ready": ready,
        "detail": detail,
        "credential_env": {
            "brave": "BRAVE_SEARCH_API_KEY",
            "tavily": "TAVILY_API_KEY",
        }.get(selected),
    }


def search(cfg: dict, query: str) -> dict[str, Any]:
    query = " ".join(str(query).split())
    if not query:
        raise ValueError("web search query must not be empty")
    if len(query) > 1000:
        raise ValueError("web search query must be at most 1000 characters")
    state = availability(cfg)
    if not state["ready"]:
        raise RuntimeError(state["detail"])
    web = cfg.get("web", {})
    count = min(max(int(web.get("max_results", 6)), 1), 10)
    timeout = min(max(int(web.get("timeout", 12)), 2), 60)
    provider = str(state["selected_provider"])
    try:
        if provider == "brave":
            rows = _brave(query, count, timeout)
        elif provider == "tavily":
            rows = _tavily(query, count, timeout)
        elif provider == "searxng":
            rows = _searxng(str(web.get("endpoint") or ""), query,
                            count, timeout)
        else:
            rows = _duckduckgo(query, count, timeout)
    except HTTPError as exc:
        detail = f"HTTP {exc.code}"
        if exc.code in (401, 403):
            detail += " (check the search credential)"
        exc.close()
        raise RuntimeError(f"{provider} web search failed: {detail}") from None
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"{provider} web search failed: {reason}") from None
    rows = _dedupe(rows)[:count]
    if not rows:
        raise RuntimeError(
            f"{provider} web search returned no usable results; retry or "
            "choose another search provider")
    return {
        "query": query,
        "provider": provider,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "results": rows,
    }


def _select_provider(configured: str, endpoint: str) -> str:
    if configured != "auto":
        return configured if configured in PROVIDERS else "duckduckgo"
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        return "brave"
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    if endpoint:
        return "searxng"
    return "duckduckgo"


def _request(url: str, timeout: int, *, headers: dict[str, str] | None = None,
             body: dict | None = None) -> tuple[bytes, str]:
    actual_headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/html"}
    actual_headers.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        actual_headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=actual_headers)
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed/configured search providers
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise RuntimeError("web search response exceeded 2 MB")
        return raw, response.headers.get("Content-Type", "")


def _json_response(url: str, timeout: int, *, headers: dict[str, str] | None = None,
                   body: dict | None = None) -> dict:
    raw, _ = _request(url, timeout, headers=headers, body=body)
    try:
        value = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise RuntimeError("web search provider returned invalid JSON") from None
    if not isinstance(value, dict):
        raise RuntimeError("web search provider returned an unexpected payload")
    return value


def _brave(query: str, count: int, timeout: int) -> list[dict]:
    url = "https://api.search.brave.com/res/v1/web/search?" + urlencode({
        "q": query, "count": count, "search_lang": "en",
        "safesearch": "moderate",
    })
    data = _json_response(url, timeout, headers={
        "X-Subscription-Token": os.environ.get("BRAVE_SEARCH_API_KEY", "")
    })
    raw_web = data.get("web")
    web: dict = dict(raw_web) if isinstance(raw_web, dict) else {}
    return [_row(item) for item in web.get("results", [])
            if isinstance(item, dict)]


def _tavily(query: str, count: int, timeout: int) -> list[dict]:
    data = _json_response("https://api.tavily.com/search", timeout, headers={
        "Authorization": "Bearer " + os.environ.get("TAVILY_API_KEY", "")
    }, body={
        "query": query,
        "search_depth": "basic",
        "max_results": count,
        "include_answer": False,
        "include_raw_content": False,
    })
    return [_row(item) for item in data.get("results", [])
            if isinstance(item, dict)]


def _searxng(endpoint: str, query: str, count: int,
             timeout: int) -> list[dict]:
    base = endpoint.strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("SearXNG endpoint must be an http(s) URL")
    if parsed.username or parsed.password:
        raise ValueError("SearXNG credentials must not be embedded in the URL")
    data = _json_response(base + "/search?" + urlencode({
        "q": query, "format": "json", "language": "en",
        "safesearch": 1,
    }), timeout)
    return [_row(item) for item in data.get("results", [])
            if isinstance(item, dict)][:count]


class _DuckParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict] = []
        self._anchor: dict[str, str] | None = None
        self._snippet = False
        self._parts: list[str] = []

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = (values.get("class") or "").split()
        if tag == "a" and ({"result__a", "result-link"} & set(classes)):
            self._anchor = {"url": values.get("href") or "", "title": "",
                            "snippet": ""}
            self._parts = []
        elif ({"result__snippet", "result-snippet"} & set(classes)
              and self._anchor is not None):
            self._snippet = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._anchor is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._anchor is None:
            return
        if tag == "a" and not self._snippet and not self._anchor["title"]:
            self._anchor["title"] = _clean(" ".join(self._parts))
            self._parts = []
        elif self._snippet and tag in ("a", "div", "span", "td", "p"):
            self._anchor["snippet"] = _clean(" ".join(self._parts))
            self.rows.append(self._anchor)
            self._anchor = None
            self._snippet = False
            self._parts = []


def _duckduckgo(query: str, count: int, timeout: int) -> list[dict]:
    raw, _ = _request(
        "https://html.duckduckgo.com/html/?q=" + quote_plus(query), timeout,
        headers={"Accept": "text/html,application/xhtml+xml"})
    parser = _parse_duck(raw)
    if not parser.rows:
        # DuckDuckGo occasionally serves its bot-check/empty shell from the
        # HTML endpoint while its documented lightweight frontend still has
        # results. Try that independent renderer before reporting failure.
        raw, _ = _request(
            "https://lite.duckduckgo.com/lite/?q=" + quote_plus(query),
            timeout, headers={"Accept": "text/html,application/xhtml+xml"})
        parser = _parse_duck(raw)
    if not parser.rows:
        raise RuntimeError(
            "DuckDuckGo returned no parseable results; it may be rate-limiting "
            "automated searches. Configure Brave, Tavily, or SearXNG for a "
            "stable research API.")
    rows = []
    for item in parser.rows[:count * 2]:
        url = _duck_url(item.get("url", ""))
        rows.append({"title": item.get("title", ""), "url": url,
                     "snippet": item.get("snippet", "")})
    return rows


def _parse_duck(raw: bytes) -> _DuckParser:
    parser = _DuckParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return parser


def _duck_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = (parse_qs(parsed.query).get("uddg") or [""])[0]
        return unquote(target)
    return url


def _row(item: dict) -> dict:
    return {
        "title": _clean(str(item.get("title") or "Untitled"))[:300],
        "url": str(item.get("url") or "").strip()[:4000],
        "snippet": _clean(str(item.get("description") or item.get("content")
                              or item.get("snippet") or ""))[:1200],
        "published": _clean(str(item.get("published") or
                                  item.get("published_date") or ""))[:100],
    }


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value)).strip()


def _dedupe(rows: list[dict]) -> list[dict]:
    out = []
    seen: set[str] = set()
    for item in rows:
        row = _row(item)
        parsed = urlparse(row["url"])
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        key = row["url"].split("#", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        row["domain"] = parsed.netloc.lower()
        out.append(row)
    return out
