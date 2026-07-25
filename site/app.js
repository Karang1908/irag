/* irag docs — search, copy buttons, TOC highlight, landing animations.
   No dependencies. */
(function () {
  "use strict";

  document.documentElement.classList.add("js");
  var REDUCE = matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- launch transition: doc links warp through the graph ---- */
  document.addEventListener("click", function (ev) {
    var a2 = ev.target.closest && ev.target.closest('a[href]');
    if (!a2 || ev.metaKey || ev.ctrlKey || ev.shiftKey || a2.target) return;
    var href = a2.getAttribute("href") || "";
    // only same-site doc destinations, and only when the scene is running
    if (!/(^|\/)docs\//.test(href) || !window.__warp) return;
    ev.preventDefault();
    window.__warp(href);
  }, true);

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
    // ---- stops: each section owns a node. The camera is driven by which
    // section is centred, so it *arrives* somewhere and settles instead of
    // drifting past, and the copy keys off that arrival. ----
    // `anchor` is where this section parks the rail on screen (fractions of
    // the viewport). It moves section to section — right, top-right, left —
    // so the rail flies around the page instead of sitting in a fixed strip.
    // The canvas is behind the copy, so a marker can never cover text.
    var STOPS = [
      { sel: ".hero",  n: hubs[0], off: [0.1, 0.15, 4.3],  o:  0.30, oy: 0,
        anchor: [0.945, 0.42], label: "Overview" },
      { sel: "#why",   n: hubs[1], off: [-1.0, 0.35, 1.9], o: -0.34, oy: 0,
        anchor: [0.955, 0.20], label: "Why" },
      { sel: "#how",   n: hubs[0], off: [0.55, 0.55, 1.85], o: 0.36, oy: 0,
        anchor: [0.048, 0.38], label: "How it runs" },
      { sel: "#built", n: hubs[2], off: [0.5, -0.55, 1.75], o: 0.30, oy: -0.30,
        anchor: [0.950, 0.70], label: "Architecture" },
      { sel: "#proof", n: hubs[3], off: [0.85, 0.5, 1.8],  o: 0.34, oy: 0,
        anchor: [0.045, 0.22], label: "Measured" },
      { sel: "#dash",  n: hubs[2], off: [-0.9, -0.35, 2.4], o: -0.33, oy: -0.28,
        anchor: [0.952, 0.46], label: "Dashboard" }
    ].map(function (st) {
      st.el = document.querySelector(st.sel);
      if (st.el) st.el.classList.add('stop');
      st.t = NODES[st.n].p.slice();
      st.p = [st.t[0] + st.off[0], st.t[1] + st.off[1], st.t[2] + st.off[2]];
      st.sx = 0; st.sy = 0; st.sr = 4;        // live screen position
      return st;
    }).filter(function (st) { return st.el; });

    // On a docs page none of those sections exist. Same node field, same
    // colours, but a slow fixed orbit — the page is for reading, so the
    // backdrop must not compete with it.
    var AMBIENT = STOPS.length === 0;
    if (AMBIENT) {
      STOPS = [{ el: document.body, c: 1, w: 1, n: hubs[0],
                 p: [0.2, 0.3, 5.2], t: [0, 0, 0], o: 0, oy: 0,
                 anchor: [1.4, 0.5], label: "" }];
    }

    // live camera + the target it eases toward (weigh() writes camT)
    var cam  = { p: STOPS[0].p.slice(), t: STOPS[0].t.slice(), o: STOPS[0].o, oy: STOPS[0].oy };
    var camT = { p: STOPS[0].p.slice(), t: STOPS[0].t.slice(), o: STOPS[0].o, oy: STOPS[0].oy };

    var spin = 0, rafS = null, liveS = false, active = -1;
    var collapse = 0, rush = 0, warping = false, arrivedness = 1;
    var railX = 0, railY = 0;
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

    // How centred is each stop? 1 = dead centre (arrived), 0 = far away.
    // Blending by these weights makes the camera settle on a node while
    // its section is being read, and travel while you move between them.
    var weigh = function () {
      var vh = innerHeight, best = -1, bw = 0, sum = 0;
      for (var i = 0; i < STOPS.length; i++) {
        var r = STOPS[i].el.getBoundingClientRect();
        var d = Math.abs((r.top + r.height / 2) - vh / 2) / (vh * 0.95);
        var w = Math.max(0, 1 - d);
        w = w * w * (3 - 2 * w);                 // smoothstep: flatter tops
        // raw centrality drives the copy (so it really does fade between
        // stops); the normalised copy drives the camera blend
        STOPS[i].c = w;
        STOPS[i].w = w; sum += w;
        if (w > bw) { bw = w; best = i; }
      }
      if (sum > 0) {
        for (var j = 0; j < STOPS.length; j++) {
          STOPS[j].w /= sum;
          var el = STOPS[j].el;
          // the copy resolves as the camera arrives; never gated on a
          // one-shot transition, so it can't get stuck hidden
          var a = Math.min(1, STOPS[j].c * 1.55);
          el.style.setProperty("--at", a.toFixed(3));
        }
        var want = { p: [0, 0, 0], t: [0, 0, 0], o: 0, oy: 0 };
        for (var k = 0; k < STOPS.length; k++) {
          var st = STOPS[k];
          for (var d2 = 0; d2 < 3; d2++) {
            want.p[d2] += st.p[d2] * st.w;
            want.t[d2] += st.t[d2] * st.w;
          }
          want.o += st.o * st.w;
          want.oy += (st.oy || 0) * st.w;
        }
        camT.p = want.p; camT.t = want.t; camT.o = want.o; camT.oy = want.oy;
      }
      // how "arrived" are we? 1 at a stop, ~0 mid-flight
      arrivedness = best >= 0 ? STOPS[best].c : 0;
      if (best !== active) { active = best; paintRail(); }
    };

    var drawS = function () {
      var W = innerWidth, H = innerHeight;
      sx.clearRect(0, 0, W, H);
      // orbit + cursor parallax applied around the look-at target
      var eye = cam.p.slice();
      // in transit the camera lifts away from its target and swoops back
      // in as the section arrives — the difference between panning past a
      // graph and flying into part of one
      var lift = AMBIENT ? 0 : (1 - Math.min(1, arrivedness * 1.35));
      var ox = eye[0] - cam.t[0], oz = eye[2] - cam.t[2];
      var ca = Math.cos(spin + mtx), sa = Math.sin(spin + mtx);
      eye[0] = cam.t[0] + ox * ca - oz * sa;
      eye[2] = cam.t[2] + ox * sa + oz * ca;
      eye[1] += mty + lift * 0.55;
      var back = 1 + lift * 0.85;                 // dolly out mid-flight
      eye[0] = cam.t[0] + (eye[0] - cam.t[0]) * back;
      eye[1] = cam.t[1] + (eye[1] - cam.t[1]) * back;
      eye[2] = cam.t[2] + (eye[2] - cam.t[2]) * back;

      var fwd = norm(sub(cam.t, eye));
      var right = norm(cross(fwd, [0, 1, 0]));
      var up = cross(right, fwd);
      var focal = Math.min(W, H) * 0.86;
      var cx = W / 2 + W * cam.o * (1 - collapse),
          cy = H / 2 + H * (cam.oy || 0) * (1 - collapse);

      var pts = [];
      // during the warp every node eases toward the origin, then the
      // camera is thrown through that point
      var gather = collapse, thrust = rush * 3.4;
      for (var i = 0; i < NODES.length; i++) {
        var wp = NODES[i].p;
        var np = gather > 0
          ? [wp[0] * (1 - gather), wp[1] * (1 - gather), wp[2] * (1 - gather)]
          : wp;
        var v = sub(np, eye);
        if (thrust) { v[0] -= fwd[0] * thrust; v[1] -= fwd[1] * thrust;
                      v[2] -= fwd[2] * thrust; }
        var z = dot(v, fwd);
        if (z <= 0.06) { pts.push(null); continue; }   // behind the camera
        pts.push({
          x: cx + (dot(v, right) / z) * focal,
          y: cy - (dot(v, up) / z) * focal,
          z: z, hub: NODES[i].hub,
          // nodes swell as they merge, so the point they gather into
          // reads as one big body rather than a speck
          r: NODES[i].r / z * 5.2 * (1 + gather * 3.4)
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

      // ---- the flying rail ----
      // Each stop marker sits docked in a small cluster while you are
      // elsewhere, and flies out to its true position in the graph as you
      // arrive at it. The cluster's anchor is blended from the stops'
      // weights, so the whole rail drifts to a new corner per section.
      var ax = 0, ay = 0, aw = 0;
      for (var si = 0; si < STOPS.length; si++) {
        aw += STOPS[si].w;
        ax += STOPS[si].anchor[0] * STOPS[si].w;
        ay += STOPS[si].anchor[1] * STOPS[si].w;
      }
      if (aw > 0.0001) { railX = ax * W; railY = ay * H; }
      var slot = Math.min(34, H * 0.046);
      var focus = (AMBIENT || active < 0 || !STOPS[active]) ? -1 : STOPS[active].n;
      var arrived = active >= 0 && STOPS[active] ? STOPS[active].c * 1.55 : 0;

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
        if (idx === focus && arrived > 0.05) {
          var af = Math.min(1, arrived);
          sx.beginPath();
          sx.arc(q.x, q.y, q.r + 11 + (1 - af) * 26, 0, 6.2832);
          sx.strokeStyle = "rgba(88,166,255," + (0.55 * af).toFixed(3) + ")";
          sx.lineWidth = 1.1; sx.stroke();
          // the name of the node you've arrived at: this is a headline in
          // the scene, not a footnote — it has to hold its own next to
          // 16px body copy, so it is set large, bright, and haloed so it
          // stays legible over edges and glow
          sx.save();
          sx.font = "600 21px ui-monospace,SFMono-Regular,Menlo,monospace";
          if ("letterSpacing" in sx) sx.letterSpacing = "0.06em";
          // place it away from the centre column, but flip sides rather
          // than let a long name ("Architecture") run off the viewport
          var lft = q.x > W / 2;
          var tw = sx.measureText(STOPS[active].label).width;
          if (lft && q.x + q.r + 22 + tw > W - 18) lft = false;
          else if (!lft && q.x - q.r - 22 - tw < 18) lft = true;
          sx.textAlign = lft ? "left" : "right";
          var lx = q.x + (lft ? q.r + 22 : -(q.r + 22)), ly = q.y + 7;
          sx.shadowColor = "rgba(4,6,10,.95)";
          sx.shadowBlur = 14;
          sx.fillStyle = "rgba(12,16,24," + (0.9 * af).toFixed(3) + ")";
          sx.fillText(STOPS[active].label, lx, ly);   // halo pass
          sx.shadowBlur = 0;
          sx.fillStyle = "rgba(238,245,255," + (0.97 * af).toFixed(3) + ")";
          sx.fillText(STOPS[active].label, lx, ly);
          sx.restore();
          sx.textAlign = "left";
        }
      });

      // ---- the zoom: the compressed node opens into the next page ----
      if (rush > 0) {
        var o0 = sub([0, 0, 0], eye);
        var oz = dot(o0, fwd);
        var px = oz > 0.05 ? cx + (dot(o0, right) / oz) * focal : cx;
        var py = oz > 0.05 ? cy - (dot(o0, up) / oz) * focal : cy;
        var maxR = Math.hypot(Math.max(px, W - px), Math.max(py, H - py));
        var rr = 4 + Math.pow(rush, 1.9) * maxR * 1.08;
        var gg = sx.createRadialGradient(px, py, 0, px, py, Math.max(rr, 1));
        gg.addColorStop(0, "rgba(240,248,255,.99)");
        gg.addColorStop(0.35, "rgba(150,200,255,.97)");
        gg.addColorStop(0.72, "rgba(46,110,205,.94)");
        gg.addColorStop(1, "rgba(8,9,12,.97)");
        sx.beginPath(); sx.arc(px, py, Math.max(rr, 1), 0, 6.2832);
        sx.fillStyle = gg; sx.fill();
      }
      if (AMBIENT) return;   // no rail on a docs page
      // ---- rail markers: docked when away, flown out when arrived ----
      var mid = (STOPS.length - 1) / 2;
      for (var r2 = 0; r2 < STOPS.length; r2++) {
        var st2 = STOPS[r2];
        var dock = 1 - Math.min(1, st2.c * 1.5);      // 0 = fully arrived
        var tp = pts[st2.n];
        var dx2 = railX, dy2 = railY + (r2 - mid) * slot;
        // when the node is off-screen/behind, stay docked
        var tx2 = tp ? tp.x : dx2, tyy = tp ? tp.y : dy2;
        var trr = tp ? tp.r : 4;
        st2.sx = dx2 + (tx2 - dx2) * (1 - dock);
        st2.sy = dy2 + (tyy - dy2) * (1 - dock);
        st2.sr = 3.4 + (Math.max(trr, 3.4) - 3.4) * (1 - dock);
        if (collapse > 0) {          // the warp pulls the rail in too
          st2.sx += (W / 2 - st2.sx) * collapse;
          st2.sy += (H / 2 - st2.sy) * collapse;
          st2.sr *= (1 - collapse);
        }
      }
      // connector threading the docked markers
      sx.beginPath();
      for (var c2 = 0; c2 < STOPS.length; c2++) {
        var m2 = STOPS[c2];
        if (c2 === 0) sx.moveTo(m2.sx, m2.sy); else sx.lineTo(m2.sx, m2.sy);
      }
      sx.strokeStyle = "rgba(120,145,180,.20)";
      sx.lineWidth = 1; sx.stroke();
      for (var r3 = 0; r3 < STOPS.length; r3++) {
        var st3 = STOPS[r3], on = st3.c * 1.5;
        sx.beginPath(); sx.arc(st3.sx, st3.sy, st3.sr, 0, 6.2832);
        sx.fillStyle = on > 0.5
          ? "rgba(88,166,255,.95)"
          : "rgba(198,212,235," + (0.26 + 0.3 * on).toFixed(3) + ")";
        sx.fill();
        if (on > 0.5) {
          sx.beginPath(); sx.arc(st3.sx, st3.sy, st3.sr + 7, 0, 6.2832);
          sx.strokeStyle = "rgba(88,166,255,.45)";
          sx.lineWidth = 1; sx.stroke();
        }
      }
    };

    var settled = 0;
    var tickS = function () {
      // A docs page is for reading: let the camera settle, paint once
      // more, then stop entirely. No loop, no drift, no battery burn —
      // the only thing that moves on a docs page is the fade-in.
      if (AMBIENT && ++settled > 90) { drawS(); rafS = null; liveS = false; return; }
      spin += AMBIENT ? 0 : 0.00055;
      weigh();
      for (var d = 0; d < 3; d++) {                 // ease toward the target
        cam.p[d] += (camT.p[d] - cam.p[d]) * 0.062;
        cam.t[d] += (camT.t[d] - cam.t[d]) * 0.062;
      }
      cam.o += (camT.o - cam.o) * 0.062;
      cam.oy += ((camT.oy || 0) - (cam.oy || 0)) * 0.062;
      mtx += (mx - mtx) * 0.05;
      mty += (my - mty) * 0.05;
      drawS();
      rafS = requestAnimationFrame(tickS);
    };

    // The rail is painted in the canvas now. This nav stays for keyboard
    // and screen-reader users: real buttons, visually hidden, focusable.
    var rail = document.getElementById("rail"), dots = [];
    var paintRail = function () {
      for (var i = 0; i < dots.length; i++) {
        dots[i].setAttribute("aria-current", i === active ? "true" : "false");
      }
    };
    if (rail) {
      rail.innerHTML = STOPS.map(function (st, i) {
        return '<button class="rdot" data-i="' + i + '">' + st.label + '</button>';
      }).join("");
      dots = [].slice.call(rail.querySelectorAll(".rdot"));
      dots.forEach(function (b) {
        b.onclick = function () {
          var el = STOPS[+b.dataset.i].el;
          if (lenis) lenis.scrollTo(el, { offset: -(innerHeight - el.offsetHeight) / 2 });
          else el.scrollIntoView({ behavior: "smooth", block: "center" });
        };
      });
    }
    addEventListener("scroll", weigh, { passive: true });
    if (lenis) lenis.on("scroll", weigh);
    weigh(); paintRail();
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

    /* ---------- warp: the launch transition ----------
       Clicking a doc link collapses every node in the graph into a single
       point, rushes the camera through it, and hands over to the next page
       on a white-out. The nodes are the site's whole visual language, so
       the navigation is made of them rather than a generic page fade. */
    window.__warp = function (href) {
      if (warping) return;
      warping = true;
      var t0 = performance.now(), DUR = 780;
      var veil = document.createElement("div");
      veil.className = "warp-veil";
      document.body.appendChild(veil);
      var step = function (t) {
        var k = Math.min(1, (t - t0) / DUR);
        // 0-0.55 the scattered nodes compress into one point;
        // 0.55-1 that point opens up and swallows the screen — the zoom
        collapse = k < 0.55 ? Math.pow(k / 0.55, 1.6) : 1;
        rush = k < 0.55 ? 0 : Math.pow((k - 0.55) / 0.45, 2.0);
        // the veil only covers the last moment, so the navigation swap is
        // invisible; the zoom itself is drawn in the canvas
        veil.style.opacity = k < 0.9 ? "0" : ((k - 0.9) / 0.1).toFixed(3);
        if (k < 1) requestAnimationFrame(step);
        else window.location.href = href;
      };
      requestAnimationFrame(step);
    };
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

  /* ---------- landing parallax + stat counters ---------- */

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
