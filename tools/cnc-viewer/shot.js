/* node shot.js <csv> <out.png> [memberLabel]
 * Renders the viewer headlessly so the drawing can be eyeballed. Dev only. */
var fs = require("fs");
var path = require("path");
var cp = require("child_process");

var csvPath = process.argv[2];
var outPng = path.resolve(process.argv[3] || "shot.png");
var label = process.argv[4] || "";

var csv = fs.readFileSync(csvPath, "utf8");
var html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

var boot =
  "<script>window.addEventListener('load',function(){" +
  "CNCViewer.loadText(" + JSON.stringify(csv) + "," +
  JSON.stringify(path.basename(csvPath)) + ");" +
  (label ? "CNCViewer.select(" + JSON.stringify(label) + ");" : "") +
  "});<\/script>";
html = html.replace("</body>", boot + "</body>");

var tmp = path.join(__dirname, "_shot.html");
fs.writeFileSync(tmp, html);

var chrome = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
].filter(fs.existsSync)[0];

cp.execFileSync(chrome, [
  "--headless=new",
  "--disable-gpu",
  "--hide-scrollbars",
  "--virtual-time-budget=3000",
  "--window-size=1400,900",
  "--screenshot=" + outPng,
  "file:///" + tmp.replace(/\\/g, "/")
], { stdio: "inherit" });

fs.unlinkSync(tmp);
console.log("wrote " + outPng);
