# -*- coding: utf-8 -*-
"""Drive the Generate CNC window outside Revit and screenshot it.

Run through run_window_check.ps1, which hosts pyRevit's IronPython 2.7 engine
and provides LIB, SAMPLES, OUT, LOADER and MODULES.

The provider below stands in for the Revit exporter: it returns the two
production CSV files as the programs. Where it adds Revit cut lengths they
are test values, there only to exercise the comparison columns.

The window is driven twice: first as a dialog, where model calls run on the
spot, then as the tool window that stays open beside Revit, where every model
call waits for Revit. In the second part a runner that holds the work until
the test releases it stands in for Revit.
"""
import sys

import clr

# pyRevit ships the Python standard library inside pyRevitLoader.dll.
clr.AddReference(MODULES)
from IronPython.Modules import ResourceMetaPathImporter

sys.meta_path.append(
    ResourceMetaPathImporter(LOADER, "pyRevitLoader.python_2712pr_lib.zip")
)
sys.path.insert(0, LIB)

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")

from System import Action
from System.IO import Directory, File, FileAccess, FileMode, FileShare, FileStream, Path
from System.Threading import Thread
from System.Windows import Int32Rect, Point, RoutedEventArgs
from System.Windows.Controls.Primitives import ButtonBase
from System.Windows.Input import Key
from System.Windows.Media import PixelFormats
from System.Windows.Media.Imaging import (
    BitmapFrame,
    CroppedBitmap,
    PngBitmapEncoder,
    RenderTargetBitmap,
)
from System.Windows.Threading import DispatcherPriority

import uniqube_cnc_files as files
import uniqube_cnc_preview as preview
import uniqube_cnc_window as cnc_window

FAILURES = 0


def check(label, condition, detail=""):
    global FAILURES
    if condition:
        print("  ok    " + label)
    else:
        FAILURES += 1
        print("  FAIL  " + label + ("  -> " + str(detail) if detail else ""))


ELB = Path.Combine(SAMPLES, "ELB-1_600S162-54(50)_.csv")
RT = Path.Combine(SAMPLES, "RT-1_362S162-54(50)_.csv")

UNITS = [
    {"name": "ELB-1", "members": [None] * 7, "csv": ELB, "revit": True},
    {"name": "RT-1", "members": [None] * 47, "csv": RT},
    {"name": "ELB-2", "members": [None] * 7, "csv": ELB, "damage": True},
    {"name": "WP-9", "members": [], "fail": True},
]
for number in range(3, 30):
    UNITS.append({"name": "ELB-{}".format(number), "members": [None] * 7, "csv": ELB})

CALLS = []


def provider(unit, job_name):
    CALLS.append((unit["name"], job_name))
    if unit.get("fail"):
        raise ValueError("no exportable framing in {}".format(unit["name"]))
    text = File.ReadAllText(unit["csv"])
    lines = text.replace("\r\n", "\n").split("\n")
    lines[0] = "DETAILS,,{}".format(job_name)
    if unit.get("damage"):
        # A station past the end of the member, and a length that disagrees
        # with the end points: both must be caught.
        lines[1] = lines[1].replace("DIMPLE,3577.99", "DIMPLE,3700.00")
        lines[4] = lines[4].replace(",1060.85,0.00,", ",1100.00,0.00,", 1)
    text = "\r\n".join(lines)
    program = preview.parse_program(text)
    result = {
        "filename": Path.GetFileName(unit["csv"]),
        "text": text,
        "count": len(program["components"]),
    }
    if unit.get("revit"):
        rows = []
        for comp in program["components"]:
            cut = comp["length"]
            if comp["label"].endswith("HB2"):
                cut += 3.2
            rows.append(
                {"id": str(1287000 + comp["index"]), "ref": comp["index"], "cut_length": cut}
            )
        result["revit"] = rows
    return result


SELECTED_IN_REVIT = []

