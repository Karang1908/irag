#!/usr/bin/env node
"use strict";

// Optional Playwright evidence collector for iRAG App Proof. This runner is
// deliberately dependency-neutral: it uses a project's local Playwright when
// available and returns an honest blocked result when it is not installed.
const fs = require("node:fs");
const path = require("node:path");

function check(kind, target, outcome, detail, evidence = "", durationMs = 0) {
  return {kind, target: String(target).slice(0, 2000), outcome, detail,
    evidence: String(evidence).slice(0, 30000), duration_ms: Math.max(0, durationMs)};
}

function safeUrl(value) {
  try {
    const parsed = new URL(value);
    parsed.search = parsed.search ? "?…" : "";
    parsed.hash = "";
    return parsed.toString();
  } catch (_) {
    return String(value).slice(0, 2000);
  }
}

function loadPlaywright() {
  for (const name of ["playwright", "@playwright/test"]) {
    try {
      return require(require.resolve(name, {paths: [process.cwd()]}));
    } catch (_) { /* The next package or Python fallback may be installed. */ }
  }
  return null;
}

async function main() {
  const inputPath = process.argv[2];
  if (!inputPath) throw new Error("proof runner requires an input file");
  const input = JSON.parse(fs.readFileSync(inputPath, "utf8"));
  const playwright = loadPlaywright();
  if (!playwright || !playwright.chromium) {
    return {status: "unavailable", checks: [check("browser", "Playwright", "blocked",
      "Playwright is not installed in this project. Add it to collect real browser evidence.")],
      controls: [], network: [], errors: []};
  }

  const origin = new URL(input.baseUrl).origin;
  const authName = typeof input.authEnv === "string" ? input.authEnv : "";
  const authValue = authName ? process.env[authName] || "" : "";
  const browser = await playwright.chromium.launch({headless: true});
  const context = await browser.newContext({ignoreHTTPSErrors: false, serviceWorkers: "block"});
  const checks = [];
  const controls = [];
  const network = [];
  const errors = [];
  await context.route("**/*", async route => {
    const request = route.request();
    const sameOrigin = new URL(request.url()).origin === origin;
    if (!sameOrigin && (request.isNavigationRequest() || !["GET", "HEAD", "OPTIONS"].includes(request.method()))) {
      checks.push(check("browser-boundary", safeUrl(request.url()), "blocked",
        "Navigation or a mutating request leaves the configured application origin."));
      await route.abort();
      return;
    }
    if (request.isNavigationRequest() || (authValue && sameOrigin)) {
      // Fetch one response without following redirects, so an app cannot
      // redirect the configured credential to another origin.
      try {
        const headers = {...request.headers()};
        if (authValue && sameOrigin) headers.Authorization = authValue;
        const response = await route.fetch({headers,
          maxRedirects: 0, timeout: Number(input.timeoutMs) || 5000});
        if (response.status() >= 300 && response.status() < 400) {
          checks.push(check("browser-boundary", safeUrl(request.url()), "blocked",
            "Browser redirect requires an explicit project-owned navigation test."));
          await route.abort();
        } else { await route.fulfill({response}); }
      } catch (_) { await route.abort().catch(() => {}); }
    } else {
      await route.continue();
    }
  });
  let remaining = Math.max(1, Math.min(Number(input.maxControls) || 200, 500));
  let limited = false;
  const pages = Array.isArray(input.pages) && input.pages.length
    ? input.pages.slice(0, 40) : [input.baseUrl];

  try {
    for (let pageIndex = 0; pageIndex < pages.length; pageIndex += 1) {
      const target = pages[pageIndex];
      let targetUrl;
      try {
        targetUrl = new URL(target);
        if (targetUrl.origin !== origin) continue;
      } catch (_) { continue; }
      const page = await context.newPage();
      let pageErrors = [];
      page.on("pageerror", error => {
        const message = String(error && error.message || error).slice(0, 1000);
        pageErrors.push(message); errors.push({page: safeUrl(target), type: "pageerror", message});
      });
      page.on("console", message => {
        if (message.type() === "error") {
          const text = message.text().slice(0, 1000);
          errors.push({page: safeUrl(target), type: "console", message: text});
        }
      });
      page.on("requestfailed", request => {
        const row = {page: safeUrl(target), method: request.method(),
          url: safeUrl(request.url()), status: null,
          error: String(request.failure() && request.failure().errorText || "request failed").slice(0, 500)};
        network.push(row);
      });
      page.on("response", response => {
        if (response.status() >= 400) network.push({page: safeUrl(target),
          method: response.request().method(), url: safeUrl(response.url()),
          status: response.status(), error: ""});
      });

      const started = Date.now();
      const pageNetworkStart = network.length;
      const pageErrorStart = errors.length;
      try {
        const response = await page.goto(targetUrl.toString(), {
          waitUntil: "domcontentloaded", timeout: Number(input.timeoutMs) || 5000});
        await page.waitForLoadState("networkidle", {
          timeout: Math.min(Number(input.timeoutMs) || 5000, 3000)}).catch(() => {});
        const status = response ? response.status() : 0;
        const fatalPageErrors = errors.slice(pageErrorStart)
          .filter(row => row.type === "pageerror");
        checks.push(check("browser-page", safeUrl(target),
          status > 0 && status < 400 && fatalPageErrors.length === 0 ? "passed" : "failed",
          fatalPageErrors.length ? "The page loaded but raised an uncaught JavaScript error."
            : status ? `Browser received HTTP ${status}.` : "Browser received no main-document response.",
          pageErrors.join("\n"), Date.now() - started));
        for (const row of network.slice(pageNetworkStart)) {
          const blocked = row.status === 401 || row.status === 403;
          checks.push(check("browser-network", `${row.method} ${row.url}`,
            blocked ? "blocked" : "failed",
            blocked ? `Browser request reached a protected boundary (HTTP ${row.status}).`
              : row.status ? `Browser request returned HTTP ${row.status}.`
                : "Browser request failed before receiving an HTTP response.",
            JSON.stringify({page: row.page, error: row.error || ""})));
        }
        for (const row of errors.slice(pageErrorStart).filter(row => row.type === "console")) {
          checks.push(check("browser-console", row.page, "failed",
            "The page emitted a console error.", row.message));
        }
      } catch (error) {
        checks.push(check("browser-page", safeUrl(target), "failed",
          "Browser navigation failed.", String(error && error.message || error), Date.now() - started));
        await page.close();
        continue;
      }

      const locator = page.locator("button, input[type=button], input[type=submit], input[type=reset], [role=button]");
      const discovered = await locator.count();
      if (discovered > remaining) limited = true;
      const count = Math.min(discovered, remaining);
      for (let index = 0; index < count; index += 1) {
        // Every interaction starts from the same page state. A previous
        // button may navigate or replace the DOM; without this reset the
        // nth locator can silently refer to a completely different control.
        if (index > 0 && input.allowInteractions) {
          try {
            await page.goto(targetUrl.toString(), {
              waitUntil: "domcontentloaded", timeout: Number(input.timeoutMs) || 5000});
            await page.waitForLoadState("networkidle", {
              timeout: Math.min(Number(input.timeoutMs) || 5000, 3000)}).catch(() => {});
          } catch (error) {
            checks.push(check("browser-control", `control ${index + 1}`, "blocked",
              "The page could not be restored before testing this control.",
              String(error && error.message || error)));
            break;
          }
        }
        const item = locator.nth(index);
        let descriptor;
        try {
          descriptor = await item.evaluate(node => ({
            tag: node.tagName.toLowerCase(), type: (node.getAttribute("type") || "button").toLowerCase(),
            label: (node.getAttribute("aria-label") || node.innerText || node.value || node.title || node.id || "unnamed control").trim().replace(/\s+/g, " ").slice(0, 300),
            disabled: Boolean(node.disabled || node.getAttribute("aria-disabled") === "true"),
            inForm: Boolean(node.closest("form")),
          }));
        } catch (_) { continue; }
        remaining -= 1;
        const record = {page: safeUrl(target), ...descriptor};
        controls.push(record);
        if (descriptor.disabled) {
          checks.push(check("browser-control", descriptor.label, "excluded",
            "Control is disabled.", record.page));
          continue;
        }
        if (!await item.isVisible().catch(() => false)) {
          checks.push(check("browser-control", descriptor.label, "excluded",
            "Control is not visible at this viewport.", record.page));
          continue;
        }
        if (!input.allowInteractions) {
          checks.push(check("browser-control", descriptor.label, "untested",
            "Browser found the visible control; interaction mode is disabled.", record.page));
          continue;
        }
        const suspicious = /\b(delete|remove|destroy|reset|logout|sign out|purchase|pay|send|publish|deploy|terminate|revoke)\b/i.test(descriptor.label);
        if (suspicious || descriptor.type === "reset" || descriptor.type === "submit" || descriptor.inForm) {
          checks.push(check("browser-control", descriptor.label, "blocked",
            "Potentially mutating control requires an explicit project-owned browser test.", record.page));
          continue;
        }
        const beforeErrors = errors.length;
        const beforeNetwork = network.length;
        const beforeUrl = page.url();
        const beforeText = await page.locator("body").innerText().catch(() => "");
        const clickStarted = Date.now();
        let opened = null;
        const onPopup = popup => { opened = popup; };
        page.once("popup", onPopup);
        try {
          await item.click({timeout: Number(input.timeoutMs) || 5000});
          await page.waitForTimeout(250);
          await page.waitForLoadState("networkidle", {
            timeout: Number(input.timeoutMs) || 5000}).catch(() => {});
          const visibleChange = page.url() !== beforeUrl || Boolean(opened) ||
            await page.locator("body").innerText().catch(() => "") !== beforeText;
          const newErrors = errors.slice(beforeErrors);
          const badNetwork = network.slice(beforeNetwork)
            .filter(row => row.status === null || row.status >= 400);
          const blocked = newErrors.length === 0 && badNetwork.length > 0 &&
            badNetwork.every(row => row.status === 401 || row.status === 403);
          const failed = newErrors.length > 0 || badNetwork.length > 0;
          checks.push(check("browser-control", descriptor.label,
            blocked ? "blocked" : failed ? "failed" : visibleChange ? "passed" : "untested",
            blocked ? "Interaction reached a protected request and needs authenticated fixture data."
              : failed ? "Interaction produced a console/page error or failed HTTP request."
                : visibleChange ? "Interaction changed visible text, navigation, or opened a window without observed errors."
                  : "Click produced no observable text or navigation change; verify the intended result with a project-owned assertion.",
            JSON.stringify({page: record.page, visible_change: visibleChange, errors: newErrors, network: badNetwork}), Date.now() - clickStarted));
        } catch (error) {
          checks.push(check("browser-control", descriptor.label, "failed",
            "Visible control could not be activated.", String(error && error.message || error), Date.now() - clickStarted));
        } finally {
          page.off("popup", onPopup);
          if (opened) await opened.close().catch(() => {});
        }
      }
      await page.goto(targetUrl.toString(), {
        waitUntil: "domcontentloaded", timeout: Number(input.timeoutMs) || 5000}).catch(() => {});
      const shotName = `page-${String(pageIndex + 1).padStart(3, "0")}.png`;
      await page.screenshot({path: path.join(input.artifactDir, shotName), fullPage: true})
        .catch(() => {});
      await page.close();
    }
  } finally {
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  }
  if (limited) checks.push(check("coverage", "browser control limit", "blocked",
    `More visible controls exist than the configured maxControls=${input.maxControls}.`));
  return {status: "completed", checks, controls, network: network.slice(0, 1000),
    errors: errors.slice(0, 1000)};
}

main().then(result => process.stdout.write(JSON.stringify(result))).catch(error => {
  process.stdout.write(JSON.stringify({status: "failed", checks: [check("browser", "Playwright run", "failed",
    "Browser runner crashed.", String(error && error.stack || error))], controls: [], network: [], errors: []}));
  process.exitCode = 1;
});
