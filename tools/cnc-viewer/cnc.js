/* CNC CSV parsing, geometry and checks.
 *
 * Loaded as a plain script by index.html so the viewer runs from a
 * double-clicked file with no server, and exported for node so the same
 * code can be tested against real machine files.
 */
(function (root) {
  "use strict";

  var MM_PER_IN = 25.4;

  var OP_LIP_NOTCH = "LIP NOTCH";
  var OP_WEB_NOTCH = "WEB NOTCH";
  var OP_DIMPLE = "DIMPLE";
  var OP_SWAGE = "SWAGE";
  var OP_END_TRUSS = "END_TRUSS";

  var KNOWN_OPS = {};
  KNOWN_OPS[OP_LIP_NOTCH] = 1;
  KNOWN_OPS[OP_WEB_NOTCH] = 1;
  KNOWN_OPS[OP_DIMPLE] = 1;
  KNOWN_OPS[OP_SWAGE] = 1;
  KNOWN_OPS[OP_END_TRUSS] = 1;

  // Column order of a COMPONENT row.
  var COL = {
    label: 1,
    section: 2,
    role: 3,
    orient: 4,
    qty: 5,
    length: 6,
    x0: 7,
    y0: 8,
    x1: 9,
    y1: 10,
    webDepth: 11,
    firstOp: 12
  };

  function num(text) {
    if (text === undefined || text === null || text === "") return null;
    var v = parseFloat(text);
    return isNaN(v) ? null : v;
  }

  /* "362S162-43(33)" -> web 3.62in, flange 1.62in, 43mil, 33ksi.
   * The web matches the CSV's own web-depth column; the flange is what the
   * member measures in the panel elevation, which the CSV never states. */
  function parseSection(name) {
    var out = {
      name: name || "",
      webMm: null,
      flangeMm: null,
      mils: null,
      ksi: null,
      series: null
    };
    var m = /^(\d{3,4})([A-Za-z])(\d{2,4})-(\d+)(?:\((\d+)\))?/.exec(
      (name || "").replace(/\s+/g, "")
    );
    if (!m) return out;
    out.webMm = (parseInt(m[1], 10) / 100) * MM_PER_IN;
    out.series = m[2].toUpperCase();
    out.flangeMm = (parseInt(m[3], 10) / 100) * MM_PER_IN;
    out.mils = parseInt(m[4], 10);
    out.ksi = m[5] ? parseInt(m[5], 10) : null;
    return out;
  }

  function splitRow(line) {
    var fields = line.split(",");
    for (var i = 0; i < fields.length; i++) fields[i] = fields[i].trim();
    return fields;
  }

  function isBlankRow(line) {
    return line.replace(/[,\s]/g, "") === "";
  }

  /* Parse one CNC file. Never throws: anything unreadable becomes a warning
   * so the operator sees the problem instead of a blank screen. */
  function parseCnc(text, fileName) {
    var file = {
      fileName: fileName || "",
      job: "",
      members: [],
      warnings: []
    };
    var lines = String(text || "").split(/\r?\n/);

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];
      if (isBlankRow(line)) continue;
      var f = splitRow(line);
      var kind = (f[0] || "").toUpperCase();

      if (kind === "DETAILS") {
        for (var d = f.length - 1; d >= 1; d--) {
          if (f[d] !== "") {
            file.job = f[d];
            break;
          }
        }
        continue;
      }
      if (kind !== "COMPONENT") {
        file.warnings.push({
          level: "info",
          text: "Line " + (i + 1) + ": ignored unknown row type " + (f[0] || "(empty)")
        });
        continue;
      }

      var member = readComponent(f, i + 1, file.warnings);
      if (member) file.members.push(member);
    }

    if (!file.members.length) {
      file.warnings.push({
        level: "error",
        text: "No COMPONENT rows found - this does not look like a CNC file."
      });
    }
    checkFile(file);
    return file;
  }

  function readComponent(f, lineNo, warnings) {
    var label = f[COL.label] || "(unnamed)";
    var m = {
      lineNo: lineNo,
      label: label,
      section: f[COL.section] || "",
      role: f[COL.role] || "",
      orient: (f[COL.orient] || "").toUpperCase(),
      qty: num(f[COL.qty]) || 1,
      length: num(f[COL.length]),
      x0: num(f[COL.x0]),
      y0: num(f[COL.y0]),
      x1: num(f[COL.x1]),
      y1: num(f[COL.y1]),
      webDepth: num(f[COL.webDepth]),
      ops: []
    };
    m.sectionInfo = parseSection(m.section);

    var missing = [];
    ["length", "x0", "y0", "x1", "y1"].forEach(function (key) {
      if (m[key] === null) missing.push(key);
    });
    if (missing.length) {
      warnings.push({
        level: "error",
        member: label,
        text:
          "Line " + lineNo + " (" + label + "): missing " + missing.join(", ") +
          " - member skipped."
      });
      return null;
    }

    for (var i = COL.firstOp; i < f.length; i += 2) {
      var name = (f[i] || "").toUpperCase();
      if (name === "") continue;
      var station = num(f[i + 1]);
      if (station === null) {
        warnings.push({
          level: "error",
          member: label,
          text: label + ": operation " + name + " has no station and was dropped."
        });
        continue;
      }
      if (!KNOWN_OPS[name]) {
        warnings.push({
          level: "warn",
          member: label,
          text: label + ": unrecognised operation " + name + " kept as-is."
        });
      }
      m.ops.push({ op: name, station: station, index: m.ops.length });
    }

    // Unit vector along the member, from the (x0,y0) end the stations
    // are measured from.
    var dx = m.x1 - m.x0;
    var dy = m.y1 - m.y0;
    var span = Math.sqrt(dx * dx + dy * dy);
    m.span = span;
    if (span < 1e-6) {
      m.ux = 1;
      m.uy = 0;
    } else {
      m.ux = dx / span;
      m.uy = dy / span;
    }
    // Left normal: the side the C opens toward when the member is NORMAL.
    m.nx = -m.uy;
    m.ny = m.ux;
    m.openSign = m.orient === "INVERTED" ? -1 : 1;
    m.thickness = m.sectionInfo.flangeMm || 41.148;
    m.vertical = Math.abs(m.uy) > Math.abs(m.ux);
    return m;
  }

  function countOp(member, name) {
    var n = 0;
    for (var i = 0; i < member.ops.length; i++) {
      if (member.ops[i].op === name) n++;
    }
    return n;
  }

  /* Problems worth stopping the machine for. */
  function checkFile(file) {
    var seen = {};
    file.members.forEach(function (m) {
      if (seen[m.label]) {
        file.warnings.push({
          level: "warn",
          member: m.label,
          text: "Duplicate label " + m.label + " (lines " + seen[m.label] + " and " + m.lineNo + ")."
        });
      } else {
        seen[m.label] = m.lineNo;
      }

      if (Math.abs(m.span - m.length) > 1.5) {
        file.warnings.push({
          level: "warn",
          member: m.label,
          text:
            m.label + ": stated length " + m.length.toFixed(2) +
            " but its endpoints are " + m.span.toFixed(2) + " apart."
        });
      }
      if (!m.sectionInfo.flangeMm) {
        file.warnings.push({
          level: "warn",
          member: m.label,
          text:
            m.label + ": could not read section \"" + m.section +
            "\", drawn at a default 41.15mm width."
        });
      }
      if (m.webDepth && m.sectionInfo.webMm &&
          Math.abs(m.webDepth - m.sectionInfo.webMm) > 1.0) {
        file.warnings.push({
          level: "warn",
          member: m.label,
          text:
            m.label + ": web depth " + m.webDepth.toFixed(2) +
            " does not match section " + m.section + " (" +
            m.sectionInfo.webMm.toFixed(2) + ")."
        });
      }
      if (m.orient !== "NORMAL" && m.orient !== "INVERTED") {
        file.warnings.push({
          level: "warn",
          member: m.label,
          text: m.label + ": orientation \"" + m.orient + "\" is neither NORMAL nor INVERTED."
        });
      }
      if (!m.ops.length) {
        file.warnings.push({
          level: "warn",
          member: m.label,
          text: m.label + ": no operations, it will run as a plain cut length."
        });
      }
      m.ops.forEach(function (op) {
        if (op.station < -0.01 || op.station > m.length + 0.01) {
          file.warnings.push({
            level: "error",
            member: m.label,
            text:
              m.label + ": " + op.op + " at " + op.station.toFixed(2) +
              " falls outside the part (0 to " + m.length.toFixed(2) + ")."
          });
        }
      });
    });
  }

  /* Coil figures, in the same terms the machine reports them. */
  function summarise(members, wastePerPart) {
    var waste = wastePerPart === undefined || wastePerPart === null ? 0 : wastePerPart;
    var out = {
      parts: 0,
      cutLength: 0,
      waste: 0,
      totalLength: 0,
      dimples: 0,
      ops: {},
      sections: {}
    };
    members.forEach(function (m) {
      var qty = m.qty || 1;
      out.parts += qty;
      out.cutLength += m.length * qty;
      m.ops.forEach(function (op) {
        out.ops[op.op] = (out.ops[op.op] || 0) + qty;
      });
      var key = m.section || "(none)";
      if (!out.sections[key]) out.sections[key] = { parts: 0, length: 0 };
      out.sections[key].parts += qty;
      out.sections[key].length += m.length * qty;
    });
    out.dimples = out.ops[OP_DIMPLE] || 0;
    out.waste = waste * out.parts;
    out.totalLength = out.cutLength + out.waste;
    return out;
  }

  /* ---- geometry for drawing --------------------------------------- */

  function pointAt(member, station) {
    return {
      x: member.x0 + member.ux * station,
      y: member.y0 + member.uy * station
    };
  }

  /* The member as seen in the panel elevation: a rectangle of its flange
   * width centred on the centreline. */
  function outline(member) {
    var h = member.thickness / 2;
    var nx = member.nx * h;
    var ny = member.ny * h;
    return [
      { x: member.x0 + nx, y: member.y0 + ny },
      { x: member.x1 + nx, y: member.y1 + ny },
      { x: member.x1 - nx, y: member.y1 - ny },
      { x: member.x0 - nx, y: member.y0 - ny }
    ];
  }

  /* Centre of the web, i.e. the closed back of the C. Drawing it shows at a
   * glance which way the section faces. */
  function webLine(member) {
    var h = (member.thickness / 2) * -member.openSign;
    return [
      { x: member.x0 + member.nx * h, y: member.y0 + member.ny * h },
      { x: member.x1 + member.nx * h, y: member.y1 + member.ny * h }
    ];
  }

  function bounds(members) {
    var b = { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity };
    members.forEach(function (m) {
      outline(m).forEach(function (p) {
        if (p.x < b.minX) b.minX = p.x;
        if (p.y < b.minY) b.minY = p.y;
        if (p.x > b.maxX) b.maxX = p.x;
        if (p.y > b.maxY) b.maxY = p.y;
      });
    });
    if (!isFinite(b.minX)) return { minX: 0, minY: 0, maxX: 1000, maxY: 1000 };
    return b;
  }

  var api = {
    MM_PER_IN: MM_PER_IN,
    OP_LIP_NOTCH: OP_LIP_NOTCH,
    OP_WEB_NOTCH: OP_WEB_NOTCH,
    OP_DIMPLE: OP_DIMPLE,
    OP_SWAGE: OP_SWAGE,
    OP_END_TRUSS: OP_END_TRUSS,
    parseSection: parseSection,
    parseCnc: parseCnc,
    summarise: summarise,
    countOp: countOp,
    pointAt: pointAt,
    outline: outline,
    webLine: webLine,
    bounds: bounds
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.CNC = api;
})(typeof window !== "undefined" ? window : this);