window = cnc_window.CncPreviewWindow(
    UNITS,
    provider,
    "ZUHA-ADMIN BUILDING",
    r"D:\Projects\ZUHA\CNC",
    checked_names=["ELB-1"],
    on_select=SELECTED_IN_REVIT.append,
    pick_folder=lambda: r"D:\Elsewhere",
)
win = window.window


def settle():
    win.Dispatcher.Invoke(DispatcherPriority.ApplicationIdle, Action(lambda: None))


def shot(name, element=None, factor=1, of=None):
    """Screenshot the window, or one element of it at ``factor`` x detail."""
    settle()
    root = (of or win).Content
    width, height = int(root.ActualWidth), int(root.ActualHeight)
    bitmap = RenderTargetBitmap(
        width * factor, height * factor, 96.0 * factor, 96.0 * factor,
        PixelFormats.Pbgra32,
    )
    bitmap.Render(root)
    if element is not None:
        origin = element.TransformToAncestor(root).Transform(Point(0.0, 0.0))
        bitmap = CroppedBitmap(
            bitmap,
            Int32Rect(
                int(origin.X * factor),
                int(origin.Y * factor),
                int(element.ActualWidth * factor),
                int(element.ActualHeight * factor),
            ),
        )
    encoder = PngBitmapEncoder()
    encoder.Frames.Add(BitmapFrame.Create(bitmap))
    path = Path.Combine(OUT, name + ".png")
    stream = FileStream(path, FileMode.Create)
    try:
        encoder.Save(stream)
    finally:
        stream.Close()
    print("  shot  " + path)


def scene_count():
    return window.scene.Children.Count


win.Show()
settle()

from System import Environment

print("IronPython {} on .NET {}".format(sys.version.split(" (")[0], Environment.Version))
print("open")
check("first ticked unit is previewed", window._current == 0, window._current)
check("ELB-1 has 7 components", window.metric_components.Text == "7", window.metric_components.Text)
check("ELB-1 has 59 operations", window.metric_operations.Text == "59", window.metric_operations.Text)
check("ELB-1 overall size", window.metric_extents.Text == "1060.9 x 3600.0", window.metric_extents.Text)
check("ELB-1 checks pass", window.check_title.Text.startswith("Program checks passed"), window.check_title.Text)
check("component table has 7 rows", window.component_grid.Items.Count == 7)
check("drawing has content", scene_count() > 10, scene_count())
check("export shows the ticked count", window.export_button.Content == "Export 1 program", window.export_button.Content)
check("Revit comparison is summarised", "6 of 7" in window.revit_check_text.Text, window.revit_check_text.Text)
shot("01_elb1_fit")
shot("01b_elb1_drawing", window.viewport, 2)

print("table values are the CSV text")
entry = window._entry_now
raw_rows = [l for l in entry["text"].split("\r\n") if l.startswith("COMPONENT")]
view = window.component_grid.ItemsSource
all_match = True
for index, raw in enumerate(raw_rows):
    fields = raw.split(",")
    row = view[index].Row
    shown = [row["Label"], row["Section"], row["Role"], row["Orient"], row["Qty"],
             row["Length"], row["X0"], row["Y0"], row["X1"], row["Y1"], row["Web"]]
    if shown != fields[1:12]:
        all_match = False
        print("        row {} differs: {} / {}".format(index, shown, fields[1:12]))
check("every fixed field equals the CSV field, in file order", all_match)

print("select a member from the table")
window.component_grid.SelectedIndex = 2
settle()
check("selection reaches the viewer", window._selected == 2, window._selected)
check("operations table lists 11 operations", window.operation_grid.Items.Count == 11, window.operation_grid.Items.Count)
check("CSV row is shown verbatim", window.row_text.Text == raw_rows[2])
ops_view = window.operation_grid.ItemsSource
stations = [ops_view[i].Row["Station"] for i in range(ops_view.Count)]
check("stations are the CSV stations", stations == raw_rows[2].split(",")[13::2], stations)
check("Revit line is shown", "1287002" in window.member_revit.Text, window.member_revit.Text)
window._on_select_in_revit(None, None)
check("Select in Revit passes the element", SELECTED_IN_REVIT == [2], SELECTED_IN_REVIT)
shot("02_elb1_member_selected")

