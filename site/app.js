/* irag docs — search, copy buttons, TOC highlight, landing animations.
   No dependencies. */
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  var REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- Lenis momentum scrolling — the core Mistral "feel" ------- */
  var lenis = null;
  if (window.Lenis && !REDUCE) {
    lenis = new Lenis({ lerp: 0.085, wheelMultiplier: 1, smoothWheel: true });
    var rafLoop = function (t) { lenis.raf(t); requestAnimationFrame(rafLoop); };
    requestAnimationFrame(rafLoop);
    // in-page anchors glide through Lenis instead of jumping
    document.addEventListener("click", function (ev) {
      var a = ev.target.closest && ev.target.closest('a[href^="#"]');
      if (!a) return;
      var href = a.getAttribute("href");
      if (href.length < 2) return;
      var target = document.querySelector(href);
      if (target) { ev.preventDefault(); lenis.scrollTo(target, { offset: -78 }); }
    });
  }

  /* ---------- scroll reveal (fade/blur-up) + clip-wipe titles ---------- */
  // section titles get a bottom-up clip-wipe; everything .reveal fades up
  document.querySelectorAll(".sec-t").forEach(function (el) {
    el.classList.add("wipe");
  });
  var pending = [].slice.call(document.querySelectorAll(".reveal, .wipe"));
  if (REDUCE) {
    pending.forEach(function (el) { el.classList.add("in"); });
  } else if (pending.length) {
    // Position-based, NOT IntersectionObserver: a fast flick or Lenis
    // momentum can carry a section past the viewport between IO samples,
    // leaving it permanently blank. Checking rect.top on each scroll frame
    // reveals anything we've reached, at any scroll speed.
    var revealCheck = function () {
      var vh = window.innerHeight, still = [];
      for (var i = 0; i < pending.length; i++) {
        if (pending[i].getBoundingClientRect().top < vh * 0.88) {
          pending[i].classList.add("in");
        } else {
          still.push(pending[i]);
        }
      }
      pending = still;
    };
    revealCheck();
    window.addEventListener("scroll", revealCheck, { passive: true });
    window.addEventListener("resize", revealCheck, { passive: true });
    window.addEventListener("load", revealCheck);
    if (lenis) lenis.on("scroll", revealCheck);
  }

  /* ---------- pixel dissolve — the signature Mistral reveal ----------
     cover the element with a canvas of background blocks, then clear them
     in an ordered-dither sequence so the image resolves out of pixels. */
  if (!REDUCE && "IntersectionObserver" in window) {
    var dissolve = function (el) {
      var W = el.clientWidth, H = el.clientHeight;
      if (!W || !H) return;
      var dpr = Math.min(2, window.devicePixelRatio || 1);
      var cv = document.createElement("canvas");
      cv.className = "px-canvas";
      cv.width = Math.round(W * dpr);
      cv.height = Math.round(H * dpr);
      var ctx = cv.getContext("2d");
      ctx.scale(dpr, dpr);
      ctx.fillStyle = "#08090c";
      ctx.fillRect(0, 0, W, H);
      el.appendChild(cv);
      var P = 15, cols = Math.ceil(W / P), rows = Math.ceil(H / P);
      var blocks = [];
      for (var y = 0; y < rows; y++) {
        for (var x = 0; x < cols; x++) {
          // ordered (Bayer-ish) weight + slight jitter = a clean dither
          blocks.push({ x: x, y: y,
            d: ((x & 3) * 4 + (y & 3)) / 16 + Math.random() * 0.18 });
        }
      }
      blocks.sort(function (a, b) { return a.d - b.d; });
      var i = 0, total = blocks.length, DUR = 780, t0 = null;
      var step = function (t) {
        if (t0 === null) t0 = t;
        var p = Math.min(1, (t - t0) / DUR);
        var target = Math.floor((1 - Math.pow(1 - p, 3)) * total);
        for (; i < target; i++) {
          var b = blocks[i];
          ctx.clearRect(b.x * P, b.y * P, P + 1, P + 1);
        }
        if (p < 1) requestAnimationFrame(step);
        else cv.remove();
      };
      requestAnimationFrame(step);
    };
    document.querySelectorAll(".px").forEach(function (el) {
      new IntersectionObserver(function (ents, obs) {
        ents.forEach(function (en) {
          if (!en.isIntersecting) return;
          obs.disconnect();
          var img = el.querySelector("img");
          if (img && !img.complete) {
            img.addEventListener("load", function () { dissolve(el); },
              { once: true });
            img.addEventListener("error", function () { dissolve(el); },
              { once: true });
          } else {
            dissolve(el);
          }
        });
      }, { threshold: 0.35 }).observe(el);
    });
  }

  /* ---------- scroll-scrubbed anime.js graph showcase ----------
     A grid of dots (anime stagger-grid) ripples in, a wave sweeps across,
     then a subset lights up and edges draw between them — the grid
     coalescing into irag's dependency graph. A *paused* anime.js timeline
     is seeked by the section's scroll progress (anime.js-landing style). */
  var scGrid = document.getElementById("sc-grid");
  var scEdges = document.getElementById("sc-edges");
  var showcase = document.getElementById("showcase");
  if (scGrid && scEdges && showcase && window.anime) {
    var narrow = window.innerWidth < 820;
    var COLS = narrow ? 16 : 24, ROWS = narrow ? 9 : 12;
    scGrid.style.gridTemplateColumns = "repeat(" + COLS + ",1fr)";
    scGrid.style.gridTemplateRows = "repeat(" + ROWS + ",1fr)";
    var dots = [];
    for (var gi = 0; gi < COLS * ROWS; gi++) {
      var cell = document.createElement("div"); cell.className = "sc-cell";
      var dot = document.createElement("div"); dot.className = "sc-dot";
      cell.appendChild(dot); scGrid.appendChild(cell); dots.push(dot);
    }
    var at = function (c, r) { return dots[r * COLS + c]; };
    var NP = narrow
      ? [[3, 2], [8, 1], [12, 3], [5, 6], [10, 7], [13, 5], [2, 7], [7, 4]]
      : [[4, 3], [9, 2], [14, 4], [19, 3], [6, 8], [12, 9], [17, 7], [2, 10],
         [21, 9], [11, 6]];
    var EP = narrow
      ? [[0, 1], [1, 2], [0, 3], [3, 4], [2, 5], [4, 5], [3, 6], [1, 7], [4, 7]]
      : [[0, 1], [1, 2], [2, 3], [0, 4], [1, 9], [4, 5], [5, 6], [2, 6], [3, 6],
         [4, 7], [5, 9], [6, 8], [9, 1], [8, 4]];
    var nodeDots = NP.map(function (n) { return at(n[0], n[1]); })
      .filter(Boolean);

    var edgeEls = [];
    var buildEdges = function () {
      var box = scEdges.getBoundingClientRect();
      if (!box.width) return;
      scEdges.setAttribute("viewBox", "0 0 " + box.width + " " + box.height);
      while (scEdges.firstChild) scEdges.removeChild(scEdges.firstChild);
      edgeEls = [];
      EP.forEach(function (e) {
        var a = nodeDots[e[0]], b = nodeDots[e[1]];
        if (!a || !b) return;
        var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        var p = document.createElementNS("http://www.w3.org/2000/svg", "path");
        p.setAttribute("class", "sc-edge");
        p.setAttribute("d", "M" + (ra.left + ra.width / 2 - box.left) + " " +
          (ra.top + ra.height / 2 - box.top) + " L" +
          (rb.left + rb.width / 2 - box.left) + " " +
          (rb.top + rb.height / 2 - box.top));
        scEdges.appendChild(p); edgeEls.push(p);
      });
    };

    var tl = null;
    var build = function () {
      buildEdges();
      tl = anime.timeline({ autoplay: false, easing: "easeOutQuad" });
      tl.add({ targets: dots, scale: [0, 1], opacity: [0, 0.42],
        duration: 600,
        delay: anime.stagger(7, { grid: [COLS, ROWS], from: "center" }) }, 0);
      tl.add({ targets: dots,
        scale: [{ value: 1.85, duration: 320 }, { value: 1, duration: 380 }],
        delay: anime.stagger(7, { grid: [COLS, ROWS], from: "first" }) }, 720);
      tl.add({ targets: nodeDots, scale: 2.7, opacity: 1,
        backgroundColor: "#58a6ff", duration: 500,
        delay: anime.stagger(45) }, 1450);
      if (edgeEls.length) {
        tl.add({ targets: edgeEls, opacity: [0, 1],
          strokeDashoffset: [anime.setDashoffset, 0], duration: 700,
          easing: "easeInOutSine", delay: anime.stagger(50) }, 1520);
      }
      tl.add({ targets: nodeDots,
        scale: [{ value: 3.1, duration: 320 }, { value: 2.7, duration: 420 }],
        easing: "easeInOutSine" }, 2250);
    };
    build();

    var caps = [
      ["Files become a graph.",
       "A node for every file, an edge for every import. One map your agent queries instead of re-reading the tree."],
      ["Imports become edges.",
       "irag parses the dependencies and wires the nodes deterministically, with no tokens and always current."],
      ["One graph, queryable.",
       "Structure, neighbors, blast radius: all read from SQL in an instant."]
    ];
    var capT = document.getElementById("sc-cap-t");
    var capP = document.getElementById("sc-cap-p");
    var cur = -1, capTO = null;
    var setCap = function (i) {
      if (i === cur) return;
      cur = i;
      if (capT) capT.style.opacity = "0";
      if (capP) capP.style.opacity = "0";
      clearTimeout(capTO);
      capTO = setTimeout(function () {
        if (capT) { capT.textContent = caps[i][0]; capT.style.opacity = "1"; }
        if (capP) { capP.textContent = caps[i][1]; capP.style.opacity = "1"; }
      }, 150);
    };
    var progAt = function () {
      var total = showcase.offsetHeight - window.innerHeight;
      return total > 0 ? Math.min(1, Math.max(0,
        -showcase.getBoundingClientRect().top / total)) : 0;
    };

    if (REDUCE) {
      if (tl) tl.seek(tl.duration);
      cur = 2;
      if (capT) capT.textContent = caps[2][0];
      if (capP) capP.textContent = caps[2][1];
    } else {
      // Seamless scrub: a continuous rAF loop lerps the timeline's playhead
      // toward the scroll-derived target instead of snapping to it on each
      // scroll event. Decoupling from scroll-event cadence + easing the
      // catch-up is what removes the stutter (GSAP-scrub style). The loop
      // only runs while the section is near the viewport.
      var curT = 0, lastSeek = -1, raf = null, active = false;
      var loop = function () {
        var target = progAt() * tl.duration;
        curT += (target - curT) * 0.11;         // the smoothing
        if (Math.abs(target - curT) < 0.5) curT = target;
        if (Math.abs(curT - lastSeek) > 0.25) { // skip redundant seeks
          tl.seek(curT);
          lastSeek = curT;
        }
        setCap(curT / tl.duration >= 0.8 ? 2
          : curT / tl.duration >= 0.5 ? 1 : 0);
        raf = requestAnimationFrame(loop);
      };
      var start = function () { if (!active) { active = true; loop(); } };
      var stop = function () {
        active = false;
        if (raf) cancelAnimationFrame(raf);
        raf = null;
      };
      if ("IntersectionObserver" in window) {
        new IntersectionObserver(function (ents) {
          ents.forEach(function (en) {
            if (en.isIntersecting) start(); else stop();
          });
        }, { rootMargin: "50% 0px 50% 0px" }).observe(showcase);
      } else {
        start();
      }
      window.addEventListener("load", function () { build(); lastSeek = -1; });
      var scRTO;
      window.addEventListener("resize", function () {
        clearTimeout(scRTO);
        scRTO = setTimeout(function () { build(); lastSeek = -1; }, 200);
      });
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
        .add({ targets: gn, scale: 1, easing: "easeOutQuint",
          duration: 520, delay: anime.stagger(40) }, 750)
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

  /* ---------- scroll spine (landing) + parallax + counters ---------- */
  var spine = document.querySelector(".spine");
  var landingMain = document.querySelector("main.landing");
  if (spine && landingMain) {
    var track = spine.querySelector(".sp-track");
    var fill = spine.querySelector(".sp-fill");
    var svg = spine.querySelector("svg");
    var marks = landingMain.querySelectorAll(".sec, .quote, .closing");
    var nodes = [];
    var spTop = 0, spH = 1;

    marks.forEach(function () {
      var c = document.createElementNS("http://www.w3.org/2000/svg",
        "circle");
      c.setAttribute("class", "sp-node");
      c.setAttribute("cx", "7");
      c.setAttribute("r", "4.5");
      svg.appendChild(c);
      nodes.push(c);
    });
    var layoutSpine = function () {
      var stats = landingMain.querySelector(".stats");
      var closing = landingMain.querySelector(".closing");
      if (!stats || !closing) return;
      spTop = stats.offsetTop;
      spH = closing.offsetTop + closing.offsetHeight - spTop;
      spine.style.top = spTop + "px";
      spine.style.height = spH + "px";
      ["x1", "x2"].forEach(function (a) {
        track.setAttribute(a, "7");
        fill.setAttribute(a, "7");
      });
      track.setAttribute("y1", "0");
      track.setAttribute("y2", spH);
      fill.setAttribute("y1", "0");
      marks.forEach(function (m, i) {
        nodes[i].setAttribute("cy",
          Math.max(10, m.offsetTop - spTop + 112));
      });
    };
    var ticking = false;
    var onScroll = function () {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(function () {
        ticking = false;
        var read = window.scrollY + window.innerHeight * 0.4 -
          (landingMain.offsetTop + spTop);
        var p = Math.max(0, Math.min(1, read / spH));
        fill.setAttribute("y2", p * spH);
        nodes.forEach(function (c) {
          c.classList.toggle("on", +c.getAttribute("cy") <= p * spH);
        });
        var hg = document.querySelector(".hero-graph");
        if (hg && !REDUCE && window.innerWidth > 1000) {
          hg.style.transform = "translateY(" +
            Math.max(-44, -window.scrollY * 0.06).toFixed(1) + "px)";
        }
      });
    };
    layoutSpine();
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", function () {
      layoutSpine();
      onScroll();
    });
    /* re-measure once images/fonts have settled the layout */
    window.addEventListener("load", function () {
      layoutSpine();
      onScroll();
    });
  }

  /* stat count-up when the strip scrolls in */
  var statNums = document.querySelectorAll(".stat b[data-cnt]");
  if (statNums.length && window.anime && !REDUCE &&
      "IntersectionObserver" in window) {
    var counted = false;
    new IntersectionObserver(function (entries, obs) {
      if (counted || !entries.some(function (e) { return e.isIntersecting; }))
        return;
      counted = true;
      obs.disconnect();
      statNums.forEach(function (el) {
        var end = +el.dataset.cnt;
        var pre = el.dataset.pre || "";
        var suf = el.dataset.suf || "";
        var o = { v: 0 };
        anime({
          targets: o, v: end, round: 1, duration: 1100,
          easing: "easeOutExpo",
          update: function () { el.textContent = pre + o.v + suf; }
        });
      });
    }, { threshold: 0.4 }).observe(statNums[0]);
  }

  /* ---------- docs reading progress bar ---------- */
  if (document.querySelector(".doc") && !REDUCE) {
    var bar = document.createElement("div");
    bar.className = "readbar";
    document.body.appendChild(bar);
    var barTick = false;
    var onRead = function () {
      if (barTick) return;
      barTick = true;
      requestAnimationFrame(function () {
        barTick = false;
        var h = document.documentElement;
        var max = h.scrollHeight - h.clientHeight;
        bar.style.transform = "scaleX(" +
          (max > 0 ? window.scrollY / max : 0).toFixed(4) + ")";
      });
    };
    onRead();
    window.addEventListener("scroll", onRead, { passive: true });
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
