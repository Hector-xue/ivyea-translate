/* Ivyea Translate 门户 —— 常春藤生长动效背景
 *
 * 按真实常春藤（Hedera helix）生命周期建模四个阶段：
 *   1. 萌芽    嫩茎钻出，颜色黄绿
 *   2. 幼藤攀爬 藤蔓蜿蜒向上，交替长出 3~5 裂的掌状幼叶，嫩绿渐深
 *   3. 成熟    老茎木质化变褐、贴生气生根，叶转深绿
 *   4. 开花结果 顶端伞形花序，结出蓝黑色浆果
 *
 * 交互：鼠标悬停处会"种下"新芽；藤蔓感知光标趋光攀爬；点击爆发式生长。
 * 性能：茎与定型的叶即时"烘焙"进离屏画布，动画层只画正在舒展/摇曳的新叶，
 *       任意生长量下每帧成本恒定。
 */
(function () {
  "use strict";

  var canvas = document.getElementById("ivy-canvas");
  if (!canvas) return;
  var ctx = canvas.getContext("2d");
  var baked = document.createElement("canvas");
  var bctx = baked.getContext("2d");

  var DPR = Math.min(window.devicePixelRatio || 1, 2);
  var W = 0, H = 0;

  // ---- 调参区 ----
  var STEP = 2.4;              // 每段长度(px)
  var MAX_TIPS = 34;           // 同时生长的藤尖上限
  var MAX_SEGMENTS = 52000;    // 总段数上限（防过度覆盖）
  var LEAF_EVERY = [7, 11];    // 每隔几段长一片叶
  var LEAF_LIVE_S = 6.5;       // 新叶动画期（之后烘焙定型）
  var ATTRACT_R = 240;         // 光标趋光半径
  var DWELL_MS = 260;          // 悬停多久种新芽
  var VINE_LIFE = [260, 560];  // 单藤段数寿命
  var BERRY_CHANCE = 0.38;     // 成熟藤结浆果概率

  // 生长阶段配色（嫩 -> 深）
  var STEM_YOUNG = [156, 192, 105];
  var STEM_MID = [104, 150, 70];
  var STEM_WOODY = [122, 104, 79];
  var LEAF_STAGES = [
    [176, 212, 122],   // 初展嫩叶
    [136, 184, 88],
    [107, 165, 63],    // 品牌绿
    [77, 128, 47],     // 成熟深绿
  ];
  var LEAF_ADULT = [62, 104, 42];
  var BERRY = [56, 50, 74];

  var vines = [];        // 生长中的藤
  var liveLeaves = [];   // 动画期的叶
  var liveBerries = [];  // 动画期的浆果簇
  var totalSegments = 0;
  var grid = null, gridW = 0, gridH = 0, CELL = 56;
  var pointer = { x: -1e4, y: -1e4, vx: 0, vy: 0, lastX: -1e4, lastY: -1e4, stillSince: 0, lastSpawn: 0 };
  var started = performance.now();
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var running = true;

  function rand(a, b) { return a + Math.random() * (b - a); }
  function irand(a, b) { return Math.floor(rand(a, b + 1)); }
  function rgba(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a + ")"; }
  function mix(a, b, t) {
    return [Math.round(a[0] + (b[0] - a[0]) * t), Math.round(a[1] + (b[1] - a[1]) * t), Math.round(a[2] + (b[2] - a[2]) * t)];
  }
  function leafColor(t) { // t: 0~1 叶龄
    var seg = Math.min(LEAF_STAGES.length - 2, Math.floor(t * (LEAF_STAGES.length - 1)));
    var f = t * (LEAF_STAGES.length - 1) - seg;
    return mix(LEAF_STAGES[seg], LEAF_STAGES[seg + 1], f);
  }

  // ---- 密度网格：避免同一区域过度生长 ----
  function densityAt(x, y) {
    var i = Math.floor(x / CELL), j = Math.floor(y / CELL);
    if (i < 0 || j < 0 || i >= gridW || j >= gridH) return 99;
    return grid[j * gridW + i];
  }
  function addDensity(x, y) {
    var i = Math.floor(x / CELL), j = Math.floor(y / CELL);
    if (i >= 0 && j >= 0 && i < gridW && j < gridH) grid[j * gridW + i]++;
  }

  // ---- 藤 ----
  function spawnVine(x, y, heading, gen) {
    if (vines.length >= MAX_TIPS || totalSegments >= MAX_SEGMENTS) return null;
    var v = {
      x: x, y: y,
      heading: heading,
      curl: rand(-0.05, 0.05),
      thickness: rand(2.2, 3.4) * (gen > 0 ? 0.75 : 1),
      age: 0,
      life: irand(VINE_LIFE[0], VINE_LIFE[1]) * (gen > 0 ? 0.6 : 1),
      leafGap: irand(LEAF_EVERY[0], LEAF_EVERY[1]),
      sinceLeaf: irand(0, 4),
      side: Math.random() < 0.5 ? 1 : -1,
      gen: gen || 0,
      path: [[x, y]],
      energy: 1,
    };
    vines.push(v);
    return v;
  }

  function stepVine(v, boost) {
    var steps = Math.max(1, Math.round(v.energy * boost));
    for (var s = 0; s < steps; s++) {
      if (v.age >= v.life || totalSegments >= MAX_SEGMENTS) { finishVine(v); return false; }
      // 蜿蜒：曲率随机游走（带回中阻尼，避免卷成小圈）+ 轻微向上趋性
      v.curl = v.curl * 0.965 + rand(-0.028, 0.028);
      v.curl = Math.max(-0.11, Math.min(0.11, v.curl));
      var up = -Math.PI / 2;
      var du = ((up - v.heading + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
      v.heading += v.curl + du * 0.006;
      // 趋光：靠近光标时向其弯转
      var dx = pointer.x - v.x, dy = pointer.y - v.y;
      var d2 = dx * dx + dy * dy;
      if (d2 < ATTRACT_R * ATTRACT_R) {
        var ang = Math.atan2(dy, dx);
        var da = ((ang - v.heading + Math.PI * 3) % (Math.PI * 2)) - Math.PI;
        v.heading += da * 0.045;
      }
      var nx = v.x + Math.cos(v.heading) * STEP;
      var ny = v.y + Math.sin(v.heading) * STEP;
      // 出界/过密：终止
      if (nx < -30 || nx > W + 30 || ny < -30 || ny > H + 30 || densityAt(nx, ny) > 26) {
        finishVine(v); return false;
      }
      // 画茎（烘焙层）：颜色随藤龄从嫩绿过渡
      var t = v.age / v.life;
      var col = t < 0.55 ? mix(STEM_YOUNG, STEM_MID, t / 0.55) : STEM_MID;
      bctx.strokeStyle = rgba(col, 0.95);
      bctx.lineWidth = Math.max(0.7, v.thickness * (1 - t * 0.6));
      bctx.lineCap = "round";
      bctx.beginPath();
      bctx.moveTo(v.x, v.y);
      bctx.lineTo(nx, ny);
      bctx.stroke();
      v.x = nx; v.y = ny;
      v.path.push([nx, ny]);
      v.age++; totalSegments++;
      addDensity(nx, ny);
      // 长叶（左右交替）
      if (++v.sinceLeaf >= v.leafGap) {
        v.sinceLeaf = 0;
        v.side = -v.side;
        spawnLeaf(v);
      }
      // 分叉
      if (v.gen < 2 && v.age > 40 && Math.random() < 0.012 && vines.length < MAX_TIPS) {
        spawnVine(v.x, v.y, v.heading + v.side * rand(0.5, 1.1), v.gen + 1);
      }
    }
    return true;
  }

  function finishVine(v) {
    var idx = vines.indexOf(v);
    if (idx >= 0) vines.splice(idx, 1);
    // 成熟阶段：老茎木质化（整条重描一遍偏褐色，带气生根短须）
    if (v.path.length > 60) {
      bakeWoody(v);
      if (Math.random() < BERRY_CHANCE) spawnBerries(v);
    }
  }

  function bakeWoody(v) {
    var n = Math.floor(v.path.length * 0.55); // 靠根部的一段木质化
    if (n < 8) return;
    bctx.strokeStyle = rgba(STEM_WOODY, 0.5);
    bctx.lineWidth = v.thickness * 1.05;
    bctx.lineCap = "round";
    bctx.beginPath();
    bctx.moveTo(v.path[0][0], v.path[0][1]);
    for (var i = 1; i < n; i++) bctx.lineTo(v.path[i][0], v.path[i][1]);
    bctx.stroke();
    // 气生根：短须
    bctx.strokeStyle = rgba(STEM_WOODY, 0.35);
    bctx.lineWidth = 0.7;
    for (var j = 6; j < n; j += irand(9, 14)) {
      var p = v.path[j], q = v.path[j - 1];
      var a = Math.atan2(p[1] - q[1], p[0] - q[0]) + Math.PI / 2;
      var len = rand(2.5, 5);
      bctx.beginPath();
      bctx.moveTo(p[0], p[1]);
      bctx.lineTo(p[0] + Math.cos(a) * len, p[1] + Math.sin(a) * len);
      bctx.stroke();
    }
  }

  // ---- 叶 ----
  function spawnLeaf(v) {
    var t = v.age / v.life;
    liveLeaves.push({
      x: v.x, y: v.y,
      angle: v.heading + v.side * rand(0.9, 1.5),
      size: rand(12, 19) * (1 - t * 0.25) * (v.gen > 0 ? 0.85 : 1),
      birth: performance.now(),
      phase: rand(0, Math.PI * 2),
      adult: t > 0.72,          // 老藤上的叶：成熟全缘叶，更深绿
      maturity: t,
    });
  }

  // 幼叶：五裂掌状、尖角深裂（常春藤标志性叶形）。局部坐标：叶基在原点，叶尖朝 +Y。
  function juvenileLeafPath(c, s) {
    c.beginPath();
    c.moveTo(0, 0.02 * s);
    // 右基裂片（朝外下）
    c.bezierCurveTo(0.24 * s, -0.10 * s, 0.52 * s, -0.06 * s, 0.62 * s, 0.10 * s);
    c.bezierCurveTo(0.50 * s, 0.22 * s, 0.38 * s, 0.27 * s, 0.28 * s, 0.33 * s);
    // 右侧裂片（朝外上）
    c.bezierCurveTo(0.42 * s, 0.36 * s, 0.52 * s, 0.44 * s, 0.52 * s, 0.55 * s);
    c.bezierCurveTo(0.38 * s, 0.62 * s, 0.24 * s, 0.62 * s, 0.13 * s, 0.62 * s);
    // 顶裂片（叶尖）
    c.bezierCurveTo(0.13 * s, 0.78 * s, 0.07 * s, 0.94 * s, 0, 1.06 * s);
    c.bezierCurveTo(-0.07 * s, 0.94 * s, -0.13 * s, 0.78 * s, -0.13 * s, 0.62 * s);
    // 左侧裂片
    c.bezierCurveTo(-0.24 * s, 0.62 * s, -0.38 * s, 0.62 * s, -0.52 * s, 0.55 * s);
    c.bezierCurveTo(-0.52 * s, 0.44 * s, -0.42 * s, 0.36 * s, -0.28 * s, 0.33 * s);
    // 左基裂片
    c.bezierCurveTo(-0.38 * s, 0.27 * s, -0.50 * s, 0.22 * s, -0.62 * s, 0.10 * s);
    c.bezierCurveTo(-0.52 * s, -0.06 * s, -0.24 * s, -0.10 * s, 0, 0.02 * s);
    c.closePath();
  }

  // 成熟叶：全缘卵形
  function adultLeafPath(c, s) {
    c.beginPath();
    c.moveTo(0, 0);
    c.bezierCurveTo(0.34 * s, 0.10 * s, 0.36 * s, 0.55 * s, 0, 1.0 * s);
    c.bezierCurveTo(-0.36 * s, 0.55 * s, -0.34 * s, 0.10 * s, 0, 0);
    c.closePath();
  }

  function drawLeaf(c, leaf, scale, sway) {
    c.save();
    c.translate(leaf.x, leaf.y);
    c.rotate(leaf.angle + Math.PI / 2 + sway);
    // 叶柄
    var col = leaf.adult ? LEAF_ADULT : leafColor(Math.min(1, leaf.maturity + 0.15));
    c.strokeStyle = rgba(mix(col, STEM_MID, 0.4), 0.9);
    c.lineWidth = 1;
    c.beginPath();
    c.moveTo(0, 0);
    c.lineTo(0, leaf.size * 0.30 * scale);
    c.stroke();
    c.translate(0, leaf.size * 0.30 * scale);
    c.scale(scale, scale);
    if (leaf.adult) adultLeafPath(c, leaf.size); else juvenileLeafPath(c, leaf.size);
    c.fillStyle = rgba(col, 0.96);
    c.fill();
    // 叶脉
    c.strokeStyle = "rgba(255,255,255,0.30)";
    c.lineWidth = 0.6;
    c.beginPath();
    c.moveTo(0, 0.04 * leaf.size);
    c.lineTo(0, 0.92 * leaf.size);
    if (!leaf.adult) {
      c.moveTo(0, 0.08 * leaf.size); c.lineTo(0.50 * leaf.size, 0.12 * leaf.size);
      c.moveTo(0, 0.08 * leaf.size); c.lineTo(-0.50 * leaf.size, 0.12 * leaf.size);
      c.moveTo(0, 0.22 * leaf.size); c.lineTo(0.40 * leaf.size, 0.50 * leaf.size);
      c.moveTo(0, 0.22 * leaf.size); c.lineTo(-0.40 * leaf.size, 0.50 * leaf.size);
    }
    c.stroke();
    c.restore();
  }

  // ---- 浆果（伞形果序）----
  function spawnBerries(v) {
    var tip = v.path[v.path.length - 1];
    liveBerries.push({ x: tip[0], y: tip[1], birth: performance.now(), n: irand(6, 10), seed: Math.random() * 100 });
  }

  function drawBerryCluster(c, b, scale) {
    scale = Math.max(0.05, Math.min(1, scale));  // rAF 时间戳可能略早于 birth，果龄会出现负数
    c.save();
    c.translate(b.x, b.y);
    var R = 7 * scale;
    for (var i = 0; i < b.n; i++) {
      var a = (i / b.n) * Math.PI * 2 + b.seed;
      var r = R * (0.55 + 0.45 * (((b.seed * 31 + i * 7) % 10) / 10));
      var px = Math.cos(a) * r, py = Math.sin(a) * r;
      // 果柄
      c.strokeStyle = rgba(STEM_WOODY, 0.55);
      c.lineWidth = 0.7;
      c.beginPath(); c.moveTo(0, 0); c.lineTo(px, py); c.stroke();
      // 浆果
      c.fillStyle = rgba(BERRY, 0.95);
      c.beginPath(); c.arc(px, py, 2.3 * scale, 0, Math.PI * 2); c.fill();
      c.fillStyle = "rgba(255,255,255,0.35)";
      c.beginPath(); c.arc(px - 0.7 * scale, py - 0.7 * scale, 0.6 * scale, 0, Math.PI * 2); c.fill();
    }
    c.restore();
  }

  // ---- 交互 ----
  function onPointerMove(e) {
    var r = canvas.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    var moved = Math.abs(x - pointer.lastX) + Math.abs(y - pointer.lastY);
    var now = performance.now();
    if (moved > 24) { pointer.stillSince = now; pointer.lastX = x; pointer.lastY = y; }
    pointer.x = x; pointer.y = y;
    // 悬停"种芽"：光标停留且该处不密
    if (now - pointer.stillSince > DWELL_MS && now - pointer.lastSpawn > 420 &&
        densityAt(x, y) < 5 && totalSegments < MAX_SEGMENTS) {
      pointer.lastSpawn = now;
      spawnVine(x + rand(-6, 6), y + rand(-6, 6), rand(0, Math.PI * 2), 0);
    }
  }

  function onPointerDown(e) {
    var r = canvas.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    for (var i = 0; i < 3; i++) {
      spawnVine(x, y, rand(0, Math.PI * 2), 0);
    }
    var hint = document.getElementById("ivy-hint");
    if (hint) hint.classList.add("gone");
  }

  // ---- 主循环 ----
  var lastFrame = performance.now();
  function frame(now) {
    if (!running) return;
    try {
      frameBody(now);
    } catch (e) {
      // 单帧出错绝不让整个动画停摆
      if (!window.__ivyerr) window.__ivyerr = String(e);
    }
    requestAnimationFrame(frame);
  }

  function frameBody(now) {
    var dt = Math.min(50, now - lastFrame);
    lastFrame = now;
    // 开场 4 秒爆发生长，让首屏立刻"活"起来；此后回到常速
    var warm = Math.max(1, 3.2 - (now - started) / 1400);
    // 光标附近的藤尖长得更快（"抚过即生长"）
    for (var i = vines.length - 1; i >= 0; i--) {
      var v = vines[i];
      var dx = pointer.x - v.x, dy = pointer.y - v.y;
      var near = dx * dx + dy * dy < ATTRACT_R * ATTRACT_R;
      stepVine(v, (near ? 2.6 : 1) * warm * 1.35 * (dt / 16.7));
    }
    // 合成：底 = 烘焙层，上 = 动画期的叶与果
    ctx.clearRect(0, 0, W, H);
    ctx.drawImage(baked, 0, 0, W, H);
    var t = now;
    for (var j = liveLeaves.length - 1; j >= 0; j--) {
      var leaf = liveLeaves[j];
      var age = Math.max(0, (t - leaf.birth) / 1000);
      if (age > LEAF_LIVE_S) {  // 定型：烘焙进静态层
        drawLeaf(bctx, leaf, 1, 0);
        liveLeaves.splice(j, 1);
        bakedLeaves++;
        continue;
      }
      // 舒展（ease-out-back）+ 幼期轻微摇曳
      var u = Math.min(1, age / 1.1);
      var back = 1.7;
      var scale = 1 + (back + 1) * Math.pow(u - 1, 3) + back * Math.pow(u - 1, 2);
      var sway = Math.sin(t / 900 + leaf.phase) * 0.07 * (1 - age / LEAF_LIVE_S);
      drawLeaf(ctx, leaf, Math.max(0.02, scale), sway);
    }
    for (var k = liveBerries.length - 1; k >= 0; k--) {
      var b = liveBerries[k];
      var bAge = Math.max(0, (t - b.birth) / 1000);
      if (bAge > 3) {
        drawBerryCluster(bctx, b, 1);
        liveBerries.splice(k, 1);
        continue;
      }
      drawBerryCluster(ctx, b, Math.min(1, bAge / 1.4));
    }
    // 常态补芽：保持画面始终有生命
    if (vines.length < 5 && totalSegments < MAX_SEGMENTS && Math.random() < 0.02) {
      spawnEdgeSprout();
    }
  }

  function spawnEdgeSprout() {
    var side = Math.random();
    if (side < 0.6) spawnVine(rand(0, W), H + 6, -Math.PI / 2 + rand(-0.5, 0.5), 0);        // 底边向上
    else if (side < 0.8) spawnVine(-6, rand(H * 0.35, H), rand(-0.6, 0.3), 0);              // 左边
    else spawnVine(W + 6, rand(H * 0.35, H), Math.PI + rand(-0.3, 0.6), 0);                 // 右边
  }

  // ---- 初始化 / 尺寸 ----
  function resize(keep) {
    var w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    W = w; H = h;
    canvas.width = Math.round(w * DPR);
    canvas.height = Math.round(h * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    var old = keep ? baked : null;
    var nb = document.createElement("canvas");
    nb.width = canvas.width; nb.height = canvas.height;
    var nc = nb.getContext("2d");
    nc.setTransform(DPR, 0, 0, DPR, 0, 0);
    if (old) nc.drawImage(old, 0, 0, W, H);
    baked = nb; bctx = nc;
    gridW = Math.ceil(W / CELL); gridH = Math.ceil(H / CELL);
    if (!keep || !grid) grid = new Uint16Array(gridW * gridH);
    else {
      var g = new Uint16Array(gridW * gridH);
      g.set(grid.subarray(0, Math.min(grid.length, g.length)));
      grid = g;
    }
  }

  // 预生长：同步跑 n 步，让页面打开时已有成型的藤（叶龄错开，加载后仍见新叶舒展）
  function preGrow(iters) {
    var t0 = performance.now();
    for (var f = 0; f < iters; f++) {
      for (var vi = vines.length - 1; vi >= 0; vi--) stepVine(vines[vi], 1);
      if (vines.length < 4 && Math.random() < 0.04) spawnEdgeSprout();
    }
    // 把预生长期的叶龄摊开：大部分直接定型烘焙，最近的一批保留舒展动画
    for (var li = liveLeaves.length - 1; li >= 0; li--) {
      var leaf = liveLeaves[li];
      var fake = rand(0, LEAF_LIVE_S * 2.2) * 1000;
      if (fake > LEAF_LIVE_S * 1000) {
        drawLeaf(bctx, leaf, 1, 0);
        liveLeaves.splice(li, 1);
        bakedLeaves++;
      } else {
        leaf.birth = t0 - fake;
      }
    }
    for (var bi = liveBerries.length - 1; bi >= 0; bi--) {
      drawBerryCluster(bctx, liveBerries[bi], 1);
      liveBerries.splice(bi, 1);
    }
  }

  function init() {
    resize(false);
    // 开场：底边多株 + 两侧各一株，页面一开始就是"活"的
    for (var i = 0; i < 6; i++) {
      spawnVine(rand(W * 0.04, W * 0.96), H + 6, -Math.PI / 2 + rand(-0.5, 0.5), 0);
    }
    spawnVine(-6, rand(H * 0.4, H * 0.8), rand(-0.5, 0.2), 0);
    spawnVine(W + 6, rand(H * 0.4, H * 0.8), Math.PI + rand(-0.2, 0.5), 0);
    var simMatch = location.search.match(/ivysim=(\d+)/);
    preGrow(simMatch ? parseInt(simMatch[1], 10) : 240);
    if (reduced) {
      // 无障碍：一次性静态长成，不跑动画不监听鼠标
      pointer.x = -1e4; pointer.y = -1e4;
      for (var f = 0; f < 900; f++) {
        for (var vi = vines.length - 1; vi >= 0; vi--) stepVine(vines[vi], 1);
        if (vines.length < 3 && Math.random() < 0.05) spawnEdgeSprout();
      }
      for (var li = 0; li < liveLeaves.length; li++) drawLeaf(bctx, liveLeaves[li], 1, 0);
      for (var bi = 0; bi < liveBerries.length; bi++) drawBerryCluster(bctx, liveBerries[bi], 1);
      liveLeaves = []; liveBerries = [];
      ctx.clearRect(0, 0, W, H);
      ctx.drawImage(baked, 0, 0, W, H);
      return;
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

  // 调试：URL 带 ?ivydebug 时把内部计数写进 title（headless 排查用）
  var bakedLeaves = 0;
  if (location.search.indexOf("ivydebug") >= 0) {
    setInterval(function () {
      document.title = "seg=" + totalSegments + " vines=" + vines.length +
        " liveLeaves=" + liveLeaves.length + " bakedLeaves=" + bakedLeaves +
        " W=" + W + " H=" + H;
    }, 400);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