print("zoom")
before = window._scale
window._zoom_to_member()
window._zoom_at(400.0, 300.0, 3.0)
settle()
check("zoom changes the scale", window._scale > before * 2, (before, window._scale))
window.stations_toggle.IsChecked = True
window._on_view_option(None, None)
window.operation_grid.SelectedIndex = 4
settle()
check("operation row selects the operation", window._selected_op == 4)
shot("03_elb1_zoomed_stations")
shot("03b_elb1_zoomed_drawing", window.viewport, 2)

print("click in the drawing")
window.stations_toggle.IsChecked = False
check("Show whole panel offered while a member is selected",
      str(window.show_panel_button.Visibility) == "Visible")
window.show_panel_button.RaiseEvent(RoutedEventArgs(ButtonBase.ClickEvent))
settle()
check("Show whole panel clears the selection", window._selected is None, window._selected)
check("and hides itself", str(window.show_panel_button.Visibility) == "Collapsed")
tbot = window._entry_now["program"]["components"][3]
mid = preview.point_at(tbot, 300.0)
window._click(Point(mid[0] * window._scale + window._tx, -mid[1] * window._scale + window._ty))
check("clicking the bottom track selects it", window._selected == 3, window._selected)
shot("03c_track_alone_with_notches", window.viewport, 2)
window._select(None)
settle()
op_point = [p for p in window._op_points if p[2] == 0][3]
window._click(Point(op_point[0] + window._tx, op_point[1] + window._ty))
check("clicking a marker selects its operation", (window._selected, window._selected_op) == (0, 3),
      (window._selected, window._selected_op))
op_point = [p for p in window._op_points if p[2] == 0][3]
window._hover(Point(op_point[0] + window._tx, op_point[1] + window._ty))
check("hover names the operation and station", "LIP NOTCH" in window.hover_text.Text and "1198.56" in window.hover_text.Text,
      window.hover_text.Text)
window._hide_tip()
shot("03d_stud_alone_with_swages", window.viewport, 2)
hidden = preview.point_at(tbot, 300.0)
window._click(Point(hidden[0] * window._scale + window._tx, -hidden[1] * window._scale + window._ty))
check("hidden members cannot be clicked; clicking off brings the panel back",
      window._selected is None, window._selected)
window._select(2)
window._on_window_key(None, type("Args", (), {"Key": Key.Escape, "Handled": False})())
check("Esc brings the panel back", window._selected is None, window._selected)
window._click(Point(5.0, 5.0))
check("clicking empty space keeps the panel", window._selected is None)
for name, x, y in (("03e_close_stud_foot", 536.0, 30.0), ("03f_close_noggin_crossing", 536.0, 1200.0)):
    window._fit(rebuild=True)
    settle()
    sx, sy = x * window._scale + window._tx, -y * window._scale + window._ty
    window._zoom_at(sx, sy, 12.0)
    settle()
    shot(name, window.viewport, 2)
window._fit(rebuild=True)
settle()

print("truss")
window.unit_list.SelectedItem = window._row_view(1)
settle()
check("RT-1 is previewed", window._current == 1)
check("RT-1 has 47 components", window.metric_components.Text == "47", window.metric_components.Text)
check("RT-1 has 450 operations", window.metric_operations.Text == "450", window.metric_operations.Text)
check("Revit columns hidden without Revit data",
      str(window.component_grid.Columns[13].Visibility) == "Collapsed")
