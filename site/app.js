/* irag docs — search, copy buttons, TOC highlight. No dependencies. */
(function () {
  "use strict";

  /* ---------- mobile menu ---------- */
  var menuBtn = document.querySelector(".menu-btn");
  if (menuBtn) {
    menuBtn.addEventListener("click", function () {
      var side = menuBtn.closest(".side");
      var open = side.classList.toggle("open");
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  /* ---------- copy buttons ---------- */
  document.querySelectorAll(".code-copy").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var host = btn.closest(".code-block, .install");
      var code = host ? host.querySelector("code") : null;
      if (!code) return;
      navigator.clipboard.writeText(code.textContent).then(function () {
        btn.textContent = "copied";
        btn.classList.add("ok");
        setTimeout(function () {
          btn.textContent = "copy";
          btn.classList.remove("ok");
        }, 1400);
      });
    });
  });

  /* ---------- search ---------- */
  var input = document.getElementById("search");
  var results = document.getElementById("search-results");
  if (input && results) {
    var index = null;
    var rel = location.pathname.indexOf("/docs/") !== -1 ? "../../" : "";
    var sel = -1;

    function load() {
      if (index) return Promise.resolve(index);
      return fetch(rel + "search-index.json")
        .then(function (r) { return r.json(); })
        .then(function (d) { index = d; return d; });
    }
    function esc(s) {
      return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }
    function hl(s, q) {
      var i = s.toLowerCase().indexOf(q.toLowerCase());
      if (i < 0) return esc(s);
      return esc(s.slice(0, i)) + "<mark>" + esc(s.slice(i, i + q.length)) +
        "</mark>" + esc(s.slice(i + q.length));
    }
    function run() {
      var q = input.value.trim();
      sel = -1;
      if (q.length < 2) { results.hidden = true; results.innerHTML = ""; return; }
      load().then(function (idx) {
        var ql = q.toLowerCase();
        var hits = [];
        for (var i = 0; i < idx.length && hits.length < 12; i++) {
          var e = idx[i];
          var inH = (e.h || "").toLowerCase().indexOf(ql) !== -1;
          var inT = e.t.toLowerCase().indexOf(ql) !== -1;
          var inX = (e.x || "").toLowerCase().indexOf(ql) !== -1;
          if (inH || inT || inX) {
            hits.push({ e: e, score: inH ? 0 : inT ? 1 : 2 });
          }
        }
        hits.sort(function (a, b) { return a.score - b.score; });
        if (!hits.length) {
          results.innerHTML = '<div class="none">No matches for “' +
            esc(q) + "”</div>";
          results.hidden = false;
          return;
        }
        results.innerHTML = hits.map(function (h) {
          var e = h.e;
          var head = e.h
            ? hl(e.h, q) + "<small>" + esc(e.t) + "</small>"
            : hl(e.t, q);
          return '<a href="' + rel + e.u + '"><div class="r-t">' + head +
            '</div><div class="r-x">' + hl(e.x || "", q) + "</div></a>";
        }).join("");
        results.hidden = false;
      });
    }
    input.addEventListener("input", run);
    input.addEventListener("keydown", function (ev) {
      var links = results.querySelectorAll("a");
      if (ev.key === "Escape") { results.hidden = true; input.blur(); return; }
      if (!links.length || results.hidden) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        sel = ev.key === "ArrowDown"
          ? Math.min(sel + 1, links.length - 1)
          : Math.max(sel - 1, 0);
        links.forEach(function (a, i) { a.classList.toggle("sel", i === sel); });
        links[sel].scrollIntoView({ block: "nearest" });
      } else if (ev.key === "Enter" && sel >= 0) {
        ev.preventDefault();
        links[sel].click();
      }
    });
    document.addEventListener("click", function (ev) {
      if (!ev.target.closest(".search-box")) results.hidden = true;
    });
    /* "/" focuses search (not while typing elsewhere) */
    document.addEventListener("keydown", function (ev) {
      var t = ev.target;
      if (ev.key === "/" && !(t && (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" || t.isContentEditable))) {
        ev.preventDefault();
        input.focus();
      }
    });
  }

  /* ---------- TOC scroll highlight ---------- */
  var toc = document.querySelector(".toc");
  if (toc && "IntersectionObserver" in window) {
    var links = {};
    toc.querySelectorAll("a[href^='#']").forEach(function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    var current = null;
    var obs = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting && links[en.target.id]) {
          if (current) current.classList.remove("on");
          current = links[en.target.id];
          current.classList.add("on");
        }
      });
    }, { rootMargin: "0px 0px -70% 0px" });
    Object.keys(links).forEach(function (id) {
      var el = document.getElementById(id);
      if (el) obs.observe(el);
    });
  }
}());
