/* node shot-detail.js <csv> <out.png> <label> <ox> <oy> <scale> [tab]
 * Zoomed render for checking that tooling marks land correctly. Dev only. */
var fs = require("fs");
var path = require("path");
var cp = require("child_process");

var a = process.argv;
var csv = fs.readFileSync(a[2], "utf8");
var out = path.resolve(a[3]);
var label = a[4];
var ox = a[5], oy = a[6], scale = a[7];
var tab = a[8] || "";

var html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
var boot =
  "<script>window.addEventListener('load',function(){" +
  "CNCViewer.loadText(" + JSON.stringify(csv) + ",'x.csv');" +
  "CNCViewer.select(" + JSON.stringify(label) + ");" +
  "CNCViewer.showStations(true);" +
  "CNCViewer.setView(" + ox + "," + oy + "," + scale + ");" +
  (tab ? "document.querySelector('[data-pane=" + tab + "]').click();" : "") +
  "});<\/script>";

var tmp = path.join(__dirname, "_shot2.html");
fs.writeFileSync(tmp, html.replace("</body>", boot + "</body>"));

cp.execFileSync("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", [
  "--headless=new", "--disable-gpu", "--hide-scrollbars",
  "--virtual-time-budget=3000", "--window-size=1400,900",
  "--screenshot=" + out,
  "file:///" + tmp.replace(/\\/g, "/")
], { stdio: "inherit" });

fs.unlinkSync(tmp);
console.log("wrote " + out);