shot("04_rt1_fit")
# Left end of the truss, close enough to read the joints.
size = window._viewport_size()
window._zoom_at(80.0, size[1] / 2.0, 7.0)
window._select(3)
shot("05_rt1_left_end")
shot("05b_rt1_left_end_drawing", window.viewport, 2)
window._select(None)
window.stations_toggle.IsChecked = True
window._on_view_option(None, None)
window._zoom_at(200.0, size[1] / 2.0, 2.2)
shot("05c_rt1_joint_stations", window.viewport, 2)
window.stations_toggle.IsChecked = False
window._on_view_option(None, None)
for name, x, y in (("05d_rt1_top_chord_joint", 620.0, 690.0),
                   ("05e_rt1_bottom_chord_joint", 560.0, 30.0)):
    window._fit(rebuild=True)
    settle()
    sx, sy = x * window._scale + window._tx, -y * window._scale + window._ty
    window._zoom_at(sx, sy, 1.0 / window._scale * 3.0)
    settle()
    shot(name, window.viewport, 2)
window._fit(rebuild=True)
settle()

print("checks")
window.unit_list.SelectedItem = window._row_view(2)
settle()
check("damaged program reports an error", "1 error" in window.check_title.Text, window.check_title.Text)
check("and a warning", "1 warning" in window.check_title.Text, window.check_title.Text)
check("list marks it red", window._unit_row(2)["Status"] == "error", window._unit_row(2)["Status"])
shot("06_damaged_program")

window.unit_list.SelectedItem = window._row_view(3)
settle()
check("unbuildable unit says why", "no exportable framing" in window.empty_text.Text, window.empty_text.Text)
check("list marks it failed", window._unit_row(3)["Status"] == "failed")
shot("07_failed_unit")

print("list")
window.search_box.Text = "RT"
settle()
check("search filters the list", window.unit_list.Items.Count == 1, window.unit_list.Items.Count)
window._set_visible_checks(True)
check("Check All ticks only what is listed", window._checked_indexes() == [0, 1], window._checked_indexes())
window.search_box.Text = ""
settle()
check("clearing search restores the list", window.unit_list.Items.Count == len(UNITS))
window._set_visible_checks(None)
check("Toggle All inverts", len(window._checked_indexes()) == len(UNITS) - 2)
window._set_visible_checks(False)
check("Uncheck All clears", window._checked_indexes() == [] and not window.export_button.IsEnabled)

print("job name and folder")
window.unit_list.SelectedItem = window._row_view(0)
settle()
window.job_box.Text = "NEW JOB"
window._on_job_committed(None, None)
check("job name reaches the DETAILS row", window._entry_now["text"].startswith("DETAILS,,NEW JOB\r\n"))
window._on_change_folder(None, None)
check("folder changes", window.folder_text.Text == r"D:\Elsewhere")

print("run checks on everything")
window._on_verify(None, None)
check("button offers Stop while running", window.verify_button.Content == "Stop")
for _ in range(3000):
    if window._verify_timer is None:
        break
    Thread.CurrentThread.Join(2)
    settle()
check("every unit gets a status", all(window._unit_row(i)["Status"] for i in range(len(UNITS))))
check("summary counts", window.verify_text.Text == "29 passed, 0 with warnings, 2 with errors", window.verify_text.Text)
shot("08_all_checked")

print("export")
window._unit_row(0)["Checked"] = True
window._unit_row(1)["Checked"] = True
window._update_checked_count()
window._on_export(None, None)
result = window.result
check("result returned", result is not None)
if result:
    check("job and folder", (result["job_name"], result["out_dir"]) == ("NEW JOB", r"D:\Elsewhere"))
    check("two programs", [e["unit"]["name"] for e in result["items"]] == ["ELB-1", "RT-1"])
    check("exported text is the previewed text", result["items"][0]["text"] is window._cache[0]["text"])
    first_line = File.ReadAllText(RT).split("\r\n")[0]
    rebuilt = result["items"][1]["text"].replace("DETAILS,,NEW JOB", first_line, 1)
    check("RT-1 text is byte-identical apart from the job name", rebuilt == File.ReadAllText(RT))

check("no handler raised", window.errors == [], window.errors)
check("as a dialog, Export closes the window", not window.is_open)


