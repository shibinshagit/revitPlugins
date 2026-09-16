/* node test-cnc.js  - checks the parser against real machine files. */
var fs = require("fs");
var path = require("path");
var CNC = require("./cnc.js");

var CSV_DIR = path.join(
  "D:", "SHIBIN SHA", "MULK-ECOSYSTEM", "Models added on revit",
  "For CNC Script on Revit"
);

var failures = 0;
function check(name, got, want) {
  var ok = Math.abs(got - want) < 0.01;
  if (!ok) failures++;
  console.log(
    (ok ? "  ok   " : "  FAIL ") + name +
    "  got " + got + (ok ? "" : "  want " + want)
  );
}

function load(rel) {
  var full = path.join(CSV_DIR, rel);
  return CNC.parseCnc(fs.readFileSync(full, "utf8"), path.basename(full));
}

console.log("section decoding");
var s = CNC.parseSection("362S162-43(33)");
check("web mm", s.webMm, 91.948);
check("flange mm", s.flangeMm, 41.148);
check("mils", s.mils, 43);
check("ksi", s.ksi, 33);
var t = CNC.parseSection("600T125-54");
check("600T125 web", t.webMm, 152.4);
check("600T125 leg", t.flangeMm, 31.75);

console.log("\nwall panel: WP-70 from Vertex BD");
var wp = load(path.join("CNC", "WP-70_362S162-43(33)_Edited.csv"));
console.log("  job      :", JSON.stringify(wp.job));
console.log("  members  :", wp.members.length);
check("member count", wp.members.length, 8);

var e1 = wp.members[0];
console.log("  first    :", e1.label, e1.role, e1.orient, "len", e1.length,
            "ops", e1.ops.length);
check("E1 length", e1.length, 2796.12);
check("E1 x0", e1.x0, 20.57);
check("E1 y1", e1.y1, 2797);
check("E1 web depth", e1.webDepth, 91.95);
check("E1 op count", e1.ops.length, 8);
check("E1 first station", e1.ops[0].station, 19.7);
console.log("  E1 ops   :", e1.ops.map(function (o) {
  return o.op + "@" + o.station;
}).join(", "));

var hb1 = wp.members.filter(function (m) { return m.label === "W70-HB1"; })[0];
check("HB1 web notches", CNC.countOp(hb1, CNC.OP_WEB_NOTCH), 2);
check("HB1 span vs length", hb1.span, hb1.length);
console.log("  HB1 vertical?", hb1.vertical, "(expected false)");

var tbot = wp.members.filter(function (m) { return m.label === "W70-TBOT1"; })[0];
console.log("  TBOT     : orient", tbot.orient,
            "-> opens toward", tbot.openSign > 0 ? "+normal" : "-normal");
var web = CNC.webLine(tbot);
console.log("  TBOT web y=" + web[0].y.toFixed(2) +
            " (below centreline " + tbot.y0 + " = closed side down)");
if (web[0].y >= tbot.y0) { console.log("  FAIL bottom track web should sit below its centreline"); failures++; }

console.log("\nfloor truss: FT-1 from Vertex BD");
var ft = load(path.join("Example", "FT-1_362S162-43(50)_Edited.csv"));
check("FT-1 members", ft.members.length, 7);
var webMember = ft.members.filter(function (m) { return m.label === "WB3-5"; })[0];
console.log("  WB3-5    : len", webMember.length, "sloped?",
            Math.abs(webMember.uy) > 0.01 && Math.abs(webMember.ux) > 0.01);
check("WB3-5 end truss ops", CNC.countOp(webMember, CNC.OP_END_TRUSS), 2);
check("WB3-5 span vs length", webMember.span, webMember.length);
var tc = ft.members.filter(function (m) { return m.label === "TC3-4"; })[0];
console.log("  TC3-4    : orient", tc.orient, "-> web sits at y=" +
            CNC.webLine(tc)[0].y.toFixed(2) + " (above centreline " + tc.y0 + ")");

console.log("\ncoil summary (WP-70, 11mm waste per part)");
var sum = CNC.summarise(wp.members, 11);
console.log("  parts    :", sum.parts);
console.log("  cut      :", sum.cutLength.toFixed(2), "mm");
console.log("  waste    :", sum.waste.toFixed(2), "mm");
console.log("  total    :", sum.totalLength.toFixed(2), "mm");
console.log("  dimples  :", sum.dimples);
console.log("  ops      :", JSON.stringify(sum.ops));

console.log("\nmachine screenshot arithmetic (5 parts: 2x1521.70 + 3x455.61)");
var fake = [
  { label: "W1-E1", qty: 1, length: 1521.7, section: "362S162-43(33)", ops: [] },
  { label: "W1-E2", qty: 1, length: 1521.7, section: "362S162-43(33)", ops: [] },
  { label: "W1-HB1", qty: 1, length: 455.61, section: "362S162-43(33)", ops: [] },
  { label: "W1-TBOT1", qty: 1, length: 455.61, section: "362S162-43(33)", ops: [] },
  { label: "W1-TTOP1", qty: 1, length: 455.61, section: "362S162-43(33)", ops: [] }
];
var f0 = CNC.summarise(fake, 0);
console.log("  sum of lengths     :", f0.cutLength.toFixed(2));
console.log("  machine total shown: 4465.23  -> implies waste of",
            (4465.23 - f0.cutLength).toFixed(2), "mm over 5 parts =",
            ((4465.23 - f0.cutLength) / 5).toFixed(2), "mm per part");

console.log("\nbad input is reported, not thrown");
var broken = CNC.parseCnc(
  "DETAILS,,Job\n" +
  "COMPONENT,BAD1,362S162-43(33),SV,SIDEWAYS,1,1000,0,0,0,1000,91.95,DIMPLE,5000\n" +
  "COMPONENT,BAD2,nonsense,SV,NORMAL,1,,0,0,0,100,91.95\n" +
  "GARBAGE,row\n",
  "broken.csv"
);
console.log("  members kept:", broken.members.length, "(BAD2 dropped for no length)");
broken.warnings.forEach(function (w) {
  console.log("  [" + w.level + "] " + w.text);
});
check("broken kept 1 member", broken.members.length, 1);

var empty = CNC.parseCnc("", "empty.csv");
console.log("  empty file ->", empty.warnings.length, "warning(s):",
            empty.warnings[0] && empty.warnings[0].text);

console.log(failures ? "\n" + failures + " CHECK(S) FAILED" : "\nall checks passed");
process.exit(failures ? 1 : 0);
