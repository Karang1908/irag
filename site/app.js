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

  /* ---------- fullscreen scroll-flown 3D scene ----------
     The graph is the page's backdrop, not a panel beside the logo. Nodes
     live in world space in four clusters (api / auth / db / tests); a real
     camera with a position and a look-at target flies between waypoints as
     you scroll, so each section arrives somewhere specific in the graph.

     Real projection: world -> view basis -> perspective divide, painted
     back-to-front. Hand-rolled (~6KB) because a 3D library would dwarf the
     site's entire JS budget on a page whose pitch is zero dependencies. */
  var scene = document.getElementById("scene");
  if (scene && scene.getContext && !REDUCE &&
      !window.matchMedia("(max-width:760px)").matches) {
    var sx = scene.getContext("2d");

    // ---- world: four clusters, wired the way a real project depends ----
    var CL = [
      { c: [ 0.0,  1.35,  0.1], n: 7, hue: "api"  },
      { c: [-1.75, 0.05,  0.5], n: 8, hue: "auth" },
      { c: [ 0.15,-1.35, -0.3], n: 7, hue: "db"   },
      { c: [ 1.85, 0.75, -0.7], n: 6, hue: "test" }
    ];
    var NODES = [], LINKS = [];
    var seed = 7;
    var rnd = function () {            // deterministic: same scene each load
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed / 0x7fffffff;
    };
    CL.forEach(function (cl, ci) {
      var first = NODES.length;
      for (var i = 0; i < cl.n; i++) {
        NODES.push({
          p: [cl.c[0] + (rnd() - 0.5) * 1.15,
              cl.c[1] + (rnd() - 0.5) * 1.05,
              cl.c[2] + (rnd() - 0.5) * 1.15],
          r: i === 0 ? 6.2 : 2.4 + rnd() * 2.2,
          hub: i === 0, cl: ci
        });
      }
      // wire each cluster to its own hub
      for (var j = first + 1; j < NODES.length; j++) LINKS.push([first, j]);
      // a couple of intra-cluster edges for texture
      for (var k = 0; k < 3; k++) {
        var a = first + 1 + Math.floor(rnd() * (cl.n - 1));
        var b = first + 1 + Math.floor(rnd() * (cl.n - 1));
        if (a !== b) LINKS.push([a, b]);
      }
    });
    var hubs = NODES.map(function (n, i) { return n.hub ? i : -1; })
      .filter(function (i) { return i >= 0; });
    // api -> auth -> db, tests -> api: the dependency spine
    LINKS.push([hubs[0], hubs[1]], [hubs[1], hubs[2]], [hubs[0], hubs[2]],
               [hubs[3], hubs[0]]);

    // ---- camera waypoints: where each part of the page lands you ----
    // o = horizontal screen offset (fraction of width): the hero copy is
    // left-aligned, so the establishing shot is pushed right of it
    var WP = [
      { p: [0.1, 0.15, 4.3], t: [0, 0.05, 0], o:  0.20 }, // establishing
      { p: [-0.8, 1.5, 2.6], t: [0.0, 1.35, 0.1], o: 0.06 }, // into api
      { p: [-2.7, 0.2, 1.5], t: [-1.75, 0.05, 0.5], o: -0.04 }, // close on auth
      { p: [0.4, -1.3, 1.7], t: [0.15, -1.35, -0.3], o: 0.04 }, // dive to db
      { p: [2.5, 1.0, 2.2], t: [1.85, 0.75, -0.7], o: -0.06 }, // over to tests
      { p: [0.0, 0.2, 5.6], t: [0, 0, 0], o: 0 }           // pull back wide
    ];
    var cam = { p: WP[0].p.slice(), t: WP[0].t.slice(), o: WP[0].o };
    var camT = { p: WP[0].p.slice(), t: WP[0].t.slice(), o: WP[0].o };
    var spin = 0, prog = 0, rafS = null, liveS = false;
    var mx = 0, my = 0, mtx = 0, mty = 0;

    var sizeS = function () {
      var dpr = Math.min(2, window.devicePixelRatio || 1);
      scene.width = Math.round(innerWidth * dpr);
      scene.height = Math.round(innerHeight * dpr);
      sx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    sizeS();

    var sub = function (a, b) { return [a[0]-b[0], a[1]-b[1], a[2]-b[2]]; };
    var norm = function (v) {
      var l = Math.hypot(v[0], v[1], v[2]) || 1;
      return [v[0]/l, v[1]/l, v[2]/l];
    };
    var cross = function (a, b) {
      return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
    };
    var dot = function (a, b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; };
    var smooth = function (x) { return x * x * (3 - 2 * x); };

    // scroll 0..1 across the page -> a point along the waypoint path
    var camAt = function (q) {
      var f = q * (WP.length - 1);
      var i = Math.min(WP.length - 2, Math.floor(f));
      var k = smooth(Math.min(1, Math.max(0, f - i)));
      var A = WP[i], B = WP[i + 1], out = { p: [], t: [] };
      for (var d = 0; d < 3; d++) {
        out.p[d] = A.p[d] + (B.p[d] - A.p[d]) * k;
        out.t[d] = A.t[d] + (B.t[d] - A.t[d]) * k;
      }
      out.o = A.o + (B.o - A.o) * k;
      return out;
    };

    var drawS = function () {
      var W = innerWidth, H = innerHeight;
      sx.clearRect(0, 0, W, H);
      // orbit + cursor parallax applied around the look-at target
      var eye = cam.p.slice();
      var ox = eye[0] - cam.t[0], oz = eye[2] - cam.t[2];
      var ca = Math.cos(spin + mtx), sa = Math.sin(spin + mtx);
      eye[0] = cam.t[0] + ox * ca - oz * sa;
      eye[2] = cam.t[2] + ox * sa + oz * ca;
      eye[1] += mty;

      var fwd = norm(sub(cam.t, eye));
      var right = norm(cross(fwd, [0, 1, 0]));
      var up = cross(right, fwd);
      var focal = Math.min(W, H) * 0.86;
      var cx = W / 2 + W * cam.o, cy = H / 2;

      var pts = [];
      for (var i = 0; i < NODES.length; i++) {
        var v = sub(NODES[i].p, eye);
        var z = dot(v, fwd);
        if (z <= 0.06) { pts.push(null); continue; }   // behind the camera
        pts.push({
          x: cx + (dot(v, right) / z) * focal,
          y: cy - (dot(v, up) / z) * focal,
          z: z, r: NODES[i].r / z * 5.2, hub: NODES[i].hub
        });
      }

      // edges, faded by distance
      for (var e = 0; e < LINKS.length; e++) {
        var a = pts[LINKS[e][0]], b = pts[LINKS[e][1]];
        if (!a || !b) continue;
        var zz = (a.z + b.z) / 2;
        var al = Math.max(0, Math.min(0.5, 0.95 / zz));
        if (al < 0.010) continue;
        sx.beginPath(); sx.moveTo(a.x, a.y); sx.lineTo(b.x, b.y);
        sx.strokeStyle = "rgba(150,172,205," + al.toFixed(3) + ")";
        sx.lineWidth = Math.max(0.45, 2.0 / zz);
        sx.stroke();
      }

      // nodes back-to-front so near ones occlude far ones
      var order = [];
      for (var n = 0; n < pts.length; n++) if (pts[n]) order.push(n);
      order.sort(function (u, w) { return pts[w].z - pts[u].z; });
      order.forEach(function (idx) {
        var q = pts[idx];
        if (q.r < 0.25) return;
        var near = Math.max(0, Math.min(1, (6.0 - q.z) / 5.0));
        if (q.hub) {
          var g = sx.createRadialGradient(q.x, q.y, 0, q.x, q.y, q.r * 6);
          g.addColorStop(0, "rgba(88,166,255," + (0.26 * near).toFixed(3) + ")");
          g.addColorStop(1, "rgba(88,166,255,0)");
          sx.fillStyle = g;
          sx.beginPath(); sx.arc(q.x, q.y, q.r * 6, 0, 6.2832); sx.fill();
        }
        sx.beginPath(); sx.arc(q.x, q.y, q.r, 0, 6.2832);
        sx.fillStyle = q.hub
          ? "rgba(88,166,255," + (0.45 + 0.5 * near).toFixed(3) + ")"
          : "rgba(214,224,240," + (0.14 + 0.5 * near).toFixed(3) + ")";
        sx.fill();
      });
    };

    var tickS = function () {
      spin += 0.00055;
      var want = camAt(prog);
      for (var d = 0; d < 3; d++) {                 // ease toward the target
        camT.p[d] = want.p[d]; camT.t[d] = want.t[d];
        cam.p[d] += (camT.p[d] - cam.p[d]) * 0.055;
        cam.t[d] += (camT.t[d] - cam.t[d]) * 0.055;
      }
      cam.o += (want.o - cam.o) * 0.055;
      mtx += (mx - mtx) * 0.05;
      mty += (my - mty) * 0.05;
      drawS();
      rafS = requestAnimationFrame(tickS);
    };

    var readProg = function () {
      var max = document.documentElement.scrollHeight - innerHeight;
      prog = max > 0 ? Math.min(1, Math.max(0, scrollY / max)) : 0;
    };
    readProg();
    addEventListener("scroll", readProg, { passive: true });
    if (lenis) lenis.on("scroll", readProg);
    addEventListener("mousemove", function (e) {
      mx = ((e.clientX / innerWidth) - 0.5) * 0.34;
      my = ((e.clientY / innerHeight) - 0.5) * -0.5;
    }, { passive: true });
    var rsTO;
    addEventListener("resize", function () {
      clearTimeout(rsTO); rsTO = setTimeout(function () { sizeS(); drawS(); }, 160);
    });
    // pause when the tab is hidden — no point burning frames
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { liveS = false; if (rafS) cancelAnimationFrame(rafS); }
      else if (!liveS) { liveS = true; tickS(); }
    });
    liveS = true; tickS();
    document.documentElement.classList.add("has-scene");
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