# ============================================================= beside Revit
class HeldRunner(object):
    """Stands in for Revit: model work waits until the test lets it run."""

    deferred = True

    def __init__(self):
        self.urgent = []
        self.normal = []

    def submit(self, work, done, urgent=False):
        (self.urgent if urgent else self.normal).append((work, done))

    def pending(self):
        return len(self.urgent) + len(self.normal)

    def release(self):
        """Revit is free: run everything that is waiting, urgent work first."""
        while self.urgent or self.normal:
            work, done = (self.urgent or self.normal).pop(0)
            try:
                result, error = work(), None
            except Exception as ex:
                result, error = None, ex
            done(result, error)
        settle()


def pump(milliseconds):
    for _ in range(max(1, milliseconds // 10)):
        Thread.CurrentThread.Join(10)
        settle()


MODEL = {"units": list(UNITS), "rescans": 0, "fail": False}


def rescan():
    MODEL["rescans"] += 1
    if MODEL["fail"]:
        raise RuntimeError("the model is no longer open in Revit")
    return list(MODEL["units"])


EXPORT_DIR = Path.Combine(OUT, "modeless_export")
if Directory.Exists(EXPORT_DIR):
    Directory.Delete(EXPORT_DIR, True)
WRITES = []
CLOSED = []


def on_export(result):
    WRITES.append(result)
    return files.write_programs(result["items"], result["out_dir"])


def ticked(of):
    return sorted(of._units[i]["name"] for i in of._checked_indexes())


def visible(element):
    return str(element.Visibility) == "Visible"


print("beside Revit: the window opens without waiting")
del CALLS[:]
del SELECTED_IN_REVIT[:]
held = HeldRunner()
tool = cnc_window.CncPreviewWindow(
    MODEL["units"],
    provider,
    "ZUHA-ADMIN BUILDING",
    EXPORT_DIR,
    checked_names=["ELB-1"],
    on_select=SELECTED_IN_REVIT.append,
    runner=held,
    rescan=rescan,
    on_export=on_export,
    on_closed=lambda: CLOSED.append(True),
)
tool._in_use = lambda: False  # Revit, not this window, is being worked in
twin = tool.window
tool.show()
settle()
check("the footer button reads Close and Esc does not close",
      tool.cancel_button.Content == "Close" and not tool.cancel_button.IsCancel)
check("re-reading the panels is offered", visible(tool.refresh_button))
check("nothing is drawn until Revit answers",
      tool._entry_now is None and "Reading ELB-1 from the model" in tool.empty_text.Text,
      tool.empty_text.Text)
check("one request is waiting for Revit", held.pending() == 1 and CALLS == [], (held.pending(), CALLS))
shot("09_waiting_for_revit", of=twin)
held.release()
check("then the program is drawn", tool.metric_components.Text == "7"
      and tool.scene.Children.Count > 10, tool.metric_components.Text)

print("beside Revit: Revit is busy")
cnc_window.SLOW_AFTER_S = 0.05
tool.unit_list.SelectedItem = tool._row_view(1)
settle()
pump(250)
check("the viewer says it is waiting for Revit", "Waiting for Revit" in tool.empty_text.Text,
      tool.empty_text.Text)
tool.unit_list.SelectedItem = tool._row_view(4)
settle()
check("two programs are waiting", held.pending() == 2, held.pending())
held.release()
check("the last one asked for is the one shown",
      tool.view_title.Text == "ELB-3" and tool._entry_now["unit"]["name"] == "ELB-3",
      tool.view_title.Text)
check("the other is kept for later", tool._unit_row(1)["Status"] == "ok", tool._unit_row(1)["Status"])
cnc_window.SLOW_AFTER_S = 2.5

print("beside Revit: Select in Revit")
tool.unit_list.SelectedItem = tool._row_view(0)
settle()
check("a program already read needs no wait", held.pending() == 0 and tool.view_title.Text == "ELB-1")
tool._select(2)
tool._on_select_in_revit(None, None)
check("the selection waits for Revit", SELECTED_IN_REVIT == [] and "Selecting" in tool.status_text.Text,
      tool.status_text.Text)
held.release()
check("then it is made", SELECTED_IN_REVIT == [2] and "selected in Revit" in tool.status_text.Text,
      (SELECTED_IN_REVIT, tool.status_text.Text))

print("beside Revit: run checks on everything")
tool._on_verify(None, None)
peak = 0
for _ in range(3000):
    if tool._verify_timer is None:
        break
    Thread.CurrentThread.Join(2)
    settle()
    peak = max(peak, held.pending())
    if held.pending() >= cnc_window.VERIFY_IN_FLIGHT or not tool._verify_queue:
        held.release()
check("Revit is asked for a few programs at a time", peak == cnc_window.VERIFY_IN_FLIGHT, peak)
check("summary counts", tool.verify_text.Text == "29 passed, 0 with warnings, 2 with errors",
      tool.verify_text.Text)

print("beside Revit: the model changes")
tool._select(2)
tool._zoom_to_member()
settle()
scale_before = tool._scale
tool._unit_row(1)["Checked"] = True
tool._update_checked_count()
calls_before = len(CALLS)
MODEL["units"] = [u for u in UNITS if u["name"] != "WP-9"] + [
    {"name": "NEW-1", "members": [None] * 7, "csv": ELB}]
tool.mark_stale()
settle()
check("a bar says the model has changed", visible(tool.model_bar)
      and "has changed" in tool.model_bar_text.Text and visible(tool.model_bar_button),
      tool.model_bar_text.Text)
check("nothing is read while Revit is being worked in",
      MODEL["rescans"] == 0 and held.pending() == 0, (MODEL["rescans"], held.pending()))
shot("10_model_changed", of=twin)
tool._on_activated(None, None)
check("coming back to the window asks Revit for the panels",
      held.pending() == 1 and "Reading the panels" in tool.model_bar_text.Text,
      (held.pending(), tool.model_bar_text.Text))
held.release()
check("the list follows the model", MODEL["rescans"] == 1
      and tool.unit_list.Items.Count == len(MODEL["units"])
      and "NEW-1" in [u["name"] for u in tool._units]
      and "WP-9" not in [u["name"] for u in tool._units], tool.unit_list.Items.Count)
check("ticks are kept by name", ticked(tool) == ["ELB-1", "RT-1"], ticked(tool))
check("the bar is gone", not visible(tool.model_bar))
check("the program shown was built again from the model",
      [c[0] for c in CALLS[calls_before:]] == ["ELB-1"] and tool.view_title.Text == "ELB-1"
      and tool.metric_components.Text == "7", CALLS[calls_before:])
check("zoom and the selected member are kept",
      abs(tool._scale - scale_before) < 1e-9 and tool._selected == 2 and not tool._fitted,
      (scale_before, tool._scale, tool._selected))
check("earlier check results are dropped", tool._unit_row(5)["Status"] == "", tool._unit_row(5)["Status"])

tool.unit_list.SelectedItem = tool._row_view(1)
settle()
calls_before = len(CALLS)
tool.mark_stale()
tool._on_activated(None, None)
held.release()
check("a program asked of the old list is not built",
      [c[0] for c in CALLS[calls_before:]] == ["RT-1"], CALLS[calls_before:])
check("the unit asked for is shown from the new list",
      tool.view_title.Text == "RT-1" and tool.metric_components.Text == "47", tool.view_title.Text)

with_truss = list(MODEL["units"])
MODEL["units"] = [u for u in with_truss if u["name"] != "RT-1"]
tool.mark_stale()
tool._on_activated(None, None)
held.release()
check("a unit that is gone says so", "RT-1 is no longer in the model" in tool.empty_text.Text,
      tool.empty_text.Text)
check("and its tick goes with it", ticked(tool) == ["ELB-1"], ticked(tool))

MODEL["fail"] = True
tool._refresh()
held.release()
check("a failed read is shown, with a way to retry",
      visible(tool.model_bar) and "could not be read" in tool.model_bar_text.Text
      and visible(tool.model_bar_button), tool.model_bar_text.Text)
MODEL["fail"] = False
MODEL["units"] = with_truss
tool._refresh()
held.release()
check("retrying reads the panels", not visible(tool.model_bar)
      and tool.unit_list.Items.Count == len(with_truss), tool.unit_list.Items.Count)

print("beside Revit: export")
for position, unit in enumerate(tool._units):
    tool._unit_row(position)["Checked"] = unit["name"] in ("ELB-1", "RT-1", "NEW-1")
tool._update_checked_count()
tool.mark_stale()  # the model changed once more before Export was pressed
tool._on_export(None, None)
check("Export first reads the model again",
      held.pending() == 1 and tool._exporting is None and WRITES == [], held.pending())
held.release()
elb_out = Path.Combine(EXPORT_DIR, "ELB-1_600S162-54(50)_.csv")
rt_out = Path.Combine(EXPORT_DIR, "RT-1_362S162-54(50)_.csv")
check("the files are written and the window stays open",
      len(WRITES) == 1 and tool.is_open and twin.IsVisible, len(WRITES))
check("identical panels share one file",
      sorted(Path.GetFileName(f) for f in Directory.GetFiles(EXPORT_DIR)) == [
          "ELB-1_600S162-54(50)_.csv", "RT-1_362S162-54(50)_.csv"],
      list(Directory.GetFiles(EXPORT_DIR)))
names = [u["name"] for u in tool._units]
check("the file is the text that was checked",
      File.Exists(elb_out) and File.ReadAllText(elb_out) == tool._cache[names.index("ELB-1")]["text"])
first_line = File.ReadAllText(RT).split("\r\n")[0]
check("RT-1 is byte-identical apart from the job name", File.Exists(rt_out)
      and File.ReadAllText(rt_out).replace("DETAILS,,ZUHA-ADMIN BUILDING", first_line, 1)
      == File.ReadAllText(RT))
check("a report is shown over the window", visible(tool.report_overlay)
      and tool.report_title.Text == "Export finished", tool.report_title.Text)
check("it counts the files and the repeat",
      tool.report_summary.Text.startswith("2 files written to " + EXPORT_DIR)
      and "1 identical unit shares a program file." in tool.report_summary.Text,
      tool.report_summary.Text)
check("it lists them", "x2 identical units" in tool.report_details.Text
      and "47 components" in tool.report_details.Text, tool.report_details.Text)
shot("11_export_report", of=twin)
tool._hide_report()
check("Done dismisses the report", not visible(tool.report_overlay))
check("Open folder can start Explorer on this .NET",
      cnc_window._process_class().__name__ == "Process")

lock = FileStream(elb_out, FileMode.Open, FileAccess.Read, FileShare.None)
try:
    tool._on_export(None, None)
    held.release()
finally:
    lock.Close()
check("a file open in another program is reported, not lost",
      len(WRITES) == 2 and tool.report_title.Text == "Export finished, with problems"
      and "OPEN IN ANOTHER PROGRAM" in tool.report_details.Text
      and "ELB-1_600S162-54(50)_.csv" in tool.report_details.Text,
      (tool.report_title.Text, tool.report_details.Text))
shot("12_export_report_locked", of=twin)
tool._hide_report()

tool.job_box.Text = "JOB 2"  # a new job name means every program is built again
tool._on_export(None, None)
check("the programs are asked of Revit",
      tool._exporting is not None and held.pending() == 3 and not tool.export_button.IsEnabled,
      held.pending())
tool.mark_stale()
check("a model change stops the export",
      tool._exporting is None and "Export stopped" in tool.status_text.Text
      and tool.export_button.IsEnabled, tool.status_text.Text)
held.release()
check("and nothing is written", len(WRITES) == 2 and not visible(tool.report_overlay), len(WRITES))

print("beside Revit: closing")
tool._on_activated(None, None)
check("a read is waiting", held.pending() == 1, held.pending())
twin.Close()
settle()
check("closing is passed on", CLOSED == [True] and not tool.is_open, CLOSED)
held.release()
check("answers that arrive after closing are ignored", tool.errors == [], tool.errors)

print("")
print("FAILURES: {}".format(FAILURES))
