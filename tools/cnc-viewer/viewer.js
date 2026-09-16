/* Drawing and interaction for the CNC viewer. Depends on cnc.js. */
(function () {
  "use strict";

  var OP_COLOUR = {};
  OP_COLOUR[CNC.OP_DIMPLE] = "#1565c0";
  OP_COLOUR[CNC.OP_SWAGE] = "#e07b00";
  OP_COLOUR[CNC.OP_LIP_NOTCH] = "#7b1fa2";
  OP_COLOUR[CNC.OP_WEB_NOTCH] = "#c62828";
  OP_COLOUR[CNC.OP_END_TRUSS] = "#00796b";
  var OP_OTHER = "#455a64";

  function colourOf(op) {
    return OP_COLOUR[op] || OP_OTHER;
  }

  var MEMBER_LINE = "#6b7a2f";
  var MEMBER_FILL = "#f4f6ea";
  var WEB_LINE = "#3f4a1a";
  var SEL_LINE = "#c62828";
  var SEL_FILL = "#fdeceb";

  var files = [];
  var active = null;
  var selected = null;
  var view = { ox: 0, oy: 0, scale: 1 };
  var needsFit = false;
  var el = {};
  var dpr = window.devicePixelRatio || 1;

  function $(id) { return document.getElementById(id); }

  function fmt(v, dp) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    return v.toFixed(dp === undefined ? 2 : dp);
  }

  /* ---- view transform ---------------------------------------------- */

  var PAD = 46;

  function toScreen(x, y) {
    return {
      x: PAD + (x - view.ox) * view.scale,
      y: el.plot.clientHeight - PAD - (y - view.oy) * view.scale
    };
  }

  function toModel(sx, sy) {
    return {
      x: (sx - PAD) / view.scale + view.ox,
      y: (el.plot.clientHeight - PAD - sy) / view.scale + view.oy
    };
  }

  function fit() {
    if (!active || !active.members.length) return;
    // The canvas may not have been laid out yet; try again once it has.
    if (el.plot.clientWidth < 120 || el.plot.clientHeight < 120) {
      needsFit = true;
      return;
    }
    needsFit = false;
    var b = CNC.bounds(active.members);
    var w = Math.max(b.maxX - b.minX, 1);
    var h = Math.max(b.maxY - b.minY, 1);
    var availW = Math.max(el.plot.clientWidth - PAD * 2, 50);
    var availH = Math.max(el.plot.clientHeight - PAD * 2, 50);
    view.scale = Math.min(availW / w, availH / h) * 0.94;
    // centre it
    view.ox = b.minX - (availW / view.scale - w) / 2;
    view.oy = b.minY - (availH / view.scale - h) / 2;
    draw();
  }

  function zoomBy(factor, cx, cy) {
    if (cx === undefined) {
      cx = el.plot.clientWidth / 2;
      cy = el.plot.clientHeight / 2;
    }
    var before = toModel(cx, cy);
    view.scale *= factor;
    var after = toModel(cx, cy);
    view.ox += before.x - after.x;
    view.oy += before.y - after.y;
    draw();
  }

  /* ---- drawing ----------------------------------------------------- */

  function resize() {
    var w = el.plot.clientWidth;
    var h = el.plot.clientHeight;
    el.plot.width = Math.round(w * dpr);
    el.plot.height = Math.round(h * dpr);
    var ctx = el.plot.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (needsFit) fit();
    else draw();
  }

  function niceStep(rawStep) {
    var pow = Math.pow(10, Math.floor(Math.log(rawStep) / Math.LN10));
    var candidates = [1, 2, 5, 10];
    for (var i = 0; i < candidates.length; i++) {
      if (candidates[i] * pow >= rawStep) return candidates[i] * pow;
    }
    return 10 * pow;
  }

  function drawGrid(ctx, w, h) {
    var step = niceStep(90 / view.scale);
    var min = toModel(PAD, h - PAD);
    var max = toModel(w, 0);

    ctx.lineWidth = 1;
    ctx.font = "10px Segoe UI, sans-serif";
    ctx.fillStyle = "#9aa7b3";

    ctx.beginPath();
    ctx.strokeStyle = "#eef1f4";
    var x0 = Math.floor(min.x / step) * step;
    for (var x = x0; x <= max.x; x += step) {
      var p = toScreen(x, 0);
      ctx.moveTo(p.x, 0);
      ctx.lineTo(p.x, h);
    }
    var y0 = Math.floor(min.y / step) * step;
    for (var y = y0; y <= max.y; y += step) {
      var q = toScreen(0, y);
      ctx.moveTo(0, q.y);
      ctx.lineTo(w, q.y);
    }
    ctx.stroke();

    // axis ticks along the bottom and left edges
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    for (var xt = x0; xt <= max.x; xt += step) {
      var pt = toScreen(xt, 0);
      if (pt.x < PAD - 10 || pt.x > w) continue;
      ctx.fillText(String(Math.round(xt)), pt.x, h - PAD + 5);
    }
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    for (var yt = y0; yt <= max.y; yt += step) {
      var qt = toScreen(0, yt);
      if (qt.y < 0 || qt.y > h - PAD + 10) continue;
      ctx.fillText(String(Math.round(yt)), PAD - 6, qt.y);
    }

    // panel origin
    var o = toScreen(0, 0);
    ctx.strokeStyle = "#c3ccd5";
    ctx.beginPath();
    ctx.moveTo(PAD, o.y);
    ctx.lineTo(w, o.y);
    ctx.moveTo(o.x, 0);
    ctx.lineTo(o.x, h - PAD);
    ctx.stroke();
  }

  function drawMember(ctx, m, isSel) {
    var poly = CNC.outline(m);
    ctx.beginPath();
    poly.forEach(function (p, i) {
      var s = toScreen(p.x, p.y);
      if (i === 0) ctx.moveTo(s.x, s.y);
      else ctx.lineTo(s.x, s.y);
    });
    ctx.closePath();
    ctx.fillStyle = isSel ? SEL_FILL : MEMBER_FILL;
    ctx.fill();
    ctx.lineWidth = isSel ? 2 : 1;
    ctx.strokeStyle = isSel ? SEL_LINE : MEMBER_LINE;
    ctx.stroke();

    // web (closed back of the C) shows which way the section faces
    var web = CNC.webLine(m);
    var a = toScreen(web[0].x, web[0].y);
    var b = toScreen(web[1].x, web[1].y);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.lineWidth = isSel ? 3 : 2;
    ctx.strokeStyle = isSel ? SEL_LINE : WEB_LINE;
    ctx.stroke();
  }

  function drawOps(ctx, m) {
    var half = (m.thickness / 2) * view.scale;
    var showStations = el.showstations.checked;

    m.ops.forEach(function (op) {
      var p = CNC.pointAt(m, op.station);
      var s = toScreen(p.x, p.y);
      // The model's left normal (-uy, ux) seen on screen, where y points
      // down: (-uy, -ux). Getting this sign wrong mirrors the lip notches
      // onto the closed side of the section.
      var nx = -m.uy;
      var ny = -m.ux;

      ctx.strokeStyle = ctx.fillStyle = colourOf(op.op);

      if (op.op === CNC.OP_DIMPLE) {
        ctx.beginPath();
        ctx.arc(s.x, s.y, Math.max(2, Math.min(4, half * 0.22)), 0, Math.PI * 2);
        ctx.fill();
      } else if (op.op === CNC.OP_SWAGE) {
        // a flattened band across the full width
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(s.x + nx * half, s.y + ny * half);
        ctx.lineTo(s.x - nx * half, s.y - ny * half);
        ctx.stroke();
      } else if (op.op === CNC.OP_WEB_NOTCH) {
        // cut across the whole section
        ctx.lineWidth = 3;
        ctx.beginPath();
        ctx.moveTo(s.x + nx * half, s.y + ny * half);
        ctx.lineTo(s.x - nx * half, s.y - ny * half);
        ctx.stroke();
      } else if (op.op === CNC.OP_LIP_NOTCH) {
        // A tick straddling the open-side edge, where the lips are. It
        // reaches outside the section so a web notch at the same station
        // cannot hide it.
        var sign = m.openSign;
        var inner = half * 0.55;
        var outer = half + Math.max(3, half * 0.35);
        ctx.lineWidth = 2.5;
        ctx.beginPath();
        ctx.moveTo(s.x + nx * inner * sign, s.y + ny * inner * sign);
        ctx.lineTo(s.x + nx * outer * sign, s.y + ny * outer * sign);
        ctx.stroke();
      } else if (op.op === CNC.OP_END_TRUSS) {
        // angled end cut
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(s.x + nx * half, s.y + ny * half);
        ctx.lineTo(s.x - nx * half + m.ux * 8, s.y - ny * half - m.uy * 8);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.arc(s.x, s.y, 3, 0, Math.PI * 2);
        ctx.stroke();
      }

      if (showStations) {
        ctx.font = "9px Segoe UI, sans-serif";
        ctx.textAlign = "left";
        ctx.textBaseline = "bottom";
        ctx.fillText(fmt(op.station), s.x + 4, s.y - 3);
      }
    });
  }

  function drawLabel(ctx, m, isSel) {
    var mid = CNC.pointAt(m, m.length / 2);
    var s = toScreen(mid.x, mid.y);
    var off = (m.thickness / 2) * view.scale + 9;
    var sign = -m.openSign;
    var nx = m.nx * sign;
    var ny = -m.ny * sign;
    ctx.font = (isSel ? "600 " : "") + "11px Segoe UI, sans-serif";
    ctx.fillStyle = isSel ? SEL_LINE : "#4a5b17";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    var tx = s.x + nx * off;
    var ty = s.y + ny * off;
    if (m.vertical) {
      ctx.save();
      ctx.translate(tx, ty);
      ctx.rotate(-Math.PI / 2);
      ctx.fillText(m.label, 0, 0);
      ctx.restore();
    } else {
      ctx.fillText(m.label, tx, ty);
    }
  }

  function draw() {
    var ctx = el.plot.getContext("2d");
    var w = el.plot.clientWidth;
    var h = el.plot.clientHeight;
    ctx.clearRect(0, 0, w, h);
    if (!active || !active.members.length) return;

    drawGrid(ctx, w, h);

    active.members.forEach(function (m) {
      if (m !== selected) drawMember(ctx, m, false);
    });
    if (selected) drawMember(ctx, selected, true);

    if (el.showops.checked) {
      active.members.forEach(function (m) { drawOps(ctx, m); });
    }
    if (el.showlabels.checked) {
      active.members.forEach(function (m) { drawLabel(ctx, m, m === selected); });
    }
  }

  /* ---- straightened single part ------------------------------------ */

  function renderPart() {
    var host = el.paneP;
    if (!selected) {
      host.innerHTML = '<div style="padding:10px;color:#6b7885">' +
        "Pick a member to see the part as it leaves the machine.</div>";
      return;
    }
    var m = selected;
    var W = 344;
    var margin = 12;
    var usable = W - margin * 2;
    var depth = m.webDepth || (m.sectionInfo.webMm || 91.95);
    var scale = usable / m.length;
    var barH = Math.max(18, Math.min(70, depth * scale * 2.2));
    var rows = Math.ceil(m.ops.length) ? m.ops.length : 0;
    var H = barH + 66;

    var parts = [];
    parts.push('<div style="padding:8px 10px">');
    parts.push('<div style="font-weight:600;margin-bottom:2px">' + esc(m.label) + "</div>");
    parts.push('<div class="chip">' + esc(m.section) + "</div> ");
    parts.push('<div class="chip">' + esc(m.role) + "</div> ");
    parts.push('<div class="chip">' + esc(m.orient) + "</div>");
    parts.push('<div style="margin:6px 0 2px;color:#5b6875">cut length ' +
      fmt(m.length) + " mm &middot; web " + fmt(depth) + " mm &middot; " +
      m.ops.length + " operations</div>");

    // flat blank with stations marked
    parts.push('<svg width="' + W + '" height="' + H + '" style="display:block">');
    var y = 26;
    parts.push('<rect x="' + margin + '" y="' + y + '" width="' + usable +
      '" height="' + barH + '" fill="#f4f6ea" stroke="' + MEMBER_LINE + '"/>');
    // lip lines
    var lip = Math.min(6, barH / 4);
    parts.push('<line x1="' + margin + '" y1="' + (y + lip) + '" x2="' +
      (margin + usable) + '" y2="' + (y + lip) + '" stroke="#c2cba0"/>');
    parts.push('<line x1="' + margin + '" y1="' + (y + barH - lip) + '" x2="' +
      (margin + usable) + '" y2="' + (y + barH - lip) + '" stroke="#c2cba0"/>');

    m.ops.forEach(function (op, i) {
      var x = margin + Math.max(0, Math.min(m.length, op.station)) * scale;
      var c = colourOf(op.op);
      if (op.op === CNC.OP_DIMPLE) {
        parts.push('<circle cx="' + x.toFixed(1) + '" cy="' + (y + barH / 2) +
          '" r="3" fill="' + c + '"/>');
      } else if (op.op === CNC.OP_LIP_NOTCH) {
        parts.push('<line x1="' + x.toFixed(1) + '" y1="' + y + '" x2="' +
          x.toFixed(1) + '" y2="' + (y + lip) + '" stroke="' + c + '" stroke-width="2.5"/>');
        parts.push('<line x1="' + x.toFixed(1) + '" y1="' + (y + barH - lip) +
          '" x2="' + x.toFixed(1) + '" y2="' + (y + barH) + '" stroke="' + c +
          '" stroke-width="2.5"/>');
      } else {
        var wd = op.op === CNC.OP_WEB_NOTCH ? 3 : 2;
        parts.push('<line x1="' + x.toFixed(1) + '" y1="' + y + '" x2="' +
          x.toFixed(1) + '" y2="' + (y + barH) + '" stroke="' + c +
          '" stroke-width="' + wd + '"/>');
      }
      // alternating tick labels above/below so they do not collide
      var ty = i % 2 ? y - 4 : y + barH + 11;
      parts.push('<text x="' + x.toFixed(1) + '" y="' + ty +
        '" font-size="8" fill="#6b7885" text-anchor="middle">' +
        fmt(op.station, 1) + "</text>");
    });
    parts.push('<text x="' + margin + '" y="' + (y + barH + 26) +
      '" font-size="9" fill="#9aa7b3">0</text>');
    parts.push('<text x="' + (margin + usable) + '" y="' + (y + barH + 26) +
      '" font-size="9" fill="#9aa7b3" text-anchor="end">' + fmt(m.length) + "</text>");
    parts.push("</svg>");

    parts.push('<div style="margin-top:6px;color:#5b6875">runs from (' +
      fmt(m.x0) + ", " + fmt(m.y0) + ") to (" + fmt(m.x1) + ", " + fmt(m.y1) +
      ") in the panel</div>");
    parts.push("</div>");
    host.innerHTML = parts.join("");
  }

  /* ---- tables ------------------------------------------------------ */

  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function renderList() {
    if (!active) { el.paneL.innerHTML = ""; return; }
    var rows = active.members.map(function (m, i) {
      return '<tr data-i="' + i + '"' + (m === selected ? ' class="sel"' : "") + ">" +
        "<td>" + esc(m.label) + "</td>" +
        '<td class="n">' + fmt(m.length) + "</td>" +
        "<td>" + esc(m.role) + "</td>" +
        "<td>" + (m.orient === "INVERTED" ? "I" : m.orient === "NORMAL" ? "N" : "?") + "</td>" +
        '<td class="n">' + m.ops.length + "</td>" +
        "</tr>";
    }).join("");
    el.paneL.innerHTML =
      "<table><thead><tr><th>number</th><th class='n'>Length</th>" +
      "<th>role</th><th>O</th><th class='n'>ops</th></tr></thead><tbody>" +
      rows + "</tbody></table>";
    bindRows(el.paneL);
  }

  function renderActions() {
    if (!selected) {
      el.paneA.innerHTML = '<div style="padding:10px;color:#6b7885">' +
        "Pick a member in the workpiece list.</div>";
      return;
    }
    var m = selected;
    var rows = m.ops.map(function (op, i) {
      var bad = op.station < -0.01 || op.station > m.length + 0.01;
      return "<tr" + (bad ? ' style="background:#fdf0ef"' : "") + ">" +
        '<td class="n">' + (i + 1) + "</td>" +
        '<td><span style="color:' + colourOf(op.op) + '">&#9632;</span> ' + esc(op.op) + "</td>" +
        '<td class="n">' + fmt(op.station) + "</td>" +
        '<td class="n">' + fmt(m.length - op.station) + "</td>" +
        "</tr>";
    }).join("");
    el.paneA.innerHTML =
      '<div style="padding:6px 8px;font-weight:600">' + esc(m.label) +
      ' <span class="chip">' + esc(m.orient) + "</span></div>" +
      "<table><thead><tr><th class='n'>#</th><th>action</th>" +
      "<th class='n'>from start</th><th class='n'>from end</th></tr></thead><tbody>" +
      (rows || '<tr><td colspan="4" style="color:#6b7885">no operations</td></tr>') +
      "</tbody></table>";
  }

  function bindRows(host) {
    var trs = host.querySelectorAll("tbody tr[data-i]");
    for (var i = 0; i < trs.length; i++) {
      trs[i].addEventListener("click", function () {
        select(active.members[parseInt(this.getAttribute("data-i"), 10)]);
      });
    }
  }

  function renderSummary() {
    if (!active) {
      el.summary.innerHTML = '<span class="muted">No file loaded.</span>';
      return;
    }
    var waste = parseFloat(el.waste.value);
    if (isNaN(waste)) waste = 0;
    var s = CNC.summarise(active.members, waste);
    var secs = Object.keys(s.sections).map(function (k) {
      return esc(k) + " " + fmt(s.sections[k].length) + "mm";
    }).join(" &middot; ");
    var opBits = Object.keys(s.ops).sort().map(function (k) {
      return '<span style="color:' + colourOf(k) + '">&#9632;</span> ' +
        esc(k.toLowerCase()) + " " + s.ops[k];
    }).join(" &nbsp; ");
    el.summary.innerHTML =
      "Length " + fmt(s.totalLength) + " mm, waste " + fmt(s.waste) +
      " mm, dimple " + s.dimples +
      ' <span class="muted">&nbsp;|&nbsp; ' + s.parts + " parts, cut " +
      fmt(s.cutLength) + " mm &nbsp;|&nbsp; " + opBits +
      " &nbsp;|&nbsp; " + secs + " &nbsp;|&nbsp; " + esc(active.job) + "</span>";
  }

  function renderIssues() {
    if (!active) { el.issues.innerHTML = ""; return; }
    var ws = active.warnings;
    if (!ws.length) {
      el.issues.innerHTML = '<div class="none">No problems found in ' +
        esc(active.fileName) + ".</div>";
      return;
    }
    var order = { error: 0, warn: 1, info: 2 };
    ws = ws.slice().sort(function (a, b) { return order[a.level] - order[b.level]; });
    el.issues.innerHTML = ws.map(function (w) {
      return '<div class="' + w.level + '">' + esc(w.text) + "</div>";
    }).join("");
  }

  function renderAll() {
    renderSummary();
    renderList();
    renderActions();
    renderPart();
    renderIssues();
  }

  function select(m) {
    selected = m || null;
    renderList();
    renderActions();
    renderPart();
    draw();
  }

  /* ---- files ------------------------------------------------------- */

  function loadFiles(fileList) {
    var pending = fileList.length;
    if (!pending) return;
    Array.prototype.forEach.call(fileList, function (f) {
      var reader = new FileReader();
      reader.onload = function () {
        var parsed = CNC.parseCnc(reader.result, f.name);
        var existing = -1;
        for (var i = 0; i < files.length; i++) {
          if (files[i].fileName === parsed.fileName) existing = i;
        }
        if (existing >= 0) files[existing] = parsed;
        else files.push(parsed);
        if (--pending === 0) {
          refreshFileSelect();
          setActive(parsed);
        }
      };
      reader.onerror = function () {
        if (--pending === 0) refreshFileSelect();
      };
      reader.readAsText(f);
    });
  }

  function refreshFileSelect() {
    el.filesel.innerHTML = files.map(function (f, i) {
      return '<option value="' + i + '">' + esc(f.fileName) +
        " (" + f.members.length + ")</option>";
    }).join("");
    el.filesel.style.display = files.length > 1 ? "" : "none";
  }

  function setActive(f) {
    active = f;
    selected = null;
    for (var i = 0; i < files.length; i++) {
      if (files[i] === f) el.filesel.value = String(i);
    }
    el.hint.style.display = active && active.members.length ? "none" : "";
    renderAll();
    fit();
  }

  /* ---- hit testing ------------------------------------------------- */

  function memberAt(sx, sy) {
    if (!active) return null;
    var p = toModel(sx, sy);
    var best = null;
    var bestDist = Infinity;
    active.members.forEach(function (m) {
      // distance from the point to the member centreline segment
      var vx = m.x1 - m.x0, vy = m.y1 - m.y0;
      var len2 = vx * vx + vy * vy || 1;
      var t = ((p.x - m.x0) * vx + (p.y - m.y0) * vy) / len2;
      t = Math.max(0, Math.min(1, t));
      var cx = m.x0 + vx * t, cy = m.y0 + vy * t;
      var d = Math.sqrt((p.x - cx) * (p.x - cx) + (p.y - cy) * (p.y - cy));
      var reach = m.thickness / 2 + 6 / view.scale;
      if (d < reach && d < bestDist) { bestDist = d; best = m; }
    });
    return best;
  }

  /* ---- wiring ------------------------------------------------------ */

  function legend() {
    var items = [
      [CNC.OP_DIMPLE, "dimple"],
      [CNC.OP_SWAGE, "swage"],
      [CNC.OP_LIP_NOTCH, "lip notch"],
      [CNC.OP_WEB_NOTCH, "web notch"],
      [CNC.OP_END_TRUSS, "end truss"]
    ];
    el.legend.innerHTML = items.map(function (it) {
      return "<span><i style='background:" + colourOf(it[0]) + "'></i>" + it[1] + "</span>";
    }).join("");
  }

  function init() {
    el.plot = $("plot");
    el.hint = $("hint");
    el.summary = $("summary");
    el.issues = $("issues");
    el.paneL = $("pane-list");
    el.paneA = $("pane-actions");
    el.paneP = $("pane-part");
    el.filesel = $("filesel");
    el.waste = $("waste");
    el.legend = $("legend");
    el.showops = $("showops");
    el.showlabels = $("showlabels");
    el.showstations = $("showstations");
    el.readout = $("readout");

    legend();
    refreshFileSelect();

    $("open").addEventListener("click", function () { $("file").click(); });
    $("file").addEventListener("change", function () {
      loadFiles(this.files);
      this.value = "";
    });
    el.filesel.addEventListener("change", function () {
      setActive(files[parseInt(this.value, 10)]);
    });
    el.waste.addEventListener("input", renderSummary);
    $("fit").addEventListener("click", fit);
    $("zin").addEventListener("click", function () { zoomBy(1.25); });
    $("zout").addEventListener("click", function () { zoomBy(0.8); });
    [el.showops, el.showlabels, el.showstations].forEach(function (cb) {
      cb.addEventListener("change", draw);
    });

    var tabs = document.querySelectorAll(".tabs button");
    Array.prototype.forEach.call(tabs, function (btn) {
      btn.addEventListener("click", function () {
        Array.prototype.forEach.call(tabs, function (b) { b.classList.remove("on"); });
        btn.classList.add("on");
        ["list", "actions", "part"].forEach(function (name) {
          $("pane-" + name).classList.toggle("on", name === btn.getAttribute("data-pane"));
        });
      });
    });

    // pan, zoom, pick
    var dragging = false, lastX = 0, lastY = 0, moved = 0;
    el.plot.addEventListener("mousedown", function (e) {
      dragging = true; moved = 0; lastX = e.clientX; lastY = e.clientY;
    });
    window.addEventListener("mouseup", function (e) {
      if (dragging && moved < 4) {
        var r = el.plot.getBoundingClientRect();
        select(memberAt(e.clientX - r.left, e.clientY - r.top));
      }
      dragging = false;
    });
    el.plot.addEventListener("mousemove", function (e) {
      var r = el.plot.getBoundingClientRect();
      var sx = e.clientX - r.left, sy = e.clientY - r.top;
      if (dragging) {
        var dx = e.clientX - lastX, dy = e.clientY - lastY;
        moved += Math.abs(dx) + Math.abs(dy);
        view.ox -= dx / view.scale;
        view.oy += dy / view.scale;
        lastX = e.clientX; lastY = e.clientY;
        draw();
      }
      if (!active) { el.readout.textContent = ""; return; }
      var p = toModel(sx, sy);
      var hit = memberAt(sx, sy);
      el.readout.textContent = "x " + fmt(p.x, 1) + "  y " + fmt(p.y, 1) +
        (hit ? "   -   " + hit.label + " (" + fmt(hit.length) + " mm)" : "");
    });
    el.plot.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = el.plot.getBoundingClientRect();
      zoomBy(e.deltaY < 0 ? 1.12 : 0.89, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    // drag and drop
    ["dragenter", "dragover"].forEach(function (t) {
      window.addEventListener(t, function (e) {
        e.preventDefault();
        document.body.classList.add("drag");
      });
    });
    ["dragleave", "drop"].forEach(function (t) {
      window.addEventListener(t, function (e) {
        e.preventDefault();
        if (t === "drop" && e.dataTransfer && e.dataTransfer.files) {
          loadFiles(e.dataTransfer.files);
        }
        document.body.classList.remove("drag");
      });
    });

    window.addEventListener("resize", resize);
    if (window.ResizeObserver) {
      new ResizeObserver(resize).observe($("plotwrap"));
    }
    resize();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Small hook so the viewer can be driven without a file dialog.
  window.CNCViewer = {
    loadText: function (text, name) {
      var parsed = CNC.parseCnc(text, name || "pasted.csv");
      files.push(parsed);
      refreshFileSelect();
      setActive(parsed);
      return parsed;
    },
    select: function (label) {
      if (!active) return null;
      for (var i = 0; i < active.members.length; i++) {
        if (active.members[i].label === label) {
          select(active.members[i]);
          return active.members[i];
        }
      }
      return null;
    },
    showStations: function (on) {
      el.showstations.checked = !!on;
      draw();
    },
    setView: function (ox, oy, scale) {
      view.ox = ox; view.oy = oy; view.scale = scale;
      needsFit = false;
      draw();
    },
    debug: function () {
      return {
        canvas: [el.plot.clientWidth, el.plot.clientHeight],
        bitmap: [el.plot.width, el.plot.height],
        view: { ox: view.ox, oy: view.oy, scale: view.scale },
        needsFit: needsFit,
        bounds: active ? CNC.bounds(active.members) : null
      };
    }
  };
})();
