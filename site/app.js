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

  /* ---------- spatial UI: figures behave like planes in depth ----------
     The .tilt class existed in the markup but had no implementation. Each
     figure now rotates toward the cursor inside a perspective container
     and lifts on Z, so the screenshots read as physical surfaces rather
     than flat images. Tracking is a direct transform (per-frame, so a
     tween engine is the wrong tool); anime.js eases the release. */
  if (!REDUCE && window.matchMedia("(pointer:fine)").matches) {
    document.querySelectorAll(".tilt").forEach(function (el) {
      var rect = null, rafT = null, tx = 0, ty = 0;
      var apply = function () {
        el.style.transform = "perspective(1400px) rotateX(" + tx.toFixed(2) +
          "deg) rotateY(" + ty.toFixed(2) + "deg) translateZ(14px)";
        rafT = null;
      };
      el.addEventListener("mouseenter", function () {
        rect = el.getBoundingClientRect();
        el.style.transition = "transform .18s var(--mist)";
      });
      el.addEventListener("mousemove", function (e) {
        if (!rect) rect = el.getBoundingClientRect();
        ty = ((e.clientX - rect.left) / rect.width - 0.5) * 9;
        tx = -((e.clientY - rect.top) / rect.height - 0.5) * 7;
        if (!rafT) rafT = requestAnimationFrame(apply);
      }, { passive: true });
      el.addEventListener("mouseleave", function () {
        rect = null;
        el.style.transition = "";
        if (window.anime) {
          anime({ targets: { x: tx, y: ty }, x: 0, y: 0, duration: 620,
            easing: "easeOutQuint",
            update: function (an) {
              var o = an.animatables[0].target;
              el.style.transform = "perspective(1400px) rotateX(" +
                o.x.toFixed(2) + "deg) rotateY(" + o.y.toFixed(2) +
                "deg) translateZ(0px)";
            } });
        } else { el.style.transform = ""; }
      });
    });
  }

  /* ---------- 3D dependency graph (hero) ----------
     A real perspective projection, not a CSS fake: nodes live in a unit
     cube, rotate around Y, and are painted back-to-front with size,
     opacity and glow driven by depth. Hand-rolled in ~4KB because a 3D
     library would be 20x the site's entire JS budget on a page whose
     pitch is "zero dependencies". The cursor orbits the camera, so the
     hero reads as a space you're looking into rather than a picture. */
  var g3d = document.getElementById("g3d");
  if (g3d && g3d.getContext && !REDUCE) {
    var ctx3 = g3d.getContext("2d");
    // x, y, z in [-1,1]; r = base radius; hot = accent-coloured
    var N3 = [
      [ 0.00, -0.78,  0.05, 5.2, 1], [-0.52, -0.50, -0.42, 3.4, 0],
      [ 0.55, -0.44,  0.40, 3.6, 0], [-0.72, -0.02,  0.30, 3.3, 0],
      [ 0.00, -0.14, -0.10, 6.4, 1], [ 0.74, -0.06, -0.34, 3.5, 0],
      [-0.40,  0.34,  0.55, 3.2, 0], [ 0.38,  0.30, -0.58, 3.3, 0],
      [-0.86,  0.52, -0.18, 2.9, 0], [ 0.00,  0.52,  0.16, 4.6, 1],
      [ 0.84,  0.46,  0.28, 3.0, 0], [-0.20,  0.82, -0.40, 2.8, 0],
      [ 0.46,  0.80,  0.02, 2.9, 0], [-0.62, -0.72,  0.52, 2.7, 0]
    ];
    var E3 = [[0,1],[0,2],[0,4],[1,3],[2,5],[4,3],[4,5],[4,6],[4,7],[4,9],
              [3,8],[6,9],[7,9],[9,10],[9,11],[9,12],[8,11],[10,12],[1,13],
              [13,3],[2,10]];
    // camera state; anime.js tweens `cam` for entrance + settle, the rAF
    // loop keeps the slow orbit and eases toward the cursor
    var cam = { spin: 0, tiltX: 0, tiltY: 0, depth: 2.2, reveal: 0 };
    var tgtX = 0, tgtY = 0, raf3 = null, live3 = false, intro3 = false;

    var size3 = function () {
      var r = g3d.getBoundingClientRect();
      var dpr = Math.min(2, window.devicePixelRatio || 1);
      g3d.width = Math.max(1, r.width * dpr);
      g3d.height = Math.max(1, r.height * dpr);
      ctx3.setTransform(dpr, 0, 0, dpr, 0, 0);
      return r;
    };
    var box = size3();

    var draw3 = function () {
      var W = box.width, H = box.height;
      if (!W || !H) return;
      ctx3.clearRect(0, 0, W, H);
      var cx = W / 2, cy = H / 2;
      var scale = Math.min(W, H) * 0.46, focal = 3.0;
      var rotY = cam.spin + cam.tiltY, rotX = cam.tiltX;
      var cosY = Math.cos(rotY), sinY = Math.sin(rotY);
      var cosX = Math.cos(rotX), sinX = Math.sin(rotX);

      var pts = N3.map(function (n) {
        // rotate around Y then X
        var k = cam.reveal;                    // 0 = collapsed at origin
        var nx = n[0] * k, ny = n[1] * k, nz = n[2] * k;
        var x = nx * cosY - nz * sinY;
        var z = nx * sinY + nz * cosY;
        var y = ny * cosX - z * sinX;
        z = ny * sinX + z * cosX;
        var d = focal / (focal + z + (cam.depth - 2.2));  // perspective divide
        return { x: cx + x * scale * d, y: cy + y * scale * d,
                 d: d, z: z, r: n[3] * d, hot: n[4] };
      });

      // edges first, dimmed by their depth
      E3.forEach(function (e) {
        var a = pts[e[0]], b = pts[e[1]];
        var dep = (a.d + b.d) / 2;
        ctx3.beginPath();
        ctx3.moveTo(a.x, a.y); ctx3.lineTo(b.x, b.y);
        ctx3.strokeStyle = "rgba(150,170,200," +
          (0.05 + Math.pow(Math.max(0, dep - 0.55), 1.7) * 0.5).toFixed(3) + ")";
        ctx3.lineWidth = 0.5 + dep * 0.7;
        ctx3.stroke();
      });

      // nodes back-to-front so nearer ones occlude correctly
      pts.slice().sort(function (p, q) { return q.z - p.z; })
        .forEach(function (p) {
          var t = Math.max(0, Math.min(1, (p.d - 0.6) / 0.75));
          if (p.hot) {
            var g = ctx3.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.r * 5);
            g.addColorStop(0, "rgba(88,166,255," + (0.30 * t).toFixed(3) + ")");
            g.addColorStop(1, "rgba(88,166,255,0)");
            ctx3.fillStyle = g;
            ctx3.beginPath(); ctx3.arc(p.x, p.y, p.r * 5, 0, 6.2832); ctx3.fill();
          }
          ctx3.beginPath(); ctx3.arc(p.x, p.y, Math.max(0.6, p.r), 0, 6.2832);
          ctx3.fillStyle = p.hot
            ? "rgba(88,166,255," + (0.45 + 0.55 * t).toFixed(3) + ")"
            : "rgba(226,232,240," + (0.16 + 0.62 * t).toFixed(3) + ")";
          ctx3.fill();
        });
    };

    var tick3 = function () {
      cam.spin += 0.0022;                        // slow, constant orbit
      cam.tiltX += (tgtX - cam.tiltX) * 0.055;   // cursor eases the camera
      cam.tiltY += (tgtY - cam.tiltY) * 0.055;
      draw3();
      raf3 = requestAnimationFrame(tick3);
    };
    // anime.js choreographs the arrival: the graph unfolds out of the
    // origin and the camera dollies back, with a proper eased curve
    // rather than the linear lerp a hand-rolled loop gives you
    var intro = function () {
      if (intro3 || !window.anime) { cam.reveal = 1; return; }
      intro3 = true;
      anime({ targets: cam, reveal: [0, 1], duration: 1600,
        easing: "easeOutQuint" });
      anime({ targets: cam, depth: [3.6, 2.2], duration: 1900,
        easing: "easeOutQuart" });
      anime({ targets: cam, tiltX: [-0.5, 0], duration: 2000,
        easing: "easeOutQuart" });
    };
    var start3 = function () { if (!live3) { live3 = true; intro(); tick3(); } };
    var stop3 = function () {
      live3 = false;
      if (raf3) cancelAnimationFrame(raf3);
      raf3 = null;
    };

    // spatial: the scene tips toward the cursor, so it reads as depth
    window.addEventListener("mousemove", function (e) {
      tgtX = ((e.clientY / window.innerHeight) - 0.5) * 0.5;
      tgtY = ((e.clientX / window.innerWidth) - 0.5) * 0.6;
    }, { passive: true });

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (ents) {
        ents.forEach(function (en) { en.isIntersecting ? start3() : stop3(); });
      }, { rootMargin: "10% 0px 10% 0px" }).observe(g3d);
    } else { start3(); }
    var r3TO;
    window.addEventListener("resize", function () {
      clearTimeout(r3TO);
      r3TO = setTimeout(function () { box = size3(); draw3(); }, 160);
    });
    window.addEventListener("load", function () { box = size3(); draw3(); });
  }

  /* ---------- scroll-scrubbed "files become a graph" showcase ----------
     Real file paths start stacked as a plain listing, then fly into graph
     positions and wire themselves together by their actual imports. The
     headline claim is *shown*, not asserted: an abstract dot field said
     nothing about files. A paused anime.js timeline is seeked by the
     section's scroll progress (anime.js-landing style). */
  var scNodes = document.getElementById("sc-nodes");
  var scEdges = document.getElementById("sc-edges");
  var showcase = document.getElementById("showcase");
  if (scNodes && scEdges && showcase && window.anime) {
    var narrow = window.innerWidth < 820;
    // a real (small) dependency graph: these imports are the ones this
    // shape of project actually has
    var FILES = [
      { f: "tests/test_auth.py",    x: 14, y: 15 },
      { f: "src/api/routes.py",     x: 45, y: 20 },
      { f: "src/api/errors.py",     x: 78, y: 24 },
      { f: "src/auth/session.py",   x: 25, y: 50 },
      { f: "src/auth/password.py",  x: 59, y: 50 },
      { f: "src/db/store.py",       x: 40, y: 80 },
      { f: "src/db/models.py",      x: 73, y: 78 }
    ];
    var EDGES = [[0, 1], [1, 3], [1, 4], [1, 2], [3, 5], [4, 5], [5, 6]];
    // if store.py changes, everything upstream of it is the blast radius
    var HOT = 5, HOT_UP = [3, 4, 1, 0];

    // outer holds the final graph position (and the -50% centering);
    // the inner chip is what anime animates, so its transform can be
    // rewritten freely without destroying the centering
    var nodeEls = [], chipEls = [];
    FILES.forEach(function (d) {
      var wrap = document.createElement("div");
      wrap.className = "sc-node";
      // on a phone a full path is wider than the stage, so show the file
      // name only and pull the layout in from the edges
      wrap.style.left = (narrow ? 50 + (d.x - 50) * 0.62 : d.x) + "%";
      wrap.style.top = d.y + "%";
      var chip = document.createElement("div");
      chip.className = "sc-chip";
      chip.textContent = narrow ? d.f.split("/").pop() : d.f;
      wrap.appendChild(chip);
      scNodes.appendChild(wrap);
      nodeEls.push(wrap); chipEls.push(chip);
    });

    var edgeEls = [], offs = [];
    var buildEdges = function () {
      var box = scEdges.getBoundingClientRect();
      if (!box.width) return;
      scEdges.setAttribute("viewBox", "0 0 " + box.width + " " + box.height);
      while (scEdges.firstChild) scEdges.removeChild(scEdges.firstChild);
      edgeEls = [];
      EDGES.forEach(function (e) {
        var a = nodeEls[e[0]], b = nodeEls[e[1]];
        if (!a || !b) return;
        var ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
        var pth = document.createElementNS("http://www.w3.org/2000/svg", "path");
        pth.setAttribute("class", "sc-edge");
        pth.setAttribute("d",
          "M" + (ra.left + ra.width / 2 - box.left) + " " +
                (ra.top + ra.height / 2 - box.top) +
          "L" + (rb.left + rb.width / 2 - box.left) + " " +
                (rb.top + rb.height / 2 - box.top));
        scEdges.appendChild(pth); edgeEls.push(pth);
      });
    };

    // where each node sits while the stage is still "just a list"
    var measureList = function () {
      var box = scNodes.getBoundingClientRect();
      offs = nodeEls.map(function (el, i) {
        var w = chipEls[i].offsetWidth, h = chipEls[i].offsetHeight;
        var finalX = (FILES[i].x / 100) * box.width;
        var finalY = (FILES[i].y / 100) * box.height;
        var gap = Math.min(h + 14, box.height * 0.115);
        var listY = box.height / 2 + (i - (FILES.length - 1) / 2) * gap;
        var listX = box.width * (narrow ? 0.30 : 0.34) + w / 2;
        return { dx: listX - finalX, dy: listY - finalY };
      });
    };

    var tl = null;
    var build = function () {
      measureList();
      buildEdges();
      tl = anime.timeline({ autoplay: false, easing: "easeOutQuart" });
      // 1. the listing types itself in, top to bottom
      tl.add({
        targets: chipEls,
        opacity: [0, 1],
        translateX: function (el, i) { return [offs[i].dx, offs[i].dx]; },
        translateY: function (el, i) { return [offs[i].dy + 10, offs[i].dy]; },
        duration: 420,
        delay: anime.stagger(70)
      }, 0);
      // 2. files fly out into their places in the graph
      tl.add({
        targets: chipEls,
        translateX: function (el, i) { return [offs[i].dx, 0]; },
        translateY: function (el, i) { return [offs[i].dy, 0]; },
        duration: 900,
        easing: "easeOutQuint",
        delay: anime.stagger(55)
      }, 1150);
      // 3. imports resolve into edges
      if (edgeEls.length) {
        tl.add({
          targets: edgeEls,
          opacity: [0, 1],
          strokeDashoffset: [anime.setDashoffset, 0],
          duration: 620,
          easing: "easeInOutSine",
          delay: anime.stagger(70)
        }, 1900);
      }
      // 4. blast radius: the changed file, then everything that depends on it
      tl.add({ targets: chipEls[HOT], duration: 380,
        easing: "easeOutQuart" }, 2700)
        .add({ targets: HOT_UP.map(function (i) { return chipEls[i]; }),
          duration: 460, delay: anime.stagger(90),
          easing: "easeOutQuart" }, 2820);
      // the highlight itself is a class, so it survives seeking backwards
      tl.add({ targets: {}, duration: 1 }, 3400);
    };
    build();

    var caps = [
      ["Your files.",
       "A list. Every new session, your agent opens them one by one to work out what they already do."],
      ["Become a graph.",
       "irag parses every import into an edge \u2014 deterministic, always current, and it costs no tokens."],
      ["So you can ask what breaks.",
       "Change one file and every module that breaks is a query away, not a guess."]
    ];
    var capT = document.getElementById("sc-cap-t");
    var capP = document.getElementById("sc-cap-p");
    var cur = -1, capTO = null;
    var setCap = function (i) {
      if (i === cur) return;
      cur = i;
      // the blast-radius highlight belongs to the last beat only
      var hot = i === 2;
      if (nodeEls[HOT]) nodeEls[HOT].classList.toggle("hot", hot);
      HOT_UP.forEach(function (n) {
        if (nodeEls[n]) nodeEls[n].classList.toggle("dep", hot);
      });
      scEdges.classList.toggle("lit", hot);
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
      // land on the finished graph, highlight included — the last beat is
      // the point of the section, not a flourish layered on top of it
      if (tl) tl.seek(tl.duration);
      cur = 2;
      if (nodeEls[HOT]) nodeEls[HOT].classList.add("hot");
      HOT_UP.forEach(function (n) {
        if (nodeEls[n]) nodeEls[n].classList.add("dep");
      });
      scEdges.classList.add("lit");
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
