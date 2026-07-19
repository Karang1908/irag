/* irag docs — search, copy buttons, TOC highlight, landing animations.
   No dependencies. */
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  var REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- scroll reveal ---------- */
  var reveals = document.querySelectorAll(".reveal");
  if (reveals.length) {
    if (REDUCE || !("IntersectionObserver" in window)) {
      reveals.forEach(function (el) { el.classList.add("in"); });
    } else {
      var rObs = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) {
            en.target.classList.add("in");
            rObs.unobserve(en.target);
          }
        });
      }, { threshold: 0.12 });
      reveals.forEach(function (el) { rObs.observe(el); });
    }
  }

  /* ---------- hero intro (anime.js, progressive enhancement) ----------
     Markup is authored in its final state; anime sets the from-state at
     runtime, so with no JS / no anime / reduced motion nothing is hidden. */
  var heroName = document.querySelector(".hero-name");
  if (heroName && window.anime && !REDUCE) {
    var letters = heroName.querySelectorAll("span");
    var gp = document.querySelectorAll(".gp");
    var gn = document.querySelectorAll(".gn");
    var gl = document.querySelectorAll(".glabels text");
    anime.set(letters, { translateY: "112%" });
    anime.set(".hero-eyebrow,.lede,.hero .cta,.hero .install",
      { opacity: 0, translateY: 14 });
    anime.set(".stat", { opacity: 0, translateY: 16 });
    if (gn.length) anime.set(gn, { scale: 0 });
    if (gl.length) anime.set(gl, { opacity: 0 });
    var tl = anime.timeline({ easing: "easeOutExpo" });
    tl.add({ targets: ".hero-eyebrow", opacity: 1, translateY: 0,
      duration: 500 }, 0)
      .add({ targets: letters, translateY: "0%", duration: 800,
        delay: anime.stagger(55) }, 60)
      .add({ targets: ".lede", opacity: 1, translateY: 0,
        duration: 600 }, 300)
      .add({ targets: ".hero .cta", opacity: 1, translateY: 0,
        duration: 600 }, 420)
      .add({ targets: ".hero .install", opacity: 1, translateY: 0,
        duration: 600 }, 510)
      .add({ targets: ".stat", opacity: 1, translateY: 0, duration: 550,
        delay: anime.stagger(70) }, 600);
    if (gp.length) {
      tl.add({ targets: gp, strokeDashoffset: [anime.setDashoffset, 0],
        easing: "easeInOutSine", duration: 850,
        delay: anime.stagger(45) }, 300)
        .add({ targets: gn, scale: 1, easing: "easeOutBack",
          duration: 500, delay: anime.stagger(40) }, 750)
        .add({ targets: gl, opacity: 1, duration: 400,
          delay: anime.stagger(30) }, 1100);
    }
  }

  /* ---------- terminal typewriter ---------- */
  var term = document.getElementById("term-body");
  if (term) {
    var LINES = [
      ["c", "$ irag init"],
      ["o", "✓ memory created — .irag/memory.db (one SQLite file)"],
      ["c", "$ irag update"],
      ["o", "✓ 42 pages written · fact-checked against code · 0 contradictions"],
      ["c", "$ irag recap"],
      ["o", "» last session: fixed token refresh — 3 files, 1 decision"],
      ["c", "$ claude"],
      ["o", "→ agent briefed in ~3k tokens. no grep. no re-reading."]
    ];
    var escT = function (s) {
      return s.replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    };
    var lineHtml = function (kind, text) {
      if (kind === "o") {
        var m = text.match(/^([✓»→])\s([\s\S]*)$/);
        if (m) {
          return '<span class="to"><span class="tg">' + m[1] + "</span> " +
            escT(m[2]) + "</span>";
        }
        return '<span class="to">' + escT(text) + "</span>";
      }
      return '<span class="tc">' + escT(text) + "</span>";
    };
    var renderTerm = function (done, kind, partial, caret) {
      var html = "";
      for (var i = 0; i < done; i++) {
        html += lineHtml(LINES[i][0], LINES[i][1]) + "\n";
      }
      if (partial !== null) html += lineHtml(kind, partial);
      if (caret) html += '<span class="term-caret"></span>';
      term.innerHTML = html;
    };
    if (REDUCE) {
      renderTerm(LINES.length, "c", null, false);
    } else {
      var li = 0, ci = 0;
      var tick = function () {
        if (li >= LINES.length) {           // hold, then restart
          renderTerm(LINES.length, "c", null, true);
          li = 0; ci = 0;
          setTimeout(tick, 4200);
          return;
        }
        var kind = LINES[li][0], text = LINES[li][1];
        if (kind === "c") {                 // commands type out
          ci++;
          renderTerm(li, "c", text.slice(0, ci), true);
          if (ci >= text.length) {
            li++; ci = 0;
            setTimeout(tick, 320);
          } else {
            setTimeout(tick, 34);
          }
        } else {                            // output appears at once
          li++;
          renderTerm(li, "o", null, true);
          setTimeout(tick, li < LINES.length && LINES[li][0] === "o"
            ? 260 : 700);
        }
      };
      tick();
    }
  }

  /* ---------- 3D tilt on hover ---------- */
  if (!REDUCE && matchMedia("(hover: hover)").matches) {
    document.querySelectorAll(".tilt").forEach(function (el) {
      el.addEventListener("mousemove", function (ev) {
        var r = el.getBoundingClientRect();
        var x = (ev.clientX - r.left) / r.width - 0.5;
        var y = (ev.clientY - r.top) / r.height - 0.5;
        el.style.transform = "perspective(900px) rotateX(" +
          (-y * 6).toFixed(2) + "deg) rotateY(" + (x * 8).toFixed(2) +
          "deg)";
      });
      el.addEventListener("mouseleave", function () {
        el.style.transition = "transform .45s ease";
        el.style.transform = "";
        setTimeout(function () { el.style.transition = ""; }, 450);
      });
    });
  }

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
