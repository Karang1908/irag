"""Optional real-browser regression checks. Run: python3 tests/check_proof_browser.py.

Requires Python Playwright with Chromium installed; also exercises the Node
runner against Playwright's bundled Node API when Node is available.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading


def main() -> None:
    import playwright
    root = Path(__file__).resolve().parents[1]
    secret = "Bearer fixture-auth-never-store"
    outside_headers: list[str | None] = []
    app_headers: list[str | None] = []

    class Outside(BaseHTTPRequestHandler):
        def do_GET(self):
            outside_headers.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.end_headers()
            self.wfile.write(b'<svg xmlns="http://www.w3.org/2000/svg"/>')

        def log_message(self, *_args):
            pass

    outside = ThreadingHTTPServer(("127.0.0.1", 0), Outside)
    outside_url = f"http://127.0.0.1:{outside.server_port}"

    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            app_headers.append(self.headers.get("Authorization"))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", outside_url + "/redirect-destination")
                self.end_headers()
                return
            if self.path not in ("/", "/broken"):
                self.send_error(404)
                return
            body = (
                "<!doctype html><html><body>"
                f'<img alt="fixture" src="{outside_url}/asset.svg">'
                '<button id="noop">No effect</button>'
                '<button id="show" onclick="document.getElementById(\'result\').hidden=false">Show detail</button>'
                '<span id="result" hidden>Detail is visible</span>'
                '<button id="bad" onclick="fetch(\'/missing-on-click\')">Load missing data</button>'
                '<button onclick="location.href=\'/redirect\'">Leave local app</button>'
                '<form><button>Save fixture</button></form>'
                + ("<script>fetch('/missing-on-load')</script>" if self.path == "/broken" else "")
                + "</body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    app = ThreadingHTTPServer(("127.0.0.1", 0), App)
    for server in (outside, app):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{app.server_port}"
    environment = {**os.environ, "IRAG_BROWSER_FIXTURE_AUTH": secret}
    try:
        with tempfile.TemporaryDirectory(prefix="irag-browser-check-") as tmp:
            directory = Path(tmp)
            input_path = directory / "input.json"
            input_path.write_text(json.dumps({
                "baseUrl": base, "pages": [base + "/", base + "/broken"],
                "maxControls": 10, "allowInteractions": True,
                "timeoutMs": 2_000, "artifactDir": tmp,
                "authEnv": "IRAG_BROWSER_FIXTURE_AUTH",
            }))
            commands = [("Python", [sys.executable,
                                     str(root / "irag/proof_browser.py"), str(input_path)])]
            node = shutil.which("node")
            driver = Path(playwright.__file__).parent / "driver/package/index.js"
            if node and driver.is_file():
                # Both distributions expose the same Playwright Node API.
                # Supply that already-installed module without installing npm dependencies.
                bootstrap = (
                    "const M=require('module'),old=M._resolveFilename;"
                    "const [driver,runner,input]=process.argv.slice(1);"
                    "M._resolveFilename=function(name,...args){return name==='playwright'?"
                    "driver:old.call(this,name,...args)};"
                    "process.argv=[process.execPath,runner,input];require(runner);"
                )
                commands.append(("Node", [node, "-e", bootstrap, str(driver),
                                          str(root / "irag/assets/proof_runner.js"),
                                          str(input_path)]))
            for label, command in commands:
                response = subprocess.run(command, env=environment, capture_output=True,
                                          text=True, timeout=90, cwd=root)
                assert response.returncode == 0, (label, response.stderr, response.stdout)
                value = json.loads(response.stdout)
                assert value["status"] == "completed", (label, value)
                checks = value["checks"]
                outcomes = {(row["kind"], row["target"]): row["outcome"] for row in checks}
                assert outcomes[("browser-control", "No effect")] == "untested", label
                assert outcomes[("browser-control", "Show detail")] == "passed", label
                assert outcomes[("browser-control", "Load missing data")] == "failed", label
                assert outcomes[("browser-control", "Save fixture")] == "blocked", label
                assert any(row["kind"] == "browser-network" and
                           "missing-on-load" in row["target"] and
                           row["outcome"] == "failed" for row in checks), label
                assert any(row["kind"] == "browser-boundary" and
                           row["outcome"] == "blocked" for row in checks), label
                assert not any(row["target"] == "browser control limit"
                               for row in checks), (label, "exact limit falsely blocked")
                assert secret not in response.stdout, label
                print(f"{label}: load/click failures, no-op, visible change, form boundary, "
                      "redirect boundary, and exact control limit passed")
            assert app_headers and secret in app_headers, "app never received its test auth"
            assert outside_headers and all(value is None for value in outside_headers), \
                "Authorization escaped the application origin"
            print("Authorization stayed on the configured origin")
    finally:
        for server in (app, outside):
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
