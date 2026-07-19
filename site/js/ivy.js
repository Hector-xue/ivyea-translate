/* Ivyea Translate 门户 —— 常春藤生命轮回动效
 *
 * 视觉系统三层：
 *   1. 世代(cohort)烘焙层：藤茎与定型叶按"世代"分层烘焙。老世代到期进入
 *      "秋天"——整层淡出、其叶染黄飘落（重力+旋转+横摆），随后世代销毁、
 *      密度释放。画面因此永远不会被长满：生长与凋零循环往复。
 *   2. 动画层：舒展中的新叶（光标靠近会加剧摇曳"沙沙"作响）、飘落的秋叶、
 *      结果期的浆果。
 *   3. 氛围层：常驻漂浮光尘 + 交互火花微粒。
 *
 * 交互：
 *   - 划过：鼠标滑动路径每隔几十像素即时冒出嫩叶 + 微型藤芽 + 光尘（核心手感）
 *   - 停留：种下会长大的藤
 *   - 点击：爆发生长 + 光环火花
 *
 * 叶片 = 公有领域真实常春藤扫描图精灵（assets/leaves/），失败回退矢量。
 */
(function () {
  "use strict";

  var canvas = document.getElementById("ivy-canvas");
  if (!canvas) return;
  var ctx = canvas.getContext("2d");

  var DPR = Math.min(window.devicePixelRatio || 1, 2);
  var W = 0, H = 0;
  var MOBILE = Math.min(window.innerWidth, window.innerHeight) < 620 || "ontouchstart" in window;

  // ---- 调参 ----
  var STEP = 2.4;
  var MAX_TIPS = MOBILE ? 14 : 30;
  var LEAF_EVERY = [8, 13];
  var LEAF_LIVE_S = 6.0;            // 新叶动画期
  var ATTRACT_R = MOBILE ? 150 : 230;
  var DWELL_MS = 300;
  var VINE_LIFE = [200, 460];
  var BERRY_CHANCE = 0.35;
  var LEAF_SCALE = MOBILE ? 0.8 : 1;
  var TRAIL_GAP = 64;               // 划过多远冒一簇
  var SEG_PER_COHORT = MOBILE ? 2400 : 5200;  // 每世代容量
  var MAX_COHORTS = 4;              // 超过则最老世代入秋
  var COHORT_MAX_AGE_S = 70;        // 世代最长寿命（保证"终会消失"）
  if (location.search.indexOf("ivyfast") >= 0) COHORT_MAX_AGE_S = 7;  // 调试：加速轮回
  var FALL_FADE_S = 2.2;            // 秋天淡出时长
  var MOTES = MOBILE ? 10 : 18;     // 常驻光尘数

  var STEM_YOUNG = [143, 176, 94];
  var STEM_MID = [94, 127, 66];
  var STEM_WOODY = [110, 91, 68];
  var LEAF_STAGES = [[176, 212, 122], [136, 184, 88], [107, 165, 63], [77, 128, 47]];
  var LEAF_ADULT = [62, 104, 42];
  var BERRY = [56, 50, 74];

  // ---- 真实叶片精灵 ----
  var SPRITE_META = [
    { file: "assets/leaves/leaf_s0.png", ax: 0.2573, ay: 0.9994, lobed: false },
    { file: "assets/leaves/leaf_s1.png", ax: 0.3745, ay: 0.9992, lobed: true },
    { file: "assets/leaves/leaf_s2.png", ax: 0.1363, ay: 0.9992, lobed: false },
    { file: "assets/leaves/leaf_s3.png", ax: 0.7115, ay: 0.9990, lobed: false },
  ];
  // 0-3 = 叶龄；4 = 秋叶（飘落时染黄）
  var TINT_FILTERS = [
    "brightness(1.45) saturate(0.85) hue-rotate(14deg)",
    "brightness(1.22) saturate(0.95) hue-rotate(6deg)",
    "none",
    "brightness(0.88) saturate(1.08)",
    "sepia(0.55) hue-rotate(-18deg) saturate(1.6) brightness(1.1)",
  ];
  var sprites = [];
  var spritesReady = false;

  // ---- 状态 ----
  var cohorts = [];        // {canvas, ctx, grid, segs, leaves[], born, state:'grow'|'fall', fallStart, alpha}
  var vines = [];
  var liveLeaves = [];
  var liveBerries = [];
  var fallingLeaves = [];
  var sparks = [];
  var motes = [];
  var totalSegments = 0;
  var bakedLeaves = 0;
  var gridW = 0, gridH = 0, CELL = 56;
  var pointer = { x: -1e4, y: -1e4, lastX: -1e4, lastY: -1e4, stillSince: 0, lastSpawn: 0,
                  trailX: -1e4, trailY: -1e4 };
  var started = performance.now();
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var running = true;

  function rand(a, b) { return a + Math.random() * (b - a); }
  function irand(a, b) { return Math.floor(rand(a, b + 1)); }
  function rgba(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")"; }
  function mix(a, b, t) {
    return [Math.round(a[0] + (b[0] - a[0]) * t), Math.round(a[1] + (b[1] - a[1]) * t), Math.round(a[2] + (b[2] - a[2]) * t)];
  }
  function leafColor(t) {
    var seg = Math.min(LEAF_STAGES.length - 2, Math.floor(t * (LEAF_STAGES.length - 1)));
    var f = t * (LEAF_STAGES.length - 1) - seg;
    return mix(LEAF_STAGES[seg], LEAF_STAGES[seg + 1], f);
  }

  // ---- 世代 ----
  function newCohort() {
    var c = document.createElement("canvas");
    c.width = Math.round(W * DPR);
    c.height = Math.round(H * DPR);
    var cc = c.getContext("2d");
    cc.setTransform(DPR, 0, 0, DPR, 0, 0);
    var co = {
      canvas: c, ctx: cc,
      grid: new Uint16Array(gridW * gridH),
      segs: 0, leaves: [],
      born: performance.now(),
      state: "grow", alpha: 1, fallStart: 0,
    };
    cohorts.push(co);
    return co;
  }

  function currentCohort() {
    var co = cohorts[cohorts.length - 1];
    if (!co || co.state !== "grow" || co.segs >= SEG_PER_COHORT) co = newCohort();
    return co;
  }

  function densityAt(x, y) {
    var i = Math.floor(x / CELL), j = Math.floor(y / CELL);
    if (i < 0 || j < 0 || i >= gridW || j >= gridH) return 99;
    var idx = j * gridW + i, sum = 0;
    for (var k = 0; k < cohorts.length; k++) sum += cohorts[k].grid[idx] || 0;
    return sum;
  }

  function maybeRetire(now) {
    // 同一时刻只让一个世代入秋
    for (var k = 0; k < cohorts.length; k++) if (cohorts[k].state === "fall") return;
    var growing = cohorts.filter(function (c) { return c.state === "grow"; });
    var oldest = growing[0];
    if (!oldest || oldest.segs < 200) return;  // 太小的世代不值得演凋零
    var tooMany = growing.length > MAX_COHORTS;
    var tooOld = (now - oldest.born) / 1000 > COHORT_MAX_AGE_S;
    if (!tooMany && !tooOld) return;
    if (growing.length < 2) newCohort();  // 保证后续生长有承接层
    startFall(oldest, now);
  }

  function startFall(co, now) {
    co.state = "fall";
    co.fallStart = now;
    // 该世代的叶染秋色陆续飘落
    var leaves = co.leaves;
    for (var i = 0; i < leaves.length; i++) {
      var rec = leaves[i];
      fallingLeaves.push({
        x: rec.x, y: rec.y, size: rec.size,
        spriteIdx: rec.spriteIdx, flip: rec.flip,
        angle: rec.angle, spin: rand(-1.6, 1.6),
        vx: rand(-6, 6), vy: rand(6, 18),
        phase: rand(0, Math.PI * 2),
        delay: rand(0, FALL_FADE_S * 0.8) * 1000,  // 错峰起飞
        start: now, alpha: 1,
      });
    }
    co.leaves = [];
  }

  function updateCohorts(now) {
    for (var k = cohorts.length - 1; k >= 0; k--) {
      var co = cohorts[k];
      if (co.state === "fall") {
        var t = (now - co.fallStart) / (FALL_FADE_S * 1000);
        co.alpha = Math.max(0, 1 - t);
        if (co.alpha <= 0) {
          totalSegments -= co.segs;
          cohorts.splice(k, 1);
        }
      }
    }
    maybeRetire(now);
  }

  // ---- 藤 ----
  function spawnVine(x, y, heading, gen, life) {
    if (vines.length >= MAX_TIPS) return null;
    var v = {
      x: x, y: y, heading: heading,
      curl: rand(-0.05, 0.05),
      thickness: rand(2.2, 3.4) * (gen > 0 ? 0.75 : 1),
      age: 0,
      life: life || irand(VINE_LIFE[0], VINE_LIFE[1]) * (gen > 0 ? 0.6 : 1),
      leafGap: irand(LEAF_EVERY[0], LEAF_EVERY[1]),
      sinceLeaf: irand(0, 4),
      side: Math.random() < 0.5 ? 1 : -1,
      gen: gen || 0,
      path: [[x, y]],
      cohort: currentCohort(),
    };
    vines.push(v);
    return v;
  }

  function stepVine(v, boost) {
    var steps = Math.max(1, Math.round(boost));
    for (var s = 0; s < steps; s++) {
      if (v.age >= v.life) { finishVine(v); return false; }
      if (v.cohort.state !== "grow") { v.cohort = currentCohort(); }
      v.curl = v.curl * 0.965 + rand(-0.028, 0.028);
      v.curl = Math.max(-0.11, Math.min(0.11, v.curl));
      var up = -Math.PI / 2;
      var du = ((up - v.heading + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
      v.heading += v.curl + du * 0.006;
      var dx = pointer.x - v.x, dy = pointer.y - v.y;
      if (dx * dx + dy * dy < ATTRACT_R * ATTRACT_R) {
        var ang = Math.atan2(dy, dx);
        var da = ((ang - v.heading + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
        v.heading += da * 0.045;
      }
      var nx = v.x + Math.cos(v.heading) * STEP;
      var ny = v.y + Math.sin(v.heading) * STEP;
      if (nx < -30 || nx > W + 30 || ny < -30 || ny > H + 30 || densityAt(nx, ny) > 22) {
        finishVine(v); return false;
      }
      var t = v.age / v.life;
      var col = t < 0.55 ? mix(STEM_YOUNG, STEM_MID, t / 0.55) : STEM_MID;
      var bc = v.cohort.ctx;
      bc.save();
      bc.shadowColor = "rgba(45, 62, 35, 0.28)";
      bc.shadowBlur = 1.5;
      bc.shadowOffsetY = 1;
      bc.strokeStyle = rgba(col, 0.95);
      bc.lineWidth = Math.max(0.7, v.thickness * (1 - t * 0.6));
      bc.lineCap = "round";
      bc.beginPath();
      bc.moveTo(v.x, v.y);
      bc.lineTo(nx, ny);
      bc.stroke();
      bc.restore();
      v.x = nx; v.y = ny;
      v.path.push([nx, ny]);
      v.age++;
      v.cohort.segs++;
      totalSegments++;
      var gi = Math.floor(nx / CELL), gj = Math.floor(ny / CELL);
      if (gi >= 0 && gj >= 0 && gi < gridW && gj < gridH) v.cohort.grid[gj * gridW + gi]++;
      if (++v.sinceLeaf >= v.leafGap) {
        v.sinceLeaf = 0;
        v.side = -v.side;
        spawnLeaf(v.x, v.y, v.heading + v.side * rand(0.9, 1.5), t, v.gen, v.cohort);
      }
      if (v.gen < 2 && v.age > 40 && Math.random() < 0.012 && vines.length < MAX_TIPS) {
        spawnVine(v.x, v.y, v.heading + v.side * rand(0.5, 1.1), v.gen + 1);
      }
    }
    return true;
  }

  function finishVine(v) {
    var idx = vines.indexOf(v);
    if (idx >= 0) vines.splice(idx, 1);
    if (v.path.length > 60 && v.cohort.state === "grow") {
      bakeWoody(v);
      if (Math.random() < BERRY_CHANCE) {
        var tip = v.path[v.path.length - 1];
        liveBerries.push({ x: tip[0], y: tip[1], birth: performance.now(),
                           n: irand(6, 10), seed: Math.random() * 100, cohort: v.cohort });
      }
    }
  }

  function bakeWoody(v) {
    var n = Math.floor(v.path.length * 0.55);
    if (n < 8) return;
    var bc = v.cohort.ctx;
    bc.strokeStyle = rgba(STEM_WOODY, 0.5);
    bc.lineWidth = v.thickness * 1.05;
    bc.lineCap = "round";
    bc.beginPath();
    bc.moveTo(v.path[0][0], v.path[0][1]);
    for (var i = 1; i < n; i++) bc.lineTo(v.path[i][0], v.path[i][1]);
    bc.stroke();
    bc.strokeStyle = rgba(STEM_WOODY, 0.35);
    bc.lineWidth = 0.7;
    for (var j = 6; j < n; j += irand(9, 14)) {
      var p = v.path[j], q = v.path[j - 1];
      var a = Math.atan2(p[1] - q[1], p[0] - q[0]) + Math.PI / 2;
      var len = rand(2.5, 5);
      bc.beginPath();
      bc.moveTo(p[0], p[1]);
      bc.lineTo(p[0] + Math.cos(a) * len, p[1] + Math.sin(a) * len);
      bc.stroke();
    }
  }

  // ---- 精灵 ----
  function loadSprites(done) {
    var pending = SPRITE_META.length;
    var ok = true;
    SPRITE_META.forEach(function (meta, idx) {
      var img = new Image();
      img.onload = function () {
        var variants = [];
        for (var t = 0; t < TINT_FILTERS.length; t++) {
          var c = document.createElement("canvas");
          c.width = img.naturalWidth;
          c.height = img.naturalHeight;
          var cc = c.getContext("2d");
          if (TINT_FILTERS[t] !== "none" && "filter" in cc) cc.filter = TINT_FILTERS[t];
          cc.drawImage(img, 0, 0);
          variants.push(c);
        }
        sprites[idx] = { meta: meta, variants: variants, w: img.naturalWidth, h: img.naturalHeight };
        if (--pending === 0) done(ok);
      };
      img.onerror = function () { ok = false; if (--pending === 0) done(ok); };
      img.src = meta.file;
    });
  }

  function pickSpriteIdx(adult) {
    if (!adult) return Math.random() < 0.7 ? 1 : [0, 2, 3][irand(0, 2)];
    return [0, 2, 3][irand(0, 2)];
  }

  // ---- 叶 ----
  function spawnLeaf(x, y, angle, maturity, gen, cohort, sizeMul) {
    liveLeaves.push({
      x: x, y: y, angle: angle,
      size: rand(34, 58) * LEAF_SCALE * (1 - maturity * 0.25) * (gen > 0 ? 0.85 : 1) * (sizeMul || 1),
      birth: performance.now(),
      phase: rand(0, Math.PI * 2),
      adult: maturity > 0.72,
      maturity: maturity,
      spriteIdx: pickSpriteIdx(maturity > 0.72),
      flip: Math.random() < 0.5,
      cohort: cohort,
    });
  }

  function drawLeafSpriteAt(c, x, y, angle, size, spriteIdx, flip, tintIdx, alpha) {
    var sp = sprites[spriteIdx];
    if (!sp || size < 1) return;
    var img = sp.variants[tintIdx] || sp.variants[2];
    var h = size, w = h * (sp.w / sp.h);
    c.save();
    if (alpha !== undefined && alpha < 1) c.globalAlpha = Math.max(0, alpha);
    c.translate(x, y);
    c.rotate(angle + Math.PI / 2);
    if (flip) c.scale(-1, 1);
    c.shadowColor = "rgba(45, 62, 35, 0.30)";
    c.shadowBlur = 3;
    c.shadowOffsetX = 1;
    c.shadowOffsetY = 2;
    c.drawImage(img, -sp.meta.ax * w, -sp.meta.ay * h, w, h);
    c.restore();
  }

  function drawLeaf(c, leaf, scale, sway) {
    if (spritesReady) {
      var tintIdx = leaf.adult ? 3 : Math.min(3, Math.floor((leaf.maturity + 0.18) * 3.2));
      drawLeafSpriteAt(c, leaf.x, leaf.y, leaf.angle + sway, leaf.size * scale,
                       leaf.spriteIdx, leaf.flip, tintIdx);
      return;
    }
    // 矢量兜底
    c.save();
    c.translate(leaf.x, leaf.y);
    c.rotate(leaf.angle + Math.PI / 2 + sway);
    var col = leaf.adult ? LEAF_ADULT : leafColor(Math.min(1, leaf.maturity + 0.15));
    var s = leaf.size * 0.34;
    c.translate(0, s * 0.30 * scale);
    c.scale(scale, scale);
    if (leaf.adult) adultLeafPath(c, s); else juvenileLeafPath(c, s);
    c.fillStyle = rgba(col, 0.96);
    c.fill();
    c.restore();
  }

  function bakeLeaf(leaf) {
    if (leaf.cohort && leaf.cohort.state === "grow") {
      drawLeaf(leaf.cohort.ctx, leaf, 1, 0);
      leaf.cohort.leaves.push({
        x: leaf.x, y: leaf.y, angle: leaf.angle, size: leaf.size,
        spriteIdx: leaf.spriteIdx, flip: leaf.flip,
      });
      bakedLeaves++;
    }
  }

  function juvenileLeafPath(c, s) {
    c.beginPath();
    c.moveTo(0, 0.02 * s);
    c.bezierCurveTo(0.24 * s, -0.10 * s, 0.52 * s, -0.06 * s, 0.62 * s, 0.10 * s);
    c.bezierCurveTo(0.50 * s, 0.22 * s, 0.38 * s, 0.27 * s, 0.28 * s, 0.33 * s);
    c.bezierCurveTo(0.42 * s, 0.36 * s, 0.52 * s, 0.44 * s, 0.52 * s, 0.55 * s);
    c.bezierCurveTo(0.38 * s, 0.62 * s, 0.24 * s, 0.62 * s, 0.13 * s, 0.62 * s);
    c.bezierCurveTo(0.13 * s, 0.78 * s, 0.07 * s, 0.94 * s, 0, 1.06 * s);
    c.bezierCurveTo(-0.07 * s, 0.94 * s, -0.13 * s, 0.78 * s, -0.13 * s, 0.62 * s);
    c.bezierCurveTo(-0.24 * s, 0.62 * s, -0.38 * s, 0.62 * s, -0.52 * s, 0.55 * s);
    c.bezierCurveTo(-0.52 * s, 0.44 * s, -0.42 * s, 0.36 * s, -0.28 * s, 0.33 * s);
    c.bezierCurveTo(-0.38 * s, 0.27 * s, -0.50 * s, 0.22 * s, -0.62 * s, 0.10 * s);
    c.bezierCurveTo(-0.52 * s, -0.06 * s, -0.24 * s, -0.10 * s, 0, 0.02 * s);
    c.closePath();
  }

  function adultLeafPath(c, s) {
    c.beginPath();
    c.moveTo(0, 0);
    c.bezierCurveTo(0.34 * s, 0.10 * s, 0.36 * s, 0.55 * s, 0, 1.0 * s);
    c.bezierCurveTo(-0.36 * s, 0.55 * s, -0.34 * s, 0.10 * s, 0, 0);
    c.closePath();
  }

  // ---- 浆果 ----
  function drawBerryCluster(c, b, scale) {
    scale = Math.max(0.05, Math.min(1, scale));
    c.save();
    c.translate(b.x, b.y);
    var R = 7 * scale;
    for (var i = 0; i < b.n; i++) {
      var a = (i / b.n) * Math.PI * 2 + b.seed;
      var r = R * (0.55 + 0.45 * (((b.seed * 31 + i * 7) % 10) / 10));
      var px = Math.cos(a) * r, py = Math.sin(a) * r;
      c.strokeStyle = rgba(STEM_WOODY, 0.55);
      c.lineWidth = 0.7;
      c.beginPath(); c.moveTo(0, 0); c.lineTo(px, py); c.stroke();
      var rr = 2.6 * scale;
      var grad = c.createRadialGradient(px - rr * 0.35, py - rr * 0.35, rr * 0.15, px, py, rr);
      grad.addColorStop(0, "rgba(96, 88, 118, 1)");
      grad.addColorStop(0.55, rgba(BERRY, 1));
      grad.addColorStop(1, "rgba(30, 26, 42, 1)");
      c.fillStyle = grad;
      c.beginPath(); c.arc(px, py, rr, 0, Math.PI * 2); c.fill();
      c.fillStyle = "rgba(255,255,255,0.45)";
      c.beginPath(); c.arc(px - rr * 0.35, py - rr * 0.4, rr * 0.22, 0, Math.PI * 2); c.fill();
    }
    c.restore();
  }

  // ---- 微粒 ----
  function initMotes() {
    motes = [];
    for (var i = 0; i < MOTES; i++) {
      motes.push({
        x: rand(0, W), y: rand(0, H),
        r: rand(1.2, 3.2), a: rand(0.08, 0.22),
        vy: rand(-8, -3), drift: rand(6, 18), phase: rand(0, Math.PI * 2),
      });
    }
  }

  function drawMotes(now, dt) {
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (var i = 0; i < motes.length; i++) {
      var m = motes[i];
      m.y += m.vy * dt / 1000;
      var x = m.x + Math.sin(now / 1400 + m.phase) * m.drift;
      if (m.y < -8) { m.y = H + 8; m.x = rand(0, W); }
      var tw = 0.6 + 0.4 * Math.sin(now / 700 + m.phase * 3);
      ctx.fillStyle = "rgba(196, 226, 150, " + (m.a * tw).toFixed(3) + ")";
      ctx.beginPath();
      ctx.arc(x, m.y, m.r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function spawnSparks(x, y, n, spread) {
    for (var i = 0; i < n; i++) {
      var a = rand(0, Math.PI * 2);
      var sp = rand(10, spread || 40);
      sparks.push({
        x: x, y: y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp - 18,
        r: rand(1, 2.4), born: performance.now(), life: rand(500, 900),
      });
    }
  }

  function drawSparks(now, dt) {
    if (!sparks.length) return;
    ctx.save();
    ctx.globalCompositeOperation = "lighter";
    for (var i = sparks.length - 1; i >= 0; i--) {
      var s = sparks[i];
      var t = (now - s.born) / s.life;
      if (t >= 1) { sparks.splice(i, 1); continue; }
      s.x += s.vx * dt / 1000;
      s.y += s.vy * dt / 1000;
      s.vy += 26 * dt / 1000;
      ctx.fillStyle = "rgba(214, 238, 168, " + (0.5 * (1 - t)).toFixed(3) + ")";
      ctx.beginPath();
      ctx.arc(s.x, s.y, s.r * (1 - t * 0.5), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  // ---- 秋叶飘落 ----
  function drawFallingLeaves(now, dt) {
    for (var i = fallingLeaves.length - 1; i >= 0; i--) {
      var f = fallingLeaves[i];
      if (now - f.start < f.delay) continue;
      var k = dt / 1000;
      f.vy = Math.min(70, f.vy + 40 * k);          // 重力（带终端速度）
      f.x += (f.vx + Math.sin(now / 500 + f.phase) * 26) * k;  // 横摆
      f.y += f.vy * k;
      f.angle += f.spin * k;
      if (f.y > H * 0.86) f.alpha -= 1.6 * k;      // 接近底部渐隐
      if (f.y > H + 40 || f.alpha <= 0) { fallingLeaves.splice(i, 1); continue; }
      if (spritesReady) {
        drawLeafSpriteAt(ctx, f.x, f.y, f.angle, f.size, f.spriteIdx, f.flip, 4, f.alpha);
      }
    }
  }

  // ---- 交互 ----
  function trailBurst(x, y) {
    var co = currentCohort();
    // 即时嫩叶（先给到眼睛的反馈）
    spawnLeaf(x + rand(-4, 4), y + rand(-4, 4), rand(0, Math.PI * 2), rand(0, 0.25), 0, co, rand(0.7, 1.0));
    if (Math.random() < 0.6) {
      spawnLeaf(x + rand(-10, 10), y + rand(-10, 10), rand(0, Math.PI * 2), rand(0, 0.3), 1, co, rand(0.55, 0.8));
    }
    // 微型藤芽随后长开
    if (vines.length < MAX_TIPS) {
      spawnVine(x, y, rand(0, Math.PI * 2), 1, irand(36, 90));
    }
    spawnSparks(x, y, MOBILE ? 3 : 5, 34);
  }

  function onPointerMove(e) {
    var r = canvas.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    var now = performance.now();
    var moved = Math.abs(x - pointer.lastX) + Math.abs(y - pointer.lastY);
    if (moved > 24) { pointer.stillSince = now; pointer.lastX = x; pointer.lastY = y; }
    pointer.x = x; pointer.y = y;
    // 划过轨迹：每 TRAIL_GAP 像素冒一簇
    var tdx = x - pointer.trailX, tdy = y - pointer.trailY;
    if (tdx * tdx + tdy * tdy > TRAIL_GAP * TRAIL_GAP) {
      pointer.trailX = x; pointer.trailY = y;
      if (densityAt(x, y) < 14) trailBurst(x, y);
      hideHint();
    }
    // 停留：种一株完整的藤
    if (now - pointer.stillSince > DWELL_MS && now - pointer.lastSpawn > 500 &&
        densityAt(x, y) < 6) {
      pointer.lastSpawn = now;
      spawnVine(x + rand(-6, 6), y + rand(-6, 6), rand(0, Math.PI * 2), 0);
    }
  }

  function onPointerDown(e) {
    var r = canvas.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    for (var i = 0; i < 3; i++) spawnVine(x, y, rand(0, Math.PI * 2), 0);
    spawnSparks(x, y, MOBILE ? 10 : 16, 70);
    hideHint();
  }

  var hintHidden = false;
  function hideHint() {
    if (hintHidden) return;
    hintHidden = true;
    var hint = document.getElementById("ivy-hint");
    if (hint) hint.classList.add("gone");
  }

  // ---- 主循环 ----
  var lastFrame = performance.now();
  function frame(now) {
    if (!running) return;
    try { frameBody(now); } catch (e) {
      if (!window.__ivyerr) window.__ivyerr = String(e);
    }
    requestAnimationFrame(frame);
  }

  function frameBody(now) {
    var dt = Math.min(50, now - lastFrame);
    lastFrame = now;
    updateCohorts(now);
    var warm = Math.max(1, 3.2 - (now - started) / 1400);
    for (var i = vines.length - 1; i >= 0; i--) {
      var v = vines[i];
      var dx = pointer.x - v.x, dy = pointer.y - v.y;
      var near = dx * dx + dy * dy < ATTRACT_R * ATTRACT_R;
      stepVine(v, (near ? 2.6 : 1) * warm * 1.35 * (dt / 16.7));
    }
    ctx.clearRect(0, 0, W, H);
    // 世代层（老世代带淡出 alpha）
    for (var k = 0; k < cohorts.length; k++) {
      var co = cohorts[k];
      if (co.alpha < 1) ctx.globalAlpha = co.alpha;
      ctx.drawImage(co.canvas, 0, 0, W, H);
      if (co.alpha < 1) ctx.globalAlpha = 1;
    }
    // 舒展中的新叶（光标附近沙沙作响）
    for (var j = liveLeaves.length - 1; j >= 0; j--) {
      var leaf = liveLeaves[j];
      var age = Math.max(0, (now - leaf.birth) / 1000);
      if (age > LEAF_LIVE_S) {
        bakeLeaf(leaf);
        liveLeaves.splice(j, 1);
        continue;
      }
      var u = Math.min(1, age / 1.1);
      var back = 1.7;
      var scale = 1 + (back + 1) * Math.pow(u - 1, 3) + back * Math.pow(u - 1, 2);
      var swayAmp = 0.07 * (1 - age / LEAF_LIVE_S);
      var ldx = pointer.x - leaf.x, ldy = pointer.y - leaf.y;
      var ld2 = ldx * ldx + ldy * ldy;
      if (ld2 < 120 * 120) swayAmp += 0.12 * (1 - Math.sqrt(ld2) / 120);  // 沙沙
      var sway = Math.sin(now / 900 + leaf.phase) * swayAmp;
      drawLeaf(ctx, leaf, Math.max(0.02, scale), sway);
    }
    // 浆果
    for (var b = liveBerries.length - 1; b >= 0; b--) {
      var berry = liveBerries[b];
      var bAge = Math.max(0, (now - berry.birth) / 1000);
      if (bAge > 3) {
        if (berry.cohort.state === "grow") drawBerryCluster(berry.cohort.ctx, berry, 1);
        liveBerries.splice(b, 1);
        continue;
      }
      drawBerryCluster(ctx, berry, Math.min(1, bAge / 1.4));
    }
    drawFallingLeaves(now, dt);
    drawMotes(now, dt);
    drawSparks(now, dt);
    // 常态补芽
    if (vines.length < 5 && Math.random() < 0.02) spawnEdgeSprout();
  }

  function spawnEdgeSprout() {
    var side = Math.random();
    if (side < 0.5) spawnVine(rand(0, W), H + 6, -Math.PI / 2 + rand(-0.5, 0.5), 0);
    else if (side < 0.7) spawnVine(-6, rand(H * 0.25, H), rand(-0.6, 0.3), 0);
    else if (side < 0.9) spawnVine(W + 6, rand(H * 0.25, H), Math.PI + rand(-0.3, 0.6), 0);
    else spawnVine(rand(W * 0.05, W * 0.95), -6, Math.PI / 2 + rand(-0.4, 0.4), 0);  // 顶部垂藤
  }

  // ---- 尺寸 / 初始化 ----
  function resize(keep) {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    var oldW = W, oldH = H;
    W = w; H = h;
    canvas.width = Math.round(w * DPR);
    canvas.height = Math.round(h * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    gridW = Math.ceil(W / CELL); gridH = Math.ceil(H / CELL);
    for (var k = 0; k < cohorts.length; k++) {
      var co = cohorts[k];
      var nb = document.createElement("canvas");
      nb.width = canvas.width; nb.height = canvas.height;
      var nc = nb.getContext("2d");
      nc.setTransform(DPR, 0, 0, DPR, 0, 0);
      if (keep && oldW) nc.drawImage(co.canvas, 0, 0, W, H);
      co.canvas = nb; co.ctx = nc;
      var g = new Uint16Array(gridW * gridH);
      g.set(co.grid.subarray(0, Math.min(co.grid.length, g.length)));
      co.grid = g;
    }
    if (!keep) initMotes();
  }

  // 首屏构图：底部丛生 + 两侧攀爬 + 顶角垂藤，三面环拱
  function plantOpening() {
    for (var i = 0; i < 5; i++) {
      spawnVine(rand(W * 0.04, W * 0.96), H + 6, -Math.PI / 2 + rand(-0.5, 0.5), 0);
    }
    spawnVine(-6, rand(H * 0.35, H * 0.7), rand(-0.5, 0.2), 0);
    spawnVine(W + 6, rand(H * 0.35, H * 0.7), Math.PI + rand(-0.2, 0.5), 0);
    spawnVine(rand(W * 0.02, W * 0.2), -6, Math.PI / 2 + rand(-0.2, 0.5), 0);
    spawnVine(rand(W * 0.8, W * 0.98), -6, Math.PI / 2 + rand(-0.5, 0.2), 0);
  }

  function preGrow(iters) {
    var t0 = performance.now();
    for (var f = 0; f < iters; f++) {
      for (var vi = vines.length - 1; vi >= 0; vi--) stepVine(vines[vi], 1);
      if (vines.length < 4 && Math.random() < 0.04) spawnEdgeSprout();
    }
    for (var li = liveLeaves.length - 1; li >= 0; li--) {
      var leaf = liveLeaves[li];
      var fake = rand(0, LEAF_LIVE_S * 2.2) * 1000;
      if (fake > LEAF_LIVE_S * 1000) {
        bakeLeaf(leaf);
        liveLeaves.splice(li, 1);
      } else {
        leaf.birth = t0 - fake;
      }
    }
    for (var bi = liveBerries.length - 1; bi >= 0; bi--) {
      drawBerryCluster(liveBerries[bi].cohort.ctx, liveBerries[bi], 1);
      liveBerries.splice(bi, 1);
    }
  }

  function init() {
    resize(false);
    newCohort();
    plantOpening();
    var simMatch = location.search.match(/ivysim=(\d+)/);
    preGrow(simMatch ? parseInt(simMatch[1], 10) : (MOBILE ? 170 : 260));
    if (reduced) {
      for (var li = 0; li < liveLeaves.length; li++) bakeLeaf(liveLeaves[li]);
      liveLeaves = [];
      ctx.clearRect(0, 0, W, H);
      for (var k = 0; k < cohorts.length; k++) ctx.drawImage(cohorts[k].canvas, 0, 0, W, H);
      return;
    }
    if (MOBILE) {
      var hintEl = document.getElementById("ivy-hint");
      if (hintEl) hintEl.textContent = "滑动屏幕让常春藤生长 · 点按爆发";
    }
    var evMove = window.PointerEvent ? "pointermove" : "mousemove";
    var evDown = window.PointerEvent ? "pointerdown" : "mousedown";
    window.addEventListener(evMove, onPointerMove, { passive: true });
    window.addEventListener(evDown, onPointerDown, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { running = false; }
      else if (!running) { running = true; lastFrame = performance.now(); requestAnimationFrame(frame); }
    });
    var resizeTimer = null;
    window.addEventListener("resize", function () {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(function () { resize(true); }, 200);
    });
    requestAnimationFrame(frame);
  }

  // 调试：?ivydebug 把计数写进 title
  if (location.search.indexOf("ivydebug") >= 0) {
    setInterval(function () {
      document.title = "seg=" + totalSegments + " vines=" + vines.length +
        " live=" + liveLeaves.length + " baked=" + bakedLeaves +
        " cohorts=" + cohorts.length + " falling=" + fallingLeaves.length +
        " W=" + W + " H=" + H;
    }, 400);
  }

  function boot() {
    loadSprites(function (ok) {
      spritesReady = ok && sprites.length === SPRITE_META.length && sprites.every(Boolean);
      init();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
