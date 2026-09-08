"""Optional Python Playwright runner for App Proof.

The core package has no Playwright dependency. This module is launched in a
bounded subprocess only when the Node runner reports that a project-local
Playwright is unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlparse, urlunparse


def _check(kind: str, target: object, outcome: str, detail: str,
           evidence: object = "", duration_ms: int = 0) -> dict[str, Any]:
    return {"kind": kind, "target": str(target)[:2_000], "outcome": outcome,
            "detail": detail[:4_000], "evidence": str(evidence)[:30_000],
            "duration_ms": max(0, int(duration_ms))}


def _safe_url(value: object) -> str:
    raw = str(value)
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw[:2_000]
    return urlunparse(parsed._replace(query="…" if parsed.query else "",
                                      fragment=""))[:2_000]


def _network_outcome(row: dict[str, Any]) -> tuple[str, str]:
    status = row.get("status")
    if status in (401, 403):
        return "blocked", f"Browser request reached a protected boundary (HTTP {status})."
    if status:
        return "failed", f"Browser request returned HTTP {status}."
    return "failed", "Browser request failed before receiving an HTTP response."


def run(input_path: Path) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]
    except ImportError:
        return {"status": "unavailable", "checks": [_check(
            "browser", "Playwright", "blocked",
            "Neither a Node nor Python Playwright package is installed.")],
            "controls": [], "network": [], "errors": []}

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    base_url = str(payload["baseUrl"])
    origin = urlparse(base_url)
    origin_key = (origin.scheme, origin.hostname, origin.port)
    auth_name = str(payload.get("authEnv") or "")
    import os
    auth_value = os.environ.get(auth_name, "") if auth_name else ""
    timeout = max(1_000, int(payload.get("timeoutMs") or 5_000))
    max_controls = max(1, min(int(payload.get("maxControls") or 200), 500))
    allow_interactions = payload.get("allowInteractions") is True
    pages = list(payload.get("pages") or [base_url])[:40]
    artifact_dir = Path(str(payload["artifactDir"]))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    checks: list[dict[str, Any]] = []
    controls: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    remaining = max_controls
    limited = False

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                ignore_https_errors=False, service_workers="block")

            def guard_request(route):
                request = route.request
                parts = urlparse(request.url)
                same_origin = (parts.scheme, parts.hostname, parts.port) == origin_key
                if not same_origin and (request.is_navigation_request() or
                                        request.method not in ("GET", "HEAD", "OPTIONS")):
                    checks.append(_check(
                        "browser-boundary", _safe_url(request.url), "blocked",
                        "Navigation or a mutating request leaves the configured application origin."))
                    route.abort()
                elif request.is_navigation_request() or auth_value and same_origin:
                    try:
                        headers = dict(request.headers)
                        if auth_value and same_origin:
                            headers["Authorization"] = auth_value
                        response = route.fetch(
                            headers=headers,
                            max_redirects=0, timeout=timeout)
                        if 300 <= response.status < 400:
                            checks.append(_check(
                                "browser-boundary", _safe_url(request.url), "blocked",
                                "Browser redirect requires an explicit project-owned navigation test."))
                            route.abort()
                        else:
                            route.fulfill(response=response)
                    except Exception:
                        route.abort()
                else:
                    route.continue_()

            context.route("**/*", guard_request)
            try:
                for page_index, target in enumerate(pages):
                    parsed = urlparse(str(target))
                    if (parsed.scheme, parsed.hostname, parsed.port) != origin_key:
                        continue
                    page = context.new_page()

                    def page_error(error, target=target):
                        message = str(error)[:1_000]
                        errors.append({"page": _safe_url(target),
                                       "type": "pageerror", "message": message})

                    def console_error(message, target=target):
                        if message.type == "error":
                            errors.append({"page": _safe_url(target),
                                           "type": "console",
                                           "message": message.text[:1_000]})

                    def request_failed(request, target=target):
                        network.append({"page": _safe_url(target),
                                        "method": request.method,
                                        "url": _safe_url(request.url),
                                        "status": None,
                                        "error": str(request.failure or
                                                     "request failed")[:500]})

                    def bad_response(response, target=target):
                        if response.status >= 400:
                            network.append({"page": _safe_url(target),
                                            "method": response.request.method,
                                            "url": _safe_url(response.url),
                                            "status": response.status,
                                            "error": ""})

                    page.on("pageerror", page_error)
                    page.on("console", console_error)
                    page.on("requestfailed", request_failed)
                    page.on("response", bad_response)
                    page_network_start = len(network)
                    page_error_start = len(errors)
                    started = time.monotonic()
                    try:
                        response = page.goto(str(target),
                                             wait_until="domcontentloaded",
                                             timeout=timeout)
                        try:
                            page.wait_for_load_state(
                                "networkidle", timeout=min(timeout, 3_000))
                        except Exception:
                            pass
                        status = response.status if response else 0
                        fatal = [row for row in errors[page_error_start:]
                                 if row["type"] == "pageerror"]
                        checks.append(_check(
                            "browser-page", _safe_url(target),
                            "passed" if 0 < status < 400 and not fatal else "failed",
                            "The page loaded but raised an uncaught JavaScript error."
                            if fatal else f"Browser received HTTP {status}."
                            if status else "Browser received no main-document response.",
                            "\n".join(str(row["message"]) for row in fatal),
                            int((time.monotonic() - started) * 1_000)))
                        for row in network[page_network_start:]:
                            outcome, detail = _network_outcome(row)
                            checks.append(_check(
                                "browser-network", f"{row['method']} {row['url']}",
                                outcome, detail,
                                json.dumps({"page": row["page"],
                                            "error": row["error"]})))
                        for row in errors[page_error_start:]:
                            if row["type"] == "console":
                                checks.append(_check(
                                    "browser-console", row["page"], "failed",
                                    "The page emitted a console error.", row["message"]))
                    except Exception as exc:
                        checks.append(_check(
                            "browser-page", _safe_url(target), "failed",
                            "Browser navigation failed.", str(exc),
                            int((time.monotonic() - started) * 1_000)))
                        page.close()
                        continue

                    locator = page.locator(
                        "button, input[type=button], input[type=submit], "
                        "input[type=reset], [role=button]")
                    discovered = locator.count()
                    if discovered > remaining:
                        limited = True
                    count = min(discovered, remaining)
                    for index in range(count):
                        if index and allow_interactions:
                            try:
                                page.goto(str(target), wait_until="domcontentloaded",
                                          timeout=timeout)
                                try:
                                    page.wait_for_load_state(
                                        "networkidle", timeout=min(timeout, 3_000))
                                except Exception:
                                    pass
                            except Exception as exc:
                                checks.append(_check(
                                    "browser-control", f"control {index + 1}",
                                    "blocked", "The page could not be restored before "
                                    "testing this control.", str(exc)))
                                break
                        item = locator.nth(index)
                        try:
                            descriptor = item.evaluate("""node => ({
                              tag: node.tagName.toLowerCase(),
                              type: (node.getAttribute('type') || 'button').toLowerCase(),
                              label: (node.getAttribute('aria-label') || node.innerText ||
                                node.value || node.title || node.id || 'unnamed control')
                                .trim().replace(/\\s+/g, ' ').slice(0, 300),
                              disabled: Boolean(node.disabled ||
                                node.getAttribute('aria-disabled') === 'true'),
                              inForm: Boolean(node.closest('form'))
                            })""")
                        except Exception:
                            continue
                        remaining -= 1
                        record = {"page": _safe_url(target), **descriptor}
                        controls.append(record)
                        label = descriptor["label"]
                        if descriptor["disabled"]:
                            checks.append(_check("browser-control", label, "excluded",
                                                 "Control is disabled.", record["page"]))
                            continue
                        try:
                            visible = item.is_visible()
                        except Exception:
                            visible = False
                        if not visible:
                            checks.append(_check(
                                "browser-control", label, "excluded",
                                "Control is not visible at this viewport.", record["page"]))
                            continue
                        if not allow_interactions:
                            checks.append(_check(
                                "browser-control", label, "untested",
                                "Browser found the visible control; interaction mode is disabled.",
                                record["page"]))
                            continue
                        import re
                        suspicious = re.search(
                            r"\b(delete|remove|destroy|reset|logout|sign out|purchase|pay|"
                            r"send|publish|deploy|terminate|revoke)\b", label, re.I)
                        if (suspicious or descriptor["type"] in ("reset", "submit")
                                or descriptor["inForm"]):
                            checks.append(_check(
                                "browser-control", label, "blocked",
                                "Potentially mutating control requires an explicit "
                                "project-owned browser test.", record["page"]))
                            continue
                        before_errors = len(errors)
                        before_network = len(network)
                        before_url = page.url
                        before_text = page.locator("body").inner_text()
                        click_started = time.monotonic()
                        opened: list[Any] = []

                        def popup_handler(popup):
                            opened.append(popup)

                        page.once("popup", popup_handler)
                        try:
                            item.click(timeout=timeout)
                            page.wait_for_timeout(250)
                            try:
                                page.wait_for_load_state("networkidle", timeout=timeout)
                            except Exception:
                                pass
                            visible_change = (page.url != before_url or bool(opened) or
                                              page.locator("body").inner_text() != before_text)
                            page.remove_listener("popup", popup_handler)
                            for popup in opened:
                                try:
                                    popup.close()
                                except Exception:
                                    pass
                            new_errors = errors[before_errors:]
                            bad_network = [row for row in network[before_network:]
                                           if row.get("status") is None or
                                           int(row.get("status") or 0) >= 400]
                            blocked = (not new_errors and bool(bad_network) and
                                       all(row.get("status") in (401, 403)
                                           for row in bad_network))
                            failed = bool(new_errors or bad_network)
                            checks.append(_check(
                                "browser-control", label,
                                "blocked" if blocked else "failed" if failed else
                                "passed" if visible_change else "untested",
                                "Interaction reached a protected request and needs "
                                "authenticated fixture data." if blocked else
                                "Interaction produced a console/page error or failed HTTP request."
                                if failed else "Interaction changed visible text, navigation, "
                                "or opened a window without observed errors." if visible_change else
                                "Click produced no observable text or navigation change; verify "
                                "the intended result with a project-owned assertion.",
                                json.dumps({"page": record["page"],
                                            "visible_change": visible_change,
                                            "errors": new_errors,
                                            "network": bad_network}),
                                int((time.monotonic() - click_started) * 1_000)))
                        except Exception as exc:
                            page.remove_listener("popup", popup_handler)
                            checks.append(_check(
                                "browser-control", label, "failed",
                                "Visible control could not be activated.", str(exc),
                                int((time.monotonic() - click_started) * 1_000)))
                    try:
                        page.goto(str(target), wait_until="domcontentloaded",
                                  timeout=timeout)
                    except Exception:
                        pass
                    try:
                        page.screenshot(
                            path=str(artifact_dir /
                                     f"page-{page_index + 1:03d}.png"),
                            full_page=True)
                    except Exception:
                        pass
                    page.close()
            finally:
                context.close()
                browser.close()
    except Exception as exc:
        return {"status": "failed" if checks else "unavailable",
                "checks": checks + [_check(
            "browser", "Python Playwright", "failed" if checks else "blocked",
            "Browser verification stopped before completing." if checks else
            "Python Playwright is installed but could not launch a browser.", exc)],
            "controls": controls, "network": network[:1_000],
            "errors": errors[:1_000]}

    if limited:
        checks.append(_check(
            "coverage", "browser control limit", "blocked",
            f"More visible controls exist than the configured maxControls={max_controls}."))
    return {"status": "completed", "checks": checks, "controls": controls,
            "network": network[:1_000], "errors": errors[:1_000]}


def main() -> None:
    try:
        value = run(Path(sys.argv[1]))
    except Exception as exc:
        value = {"status": "failed", "checks": [_check(
            "browser", "Python Playwright run", "failed",
            "Browser runner crashed.", exc)], "controls": [],
            "network": [], "errors": []}
    sys.stdout.write(json.dumps(value, ensure_ascii=False))


if __name__ == "__main__":
    main()
