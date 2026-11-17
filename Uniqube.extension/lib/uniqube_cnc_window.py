# -*- coding: utf-8 -*-
"""Generate CNC window: panel list, program viewer and CNC value tables.

Left    the panels / trusses, a tick to export and an eye to preview.
Centre  the program drawn from its CSV rows, with zoom and pan.
Right   the same rows as tables, so every value can be read and checked.

The viewer and the tables are both read back from the CSV text that will be
written, so what is checked here is what the rollformer receives.

The window can stay open beside Revit (``show``) or run as a dialog
(``show_dialog``). It never touches the model itself: the caller passes
callables for that, and a runner that decides when they may run. Beside Revit
the runner hands them to Revit, which runs them when it is free, so every
model call here is a request with a callback rather than a direct call.

No Revit or pyRevit imports, which keeps this window testable outside Revit.
"""
from __future__ import print_function

import math

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xaml")
clr.AddReference("System.Data")

from System import Action, Array, Boolean, DateTime, Double, Int32, Object, String, TimeSpan
from System.Data import DataTable
from System.IO import File
from System.IO import Path as IOPath
from System.Windows import (
    CornerRadius,
    FontWeights,
    MessageBox,
    MessageBoxButton,
    MessageBoxImage,
    MessageBoxResult,
    Point,
    RoutedEventHandler,
    Size,
    SystemParameters,
    TextWrapping,
    Thickness,
    VerticalAlignment,
    Visibility,
    WindowState,
)
from System.Windows.Controls import (
    Border,
    Button,
    Canvas,
    CheckBox,
    DataGridLength,
    StackPanel,
    TextBlock,
)
from System.Windows.Controls import Orientation as StackOrientation
from System.Windows.Controls.Primitives import ButtonBase
from System.Windows.Input import Cursors, Key, MouseButton
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader
from System.Windows.Media import (
    ColorConverter,
    FillRule,
    FontFamily,
    Geometry,
    GeometryCombineMode,
    GeometryGroup,
    PenLineCap,
    PenLineJoin,
    RotateTransform,
    SolidColorBrush,
    StreamGeometry,
    SweepDirection,
    TranslateTransform,
)
from System.Windows.Shapes import Ellipse, Rectangle
from System.Windows.Shapes import Path as ShapePath
from System.Windows.Threading import DispatcherPriority, DispatcherTimer

import uniqube_brand as brand
import uniqube_cnc_files as files
import uniqube_cnc_preview as preview

XAML_NAME = "CncPreviewWindow.xaml"
MAX_SCALE = 40.0  # px per mm
ZOOM_STEP = 1.25
DRAG_THRESHOLD = 4.0
PICK_RADIUS = 9.0
REVIT_LENGTH_TOL_MM = 1.0
VERIFY_IN_FLIGHT = 8  # programs asked of Revit at a time while checking all
SLOW_AFTER_S = 2.5  # how long a model call may take before saying Revit is busy
REVIT_BUSY = (
    "Waiting for Revit. This continues as soon as Revit is free - "
    "finish or cancel whatever is running there."
)


def _brush(code):
    brush = SolidColorBrush(ColorConverter.ConvertFromString(code))
    brush.Freeze()
    return brush


INK = _brush("#FF1F2933")
INK_SOFT = _brush("#FF6B7280")
PAPER = _brush("#FFFFFFFF")
SELECT_FILL = _brush("#FFFFE9BF")
DIM = _brush("#FF8A93A1")
GOOD = _brush("#FF15803D")
GOOD_BG = _brush("#FFE9F7EE")
WARN = _brush("#FFB45309")
WARN_BG = _brush("#FFFFF4D6")
BAD = _brush("#FFB91C1C")
BAD_BG = _brush("#FFFDE8E8")
NEUTRAL_BG = _brush("#FFF6F7F9")
TEXT = _brush("#FF111827")
MUTED = _brush("#FF6B7280")
CHIP_BG = _brush("#FFF3F4F6")
MONO = FontFamily("Consolas")

# operation -> (colour, marker)
OP_STYLE = {
    "DIMPLE": ("#FF1D4ED8", "dot"),
    "SWAGE": ("#FFD97706", "bar"),
    "LIP NOTCH": ("#FF059669", "lip"),
    "WEB NOTCH": ("#FF7C3AED", "web"),
    "SERVICE HOLE": ("#FF0E7490", "ring"),
    "WEB HOLE": ("#FFBE185D", "ring-small"),
    "END_TRUSS": ("#FFDC2626", "diamond"),
}
OP_DEFAULT = ("#FF6B7280", "cross")
_FILLED = ("dot", "lip", "web", "diamond")
_SHAPED = ("bar", "lip", "web")
_OP_BRUSH = {}


def _op_style(name):
    return OP_STYLE.get(name, OP_DEFAULT)


def _op_brush(name):
    brush = _OP_BRUSH.get(name)
    if brush is None:
        brush = _brush(_op_style(name)[0])
        _OP_BRUSH[name] = brush
    return brush


def _cut_brush(name):
    """Translucent operation colour for the material a cut removes."""
    key = ("cut", name)
    brush = _OP_BRUSH.get(key)
    if brush is None:
        brush = _brush("#73" + _op_style(name)[0][3:])
        _OP_BRUSH[key] = brush
    return brush


def _message(ex):
    """Exception text that cannot itself raise (IronPython unicode quirks)."""
    try:
        text = u"{}".format(ex)
    except Exception:
        try:
            text = repr(ex)
        except Exception:
            text = "unknown error"
    return text.strip() or type(ex).__name__


def _process_class():
    """System.Diagnostics.Process, wherever this .NET keeps it."""
    try:
        clr.AddReference("System.Diagnostics.Process")  # .NET 8 (Revit 2025+)
    except Exception:
        pass  # .NET Framework has it in System, which is already loaded
    from System.Diagnostics import Process

    return Process


def _thousands(value):
    """12345.6 -> 12,345.60"""
    text = preview.fmt(value)
    whole, _dot, frac = text.partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "-", whole[1:]
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return "{}{}.{}".format(sign, ",".join(groups), frac)


def _signed(value):
    text = preview.fmt(value)
    if text == "0.00":
        return text
    return text if text.startswith("-") else "+" + text


def _table(columns):
    table = DataTable()
    for name, kind in columns:
        table.Columns.Add(name, clr.GetClrType(kind))
    return table


def _add_row(table, values):
    table.Rows.Add(Array[Object](list(values)))


def _like_escape(text):
    """Escape user text for a DataView RowFilter LIKE pattern."""
    out = []
    for ch in text:
        if ch in "[]%*":
            out.append("[" + ch + "]")
        elif ch == "'":
            out.append("''")
        else:
            out.append(ch)
    return "".join(out)


class ImmediateRunner(object):
    """Runs model work on the spot.

    Right while a Revit command is running (the window shown as a dialog) and
    in tests. A window that stays open beside Revit is given a runner that
    hands the work to Revit instead: see uniqube_cnc_session.RevitRunner.

    A runner has ``submit(work, done, urgent=False)``: it calls ``work()``
    when the model may be touched, then ``done(result, error)``. ``deferred``
    says whether that happens later rather than inside ``submit``.
    """

    deferred = False

    def submit(self, work, done, urgent=False):
        try:
            result, error = work(), None
        except Exception as ex:
            result, error = None, ex
        done(result, error)


class CncPreviewWindow(object):
    """The Generate CNC window.

    units          [{"name": str, "members": [...]}] in list order
    provider       callable(unit, job_name) -> {"filename", "text", "count",
                   "revit": [{"id": str, "ref": object, "cut_length": mm}]}
                   ``revit`` is optional and follows COMPONENT row order.
    checked_names  unit names to tick when the window opens
    on_select      callable(ref) that selects a member in Revit
    pick_folder    callable() -> folder path or None
    owner_handle   Revit main window handle
    runner         decides when ``provider``, ``on_select`` and ``rescan`` may
                   run (see ImmediateRunner); they are the model calls
    rescan         callable() -> units, read again from the model. With it the
                   window can follow a model that changes while it is open.
    on_export      callable(result) -> report (see uniqube_cnc_files). With it
                   Export writes the files and the window stays open; without
                   it Export closes the window and the caller writes them.
    on_closed      callable() once the window has closed

    An export ``result`` is {"job_name", "out_dir", "items": [entry, ...]}
    where each entry carries "unit", "filename", "text", "count" and "error".
    ``show_dialog`` returns it, or None when cancelled.
    """

    def __init__(
        self,
        units,
        provider,
        job_name,
        out_dir,
        checked_names=None,
        on_select=None,
        pick_folder=None,
        owner_handle=None,
        xaml_path=None,
        runner=None,
        rescan=None,
        on_export=None,
        on_closed=None,
    ):
        self._units = list(units)
        self._provider = provider
        self._on_select = on_select
        self._pick_folder = pick_folder
        self._runner = runner if runner is not None else ImmediateRunner()
        self._deferred = bool(getattr(self._runner, "deferred", False))
        self._rescan = rescan
        self._on_export_cb = on_export
        self._on_closed_cb = on_closed
        self._default_job = job_name or ""
        self._out_dir = out_dir or ""
        self.result = None
        self.report = None  # what the last export wrote
        self.errors = []  # faults caught in event handlers, for the caller's log

        self._cache = {}
        self._cache_job = None
        self._current = None  # index of the unit in the viewer
        self._entry_now = None
        self._selected = None  # component index
        self._selected_op = None
        self._syncing = False

        # Model calls in progress. A generation is one reading of the unit
        # list: results that belong to an earlier one are dropped.
        self._generation = 0
        self._waiting = {}
        self._show_token = 0
        self._reading = None
        self._stale = False
        self._refreshing = False
        self._after_refresh = None
        self._restore = None
        self._exporting = None
        self._closed = False

        self._scale = 1.0
        self._fit_scale = 1.0
        self._tx = 0.0
        self._ty = 0.0
        self._fitted = True
        self._box = None
        self._op_points = []
        self._boxes = []

        self._drag_start = None
        self._drag_origin = None
        self._dragging = False

        self._verify_timer = None
        self._verify_queue = []
        self._verify_total = 0
        self._verify_flight = 0
        self._verify_token = 0

        if xaml_path is None:
            xaml_path = IOPath.Combine(
                IOPath.GetDirectoryName(__file__), "ui", XAML_NAME
            )
        self.window = XamlReader.Parse(File.ReadAllText(xaml_path))
        if owner_handle:
            try:
                WindowInteropHelper(self.window).Owner = owner_handle
            except Exception:
                pass
        self._size_to_screen()
        self._bind_names()
        self._translate = TranslateTransform(0.0, 0.0)
        self.scene.RenderTransform = self._translate

        self._build_unit_table(checked_names or ())
        self.job_box.Text = self._default_job
        self._show_folder()
        brand.load_logo(self.brand_logo, dark=True, header=True)
        if self._rescan is not None:
            self.refresh_button.Visibility = Visibility.Visible
        self._wire_events()
        self._update_checked_count()
        self._clear_program()

    # ------------------------------------------------------------------ setup
    def _size_to_screen(self):
        area = SystemParameters.WorkArea
        self.window.Width = max(
            self.window.MinWidth, min(self.window.Width, area.Width - 60)
        )
        self.window.Height = max(
            self.window.MinHeight, min(self.window.Height, area.Height - 60)
        )

    def _bind_names(self):
        find = self.window.FindName
        self.brand_logo = find("BrandLogo")
        self.job_box = find("JobBox")
        self.folder_text = find("FolderText")
        self.folder_button = find("FolderButton")
        self.checked_count = find("CheckedCount")
        self.search_box = find("SearchBox")
        self.search_hint = find("SearchHint")
        self.unit_list = find("UnitList")
        self.check_all_button = find("CheckAllButton")
        self.uncheck_all_button = find("UncheckAllButton")
        self.toggle_all_button = find("ToggleAllButton")
        self.verify_button = find("VerifyButton")
        self.verify_text = find("VerifyText")
        self.view_title = find("ViewTitle")
        self.view_subtitle = find("ViewSubtitle")
        self.tooling_toggle = find("ToolingToggle")
        self.labels_toggle = find("LabelsToggle")
        self.stations_toggle = find("StationsToggle")
        self.dimensions_toggle = find("DimensionsToggle")
        self.zoom_out_button = find("ZoomOutButton")
        self.zoom_in_button = find("ZoomInButton")
        self.fit_button = find("FitButton")
        self.show_panel_button = find("ShowPanelButton")
        self.viewport = find("Viewport")
        self.scene = find("Scene")
        self.hover_tip = find("HoverTip")
        self.hover_text = find("HoverText")
        self.empty_text = find("EmptyText")
        self.legend_panel = find("LegendPanel")
        self.readout = find("Readout")
        self.file_text = find("FileText")
        self.metric_components = find("MetricComponents")
        self.metric_operations = find("MetricOperations")
        self.metric_length = find("MetricLength")
        self.metric_extents = find("MetricExtents")
        self.op_totals = find("OpTotals")
        self.check_banner = find("CheckBanner")
        self.check_title = find("CheckTitle")
        self.revit_check_text = find("RevitCheckText")
        self.problem_scroll = find("ProblemScroll")
        self.problem_list = find("ProblemList")
        self.component_grid = find("ComponentGrid")
        self.member_title = find("MemberTitle")
        self.member_revit = find("MemberRevit")
        self.zoom_member_button = find("ZoomMemberButton")
        self.select_revit_button = find("SelectRevitButton")
        self.operation_grid = find("OperationGrid")
        self.row_text = find("RowText")
        self.status_text = find("StatusText")
        self.cancel_button = find("CancelButton")
        self.export_button = find("ExportButton")
        self.refresh_button = find("RefreshButton")
        self.model_bar = find("ModelBar")
        self.model_bar_text = find("ModelBarText")
        self.model_bar_button = find("ModelBarButton")
        self.report_overlay = find("ReportOverlay")
        self.report_dot = find("ReportDot")
        self.report_title = find("ReportTitle")
        self.report_summary = find("ReportSummary")
        self.report_details = find("ReportDetails")
        self.report_open_button = find("ReportOpenButton")
        self.report_close_button = find("ReportCloseButton")

    def _wire_events(self):
        guard = self._guard
        self.window.Loaded += guard(self._on_loaded)
        self.window.Closed += guard(self._on_closed)
        self.window.Activated += guard(self._on_activated)

        self.refresh_button.Click += guard(lambda s, e: self._refresh())
        self.model_bar_button.Click += guard(lambda s, e: self._refresh())
        self.report_open_button.Click += guard(self._on_open_folder)
        self.report_close_button.Click += guard(lambda s, e: self._hide_report())

        self.job_box.LostFocus += guard(self._on_job_committed)
        self.job_box.KeyDown += guard(self._on_job_key)
        self.folder_button.Click += guard(self._on_change_folder)

        self.search_box.TextChanged += guard(self._on_search)
        self.unit_list.SelectionChanged += guard(self._on_unit_selected)
        self.unit_list.AddHandler(
            ButtonBase.ClickEvent, RoutedEventHandler(guard(self._on_list_click))
        )
        self.check_all_button.Click += guard(
            lambda s, e: self._set_visible_checks(True)
        )
        self.uncheck_all_button.Click += guard(
            lambda s, e: self._set_visible_checks(False)
        )
        self.toggle_all_button.Click += guard(
            lambda s, e: self._set_visible_checks(None)
        )
        self.verify_button.Click += guard(self._on_verify)

        for toggle in (
            self.tooling_toggle,
            self.labels_toggle,
            self.stations_toggle,
            self.dimensions_toggle,
        ):
            toggle.Click += guard(self._on_view_option)
        self.fit_button.Click += guard(lambda s, e: self._fit())
        self.show_panel_button.Click += guard(lambda s, e: self._select(None))
        self.window.PreviewKeyDown += guard(self._on_window_key)
        self.zoom_in_button.Click += guard(lambda s, e: self._zoom_centre(ZOOM_STEP))
        self.zoom_out_button.Click += guard(
            lambda s, e: self._zoom_centre(1.0 / ZOOM_STEP)
        )

        self.viewport.SizeChanged += guard(self._on_viewport_resized)
        self.viewport.MouseWheel += guard(self._on_wheel)
        self.viewport.MouseDown += guard(self._on_mouse_down)
        self.viewport.MouseMove += guard(self._on_mouse_move)
        self.viewport.MouseUp += guard(self._on_mouse_up)
        self.viewport.MouseLeave += guard(self._on_mouse_leave)
        self.viewport.LostMouseCapture += guard(self._on_capture_lost)

        self.component_grid.SelectionChanged += guard(self._on_component_row)
        self.component_grid.MouseDoubleClick += guard(
            lambda s, e: self._zoom_to_member()
        )
        self.operation_grid.SelectionChanged += guard(self._on_operation_row)
        self.zoom_member_button.Click += guard(lambda s, e: self._zoom_to_member())
        self.select_revit_button.Click += guard(self._on_select_in_revit)

        self.export_button.Click += guard(self._on_export)
        self.cancel_button.Click += guard(lambda s, e: self.window.Close())

    def _fault(self, ex):
        self.errors.append(_message(ex))
        try:
            self._status(u"Something went wrong: {}".format(_message(ex)))
        except Exception:
            pass

    def _guard(self, handler):
        """Wrap an event handler so a fault is reported, not thrown at Revit.

        An exception escaping a WPF handler would unwind into Revit's message
        loop and take the whole window down with it.
        """

        def guarded(sender, args):
            try:
                handler(sender, args)
            except Exception as ex:
                self._fault(ex)

        return guarded

    def _safely(self, callback):
        """The same protection for a callback that a model call ends in."""

        def safe(*args):
            try:
                callback(*args)
            except Exception as ex:
                self._fault(ex)

        return safe

    def _after(self, action):
        """Run ``action`` once the model call in progress has returned."""
        if not self._deferred:
            action()
            return
        self.window.Dispatcher.BeginInvoke(
            DispatcherPriority.Background, Action(self._safely(action))
        )

    def _when_slow(self, still_waiting, tell):
        """Call ``tell()`` if a model call is still waiting for Revit."""
        if not self._deferred:
            return
        timer = DispatcherTimer()
        timer.Interval = TimeSpan.FromSeconds(SLOW_AFTER_S)

        def tick(sender, args):
            timer.Stop()
            if not self._closed and still_waiting():
                tell()

        timer.Tick += self._guard(tick)
        timer.Start()

    # --------------------------------------------------------------- showing
    def show(self):
        """Show as a tool window: Revit stays usable while it is open."""
        self.cancel_button.Content = "Close"
        self.cancel_button.IsCancel = False  # Esc must not close a tool window
        self.window.Show()

    def show_dialog(self, notice=None):
        """Show as a dialog; returns the export result, or None if cancelled.

        ``notice`` is shown in a bar across the top for as long as it is open.
        """
        if notice:
            self._show_model_bar(notice, False)
        if self._on_export_cb is not None:
            self.cancel_button.Content = "Close"  # Export leaves it open
        self.window.ShowDialog()
        return self.result

    def activate(self):
        """Bring the window to the front."""
        if self.window.WindowState == WindowState.Minimized:
            self.window.WindowState = WindowState.Normal
        self.window.Activate()

    def close(self):
        if not self._closed:
            self.window.Close()

    @property
    def is_open(self):
        return not self._closed

    # ------------------------------------------------------------- unit list
    def _build_unit_table(self, checked_names):
        checked = set(checked_names)
        self._unit_table = _table(
            [
                ("Index", Int32),
                ("Checked", Boolean),
                ("Name", String),
                ("Members", String),
                ("Status", String),
            ]
        )
        for index, unit in enumerate(self._units):
            count = len(unit.get("members") or [])
            _add_row(
                self._unit_table,
                [
                    index,
                    unit.get("name") in checked,
                    u"{}".format(unit.get("name") or ""),
                    "({} member{})".format(count, "" if count == 1 else "s"),
                    "",
                ],
            )
        self._unit_view = self._unit_table.DefaultView
        self.unit_list.ItemsSource = self._unit_view

    def _unit_row(self, index):
        return self._unit_table.Rows[index]

    def _visible_rows(self):
        return [item.Row for item in self._unit_view]

    def _checked_indexes(self):
        return [
            index
            for index in range(len(self._units))
            if self._unit_row(index)["Checked"]
        ]

    def _row_view(self, index):
        for item in self._unit_view:
            if item.Row["Index"] == index:
                return item
        return None

    def _set_visible_checks(self, value):
        for row in self._visible_rows():
            row["Checked"] = (not row["Checked"]) if value is None else value
        self._update_checked_count()

    def _update_checked_count(self):
        count = len(self._checked_indexes())
        self.checked_count.Text = "{} of {} ticked".format(count, len(self._units))
        self.export_button.Content = (
            "Export {} program{}".format(count, "" if count == 1 else "s")
            if count
            else "Export"
        )
        self.export_button.IsEnabled = count > 0 and self._exporting is None
        if not count:
            self._status("Tick the panels to export, then press Export.")
        else:
            self._status("")

    def _on_search(self, sender, args):
        text = self.search_box.Text.strip()
        self.search_hint.Visibility = (
            Visibility.Collapsed if self.search_box.Text else Visibility.Visible
        )
        self._syncing = True
        try:
            self._unit_view.RowFilter = (
                "Name LIKE '%{}%'".format(_like_escape(text)) if text else ""
            )
            # Keep the previewed unit highlighted when it is still listed.
            if self._current is not None:
                item = self._row_view(self._current)
                if item is not None:
                    self.unit_list.SelectedItem = item
        finally:
            self._syncing = False

    def _on_list_click(self, sender, args):
        source = args.OriginalSource
        item = getattr(source, "DataContext", None)
        row = getattr(item, "Row", None)
        if row is None:
            return
        if isinstance(source, CheckBox):
            row["Checked"] = bool(source.IsChecked)
            self._update_checked_count()
        elif isinstance(source, Button):
            if self.unit_list.SelectedItem is item:
                self._fit()
            else:
                self.unit_list.SelectedItem = item
        args.Handled = True

    def _on_unit_selected(self, sender, args):
        if self._syncing:
            return
        item = self.unit_list.SelectedItem
        if item is None:
            return
        index = item.Row["Index"]
        if index != self._current:
            self._show_unit(index)

    def _set_status_flag(self, index, entry):
        if entry["error"]:
            flag = "failed"
        elif entry["summary"]["errors"]:
            flag = "error"
        elif entry["summary"]["warnings"]:
            flag = "warning"
        else:
            flag = "ok"
        self._unit_row(index)["Status"] = flag

    # --------------------------------------------------------------- programs
    def _job(self):
        return self.job_box.Text.strip() or self._default_job

    def _use_job(self, job):
        """Programs are cached per job name: it is part of their text."""
        if job != self._cache_job:
            self._cache.clear()
            self._cache_job = job

    def _make_entry(self, index, unit, built, error):
        entry = {
            "index": index,
            "unit": unit,
            "filename": "",
            "text": "",
            "count": 0,
            "program": None,
            "summary": None,
            "flags": {},
            "revit": [],
            "error": None,
        }
        if error is not None:
            entry["error"] = _message(error)
            return entry
        try:
            program = preview.parse_program(built["text"])
            entry["filename"] = built["filename"]
            entry["text"] = built["text"]
            entry["count"] = built["count"]
            entry["program"] = program
            entry["summary"] = preview.summarize(program)
            entry["flags"] = preview.component_flags(program)
            revit = built.get("revit") or []
            if len(revit) == len(program["components"]):
                entry["revit"] = revit
        except Exception as ex:
            entry["error"] = _message(ex)
        return entry

    def _request_entry(self, index, job, done, urgent=False):
        """Build (or reuse) the program for a unit, then call ``done(entry)``.

        The entry is cached under the job name, so the text shown is the text
        that is exported. ``done`` is not called if the unit list is read
        again from the model before the program arrives.
        """
        self._use_job(job)
        entry = self._cache.get(index)
        if entry is not None:
            done(entry)
            return

        generation = self._generation
        key = (generation, job, index)
        waiting = self._waiting.get(key)
        if waiting is not None:
            waiting.append(done)
            return
        self._waiting[key] = [done]
        unit = self._units[index]
        provider = self._provider

        def work():
            if generation != self._generation or self._closed:
                return None  # asked of a list that has since been replaced
            return provider(unit, job)

        def finished(built, error):
            callbacks = self._waiting.pop(key, [])
            if generation != self._generation or self._closed:
                return
            entry = self._make_entry(index, unit, built, error)
            if job == self._cache_job:
                self._cache[index] = entry
                self._set_status_flag(index, entry)
            for callback in callbacks:
                callback(entry)

        self._runner.submit(work, self._safely(finished), urgent)

    def _show_unit(self, index):
        """Put a unit in the viewer; its program arrives from the model."""
        self._current = index
        self._show_token += 1
        token = self._show_token
        if self._stale or self._refreshing:
            # The list is read again first; that shows this unit by name.
            self._show_reading(index, token)
            self._refresh()
            return
        job = self._job()
        self._use_job(job)
        if index not in self._cache:
            self._show_reading(index, token)

        def ready(entry):
            if token == self._show_token:
                self._present(entry)

        self._request_entry(index, job, ready, urgent=True)

    def _show_reading(self, index, token):
        name = u"{}".format(self._units[index].get("name") or "")
        self._clear_program()
        self._reading = token
        self.window.Cursor = Cursors.Wait
        self.view_title.Text = name
        self.empty_text.Text = u"Reading {} from the model...".format(name)
        self._banner(NEUTRAL_BG, MUTED, "Reading the program from the model", [])

        def tell():
            self.empty_text.Text = u"Reading {} from the model...\n\n{}".format(
                name, REVIT_BUSY
            )

        self._when_slow(lambda: self._reading == token, tell)

    def _present(self, entry):
        self._reading = None
        self.window.Cursor = None
        self._entry_now = entry
        self._selected = None
        self._selected_op = None
        self._hide_tip()
        self._update_checked_count()  # also clears any stale status text

        name = u"{}".format(entry["unit"].get("name") or "")
        restore, self._restore = self._restore, None
        self.view_title.Text = name
        if entry["error"]:
            self._clear_program()
            self.view_title.Text = name
            self.empty_text.Text = u"{} could not be built.\n\n{}".format(
                name, entry["error"]
            )
            self._banner(BAD_BG, BAD, "Program could not be built", [])
            return

        program = entry["program"]
        self.view_subtitle.Text = entry["filename"]
        self.empty_text.Visibility = Visibility.Collapsed
        self._box = preview.bounds(program["components"])
        self._fill_summary(entry)
        self._fill_components(entry)
        self._fill_member()
        self._fill_legend(entry)
        self._fit(rebuild=True)
        if restore is not None and restore["name"] == name:
            self._restore_view(restore)

    def _remember_view(self):
        """Keep the zoom and the selected member across a rebuild of the unit."""
        self._restore = None
        entry = self._entry_now
        if entry is None or entry["error"]:
            return
        comp = self._component()
        self._restore = {
            "name": u"{}".format(entry["unit"].get("name") or ""),
            "fitted": self._fitted,
            "view": (self._scale, self._tx, self._ty),
            "label": comp["label"] if comp is not None else None,
            "op": self._selected_op,
        }

    def _restore_view(self, restore):
        if not restore["fitted"]:
            scale, tx, ty = restore["view"]
            self._fitted = False
            self._set_view(scale, tx, ty)
        if restore["label"] is None:
            return
        for comp in self._entry_now["program"]["components"]:
            if comp["label"] == restore["label"]:
                op = restore["op"]
                if op is not None and op >= len(comp["ops"]):
                    op = None
                self._select(comp["index"], op, zoom=False)
                return

    def _clear_program(self):
        self._reading = None
        self.window.Cursor = None
        self._entry_now = None
        self._box = None
        self._selected = None
        self._selected_op = None
        self._op_points = []
        self.scene.Children.Clear()
        self.legend_panel.Children.Clear()
        self.op_totals.Children.Clear()
        self.view_title.Text = "No program shown"
        self.view_subtitle.Text = ""
        self.file_text.Text = ""
        self.readout.Text = ""
        for block in (
            self.metric_components,
            self.metric_operations,
            self.metric_length,
            self.metric_extents,
        ):
            block.Text = "-"
        self.empty_text.Text = "Click the eye beside a panel to see its CNC program."
        self.empty_text.Visibility = Visibility.Visible
        self._banner(NEUTRAL_BG, MUTED, "No program shown", [])
        self.component_grid.ItemsSource = None
        self._fill_member()

    # ---------------------------------------------------------- right: tables
    def _fill_summary(self, entry):
        summary = entry["summary"]
        self.file_text.Text = entry["filename"]
        self.metric_components.Text = str(summary["components"])
        self.metric_operations.Text = str(summary["op_total"])
        self.metric_length.Text = _thousands(summary["cut_length"])
        self.metric_extents.Text = u"{} x {}".format(
            preview.fmt_overall(summary["extents"][0]),
            preview.fmt_overall(summary["extents"][1]),
        )

        self.op_totals.Children.Clear()
        for name, count in summary["ops"]:
            self.op_totals.Children.Add(self._op_chip(name, count, CHIP_BG))

        problems = entry["program"]["problems"]
        errors, warnings = summary["errors"], summary["warnings"]
        if errors:
            title = "{} error{}".format(errors, "" if errors == 1 else "s")
            if warnings:
                title += ", {} warning{}".format(warnings, "" if warnings == 1 else "s")
            self._banner(BAD_BG, BAD, title + " in this program", problems)
        elif warnings:
            self._banner(
                WARN_BG,
                WARN,
                "{} warning{} in this program".format(
                    warnings, "" if warnings == 1 else "s"
                ),
                problems,
            )
        else:
            self._banner(
                GOOD_BG,
                GOOD,
                "Program checks passed: stations inside each length, "
                "lengths match end points",
                [],
            )
        self._fill_revit_check(entry)

    def _fill_revit_check(self, entry):
        revit = entry["revit"]
        lengths = [
            (comp, info.get("cut_length"))
            for comp, info in zip(entry["program"]["components"], revit)
        ]
        known = [(comp, cut) for comp, cut in lengths if cut is not None]
        if not known:
            self.revit_check_text.Visibility = Visibility.Collapsed
            return
        matching = sum(
            1
            for comp, cut in known
            if abs(cut - comp["length"]) <= REVIT_LENGTH_TOL_MM
        )
        self.revit_check_text.Text = (
            "Length against Revit Cut Length: {} of {} within {} mm".format(
                matching, len(known), preview.fmt(REVIT_LENGTH_TOL_MM)
            )
        )
        self.revit_check_text.Visibility = Visibility.Visible

    def _banner(self, background, foreground, title, problems):
        self.check_banner.Background = background
        self.check_title.Foreground = foreground
        self.check_title.Text = title
        self.revit_check_text.Visibility = Visibility.Collapsed
        self.problem_list.Children.Clear()
        for problem in problems:
            line = TextBlock()
            line.Text = problem["text"]
            line.TextWrapping = TextWrapping.Wrap
            line.FontSize = 11.5
            line.Margin = Thickness(0, 1, 0, 1)
            line.Foreground = BAD if problem["level"] == preview.ERROR else WARN
            if problem["component"] is not None:
                line.Cursor = Cursors.Hand
                line.Tag = problem["component"]
                line.MouseLeftButtonUp += self._guard(self._on_problem_click)
            self.problem_list.Children.Add(line)
        self.problem_scroll.Visibility = (
            Visibility.Visible if problems else Visibility.Collapsed
        )

    def _on_problem_click(self, sender, args):
        self._select(sender.Tag)

    def _fill_components(self, entry):
        components = entry["program"]["components"]
        revit = entry["revit"]
        table = _table(
            [
                (name, String)
                for name in (
                    "N", "Label", "Role", "Orient", "Length", "X0", "Y0", "X1",
                    "Y1", "Web", "Qty", "Ops", "Section", "RevitCut", "Delta",
                    "RevitId", "Flag",
                )
            ]
        )
        for comp in components:
            info = revit[comp["index"]] if revit else {}
            cut = info.get("cut_length")
            text = comp["text"]
            _add_row(
                table,
                [
                    str(comp["index"] + 1),
                    comp["label"],
                    comp["role"],
                    comp["orient"],
                    text["length"],
                    text["x0"],
                    text["y0"],
                    text["x1"],
                    text["y1"],
                    text["depth"],
                    comp["qty"],
                    str(len(comp["ops"])),
                    comp["section"],
                    preview.fmt(cut) if cut is not None else "",
                    _signed(cut - comp["length"]) if cut is not None else "",
                    u"{}".format(info.get("id") or ""),
                    entry["flags"].get(comp["index"], ""),
                ],
            )
        revit_columns = Visibility.Visible if revit else Visibility.Collapsed
        for column in self.component_grid.Columns:
            if column.Header in ("Revit cut length", "Difference", "Revit ID"):
                column.Visibility = revit_columns
            if column.Header != "#":
                # Size to this program, not to the widest one seen so far.
                column.Width = DataGridLength.Auto
        self.select_revit_button.Visibility = (
            Visibility.Visible
            if revit and self._on_select is not None
            else Visibility.Collapsed
        )
        self._syncing = True
        try:
            self.component_grid.ItemsSource = table.DefaultView
            self.component_grid.SelectedIndex = -1
        finally:
            self._syncing = False

    def _component(self, index=None):
        index = self._selected if index is None else index
        if self._entry_now is None or self._entry_now["error"] or index is None:
            return None
        components = self._entry_now["program"]["components"]
        if 0 <= index < len(components):
            return components[index]
        return None

    def _fill_member(self):
        comp = self._component()
        table = _table(
            [
                (name, String)
                for name in ("N", "Operation", "Color", "Station", "ToEnd", "Step")
            ]
        )
        if comp is None:
            self.member_title.Text = "Click a member in the drawing or a row above"
            self.member_title.Foreground = MUTED
            self.member_revit.Visibility = Visibility.Collapsed
            self.zoom_member_button.IsEnabled = False
            self.show_panel_button.Visibility = Visibility.Collapsed
            self.select_revit_button.IsEnabled = False
            self.row_text.Text = ""
        else:
            previous = None
            for number, op in enumerate(comp["ops"], 1):
                _add_row(
                    table,
                    [
                        str(number),
                        op["name"],
                        _op_style(op["name"])[0],
                        op["text"],
                        preview.fmt(comp["length"] - op["station"]),
                        "" if previous is None
                        else preview.fmt(op["station"] - previous),
                    ],
                )
                previous = op["station"]
            self.member_title.Text = u"{}   {}   {}   length {}   {} operation{}".format(
                comp["label"],
                comp["role"],
                comp["orient"],
                comp["text"]["length"],
                len(comp["ops"]),
                "" if len(comp["ops"]) == 1 else "s",
            )
            self.member_title.Foreground = TEXT
            self.zoom_member_button.IsEnabled = True
            self.show_panel_button.Visibility = Visibility.Visible
            self.row_text.Text = comp["raw"]
            self._fill_member_revit(comp)
        self._syncing = True
        try:
            self.operation_grid.ItemsSource = table.DefaultView
            self.operation_grid.SelectedIndex = (
                -1 if self._selected_op is None else self._selected_op
            )
        finally:
            self._syncing = False

    def _fill_member_revit(self, comp):
        revit = self._entry_now["revit"]
        if not revit:
            self.member_revit.Visibility = Visibility.Collapsed
            self.select_revit_button.IsEnabled = False
            return
        info = revit[comp["index"]]
        parts = [u"Revit element {}".format(info.get("id") or "?")]
        cut = info.get("cut_length")
        if cut is None:
            parts.append("no Cut Length parameter")
            self.member_revit.Foreground = MUTED
        else:
            difference = cut - comp["length"]
            if abs(difference) <= REVIT_LENGTH_TOL_MM:
                parts.append(
                    "Cut Length {} mm, matches the CSV length".format(preview.fmt(cut))
                )
                self.member_revit.Foreground = GOOD
            else:
                parts.append(
                    "Cut Length {} mm, CSV length differs by {} mm".format(
                        preview.fmt(cut), _signed(-difference)
                    )
                )
                self.member_revit.Foreground = WARN
        self.member_revit.Text = u"   ".join(parts)
        self.member_revit.Visibility = Visibility.Visible
        self.select_revit_button.IsEnabled = info.get("ref") is not None

    def _select(self, index, op_index=None, zoom=True):
        """Select a component (or None) everywhere: drawing and both tables.

        When a member is selected the canvas shows only that member. Pass
        zoom=False when restoring a saved view so the camera is left alone.
        """
        self._selected = index
        self._selected_op = op_index if index is not None else None
        self._syncing = True
        try:
            self.component_grid.SelectedIndex = -1 if index is None else index
            if index is not None and self.component_grid.SelectedItem is not None:
                self.component_grid.ScrollIntoView(self.component_grid.SelectedItem)
        finally:
            self._syncing = False
        self._fill_member()
        if op_index is not None and self.operation_grid.SelectedItem is not None:
            self.operation_grid.ScrollIntoView(self.operation_grid.SelectedItem)
        if zoom and index is not None:
            self._zoom_to_member(rebuild=True)
        elif zoom and index is None:
            self._fit(rebuild=True)
        else:
            self._rebuild_scene()

    def _on_component_row(self, sender, args):
        if self._syncing:
            return
        index = self.component_grid.SelectedIndex
        self._select(index if index >= 0 else None)

    def _on_operation_row(self, sender, args):
        if self._syncing:
            return
        index = self.operation_grid.SelectedIndex
        self._selected_op = index if index >= 0 else None
        self._rebuild_scene()

    def _on_select_in_revit(self, sender, args):
        comp = self._component()
        if comp is None or self._on_select is None or not self._entry_now["revit"]:
            return
        info = self._entry_now["revit"][comp["index"]]
        label, ident, ref = comp["label"], info.get("id"), info.get("ref")
        select = self._on_select
        pending = [True]

        def done(_result, error):
            pending[0] = False
            if error is not None:
                self._status(u"Could not select in Revit: {}".format(_message(error)))
            else:
                self._status(
                    u"{} selected in Revit (element {}).".format(label, ident)
                )

        self._status(u"Selecting {} in Revit...".format(label))
        self._when_slow(lambda: pending[0], lambda: self._status(REVIT_BUSY))
        self._runner.submit(lambda: select(ref), self._safely(done), urgent=True)

    # ------------------------------------------------------------ legend/chips
    def _op_icon(self, name):
        kind = _op_style(name)[1]
        brush = _op_brush(name)
        if kind in ("ring", "ring-small"):
            shape = Ellipse()
            size = 10 if kind == "ring" else 8
            shape.Width = shape.Height = size
            shape.Stroke = brush
            shape.StrokeThickness = 1.6
        elif kind == "dot":
            shape = Ellipse()
            shape.Width = shape.Height = 8
            shape.Fill = brush
        elif kind == "bar":
            shape = Rectangle()
            shape.Width, shape.Height = 2.5, 11
            shape.Fill = brush
        elif kind == "diamond":
            shape = Rectangle()
            shape.Width = shape.Height = 7
            shape.Fill = brush
            shape.RenderTransform = RotateTransform(45.0, 3.5, 3.5)
        else:
            shape = Rectangle()
            shape.Width, shape.Height = 9, 6
            shape.Fill = brush
        shape.VerticalAlignment = VerticalAlignment.Center
        shape.Margin = Thickness(0, 0, 6, 0)
        return shape

    def _op_chip(self, name, count, background=None):
        row = StackPanel()
        row.Orientation = StackOrientation.Horizontal
        row.Children.Add(self._op_icon(name))
        label = TextBlock()
        label.Text = name
        label.FontSize = 11.5
        label.Foreground = TEXT
        row.Children.Add(label)
        number = TextBlock()
        number.Text = str(count)
        number.FontSize = 11.5
        number.FontFamily = MONO
        number.FontWeight = FontWeights.SemiBold
        number.Foreground = TEXT
        number.Margin = Thickness(6, 0, 0, 0)
        row.Children.Add(number)
        if background is None:
            row.Margin = Thickness(0, 0, 16, 0)
            return row
        chip = Border()
        chip.Background = background
        chip.CornerRadius = CornerRadius(4)
        chip.Padding = Thickness(8, 3, 8, 3)
        chip.Margin = Thickness(0, 0, 6, 4)
        chip.Child = row
        return chip

    def _fill_legend(self, entry):
        self.legend_panel.Children.Clear()
        for name, count in entry["summary"]["ops"]:
            self.legend_panel.Children.Add(self._op_chip(name, count))
        note = TextBlock()
        note.Text = "heavy edge = web side"
        note.FontSize = 11.5
        note.Foreground = MUTED
        self.legend_panel.Children.Add(note)

    # ------------------------------------------------------------------ view
    def _viewport_size(self):
        return self.viewport.ActualWidth, self.viewport.ActualHeight

    def _fit(self, rebuild=False):
        box = self._box
        width, height = self._viewport_size()
        if box is None or width < 40 or height < 40:
            self._fitted = True
            return
        box_w = max(box[2] - box[0], 1.0)
        box_h = max(box[3] - box[1], 1.0)
        # Room for the overall dimensions (left, bottom) and outside labels.
        pad_left, pad_right, pad_top, pad_bottom = 72.0, 34.0, 34.0, 64.0
        room_w = max(width - pad_left - pad_right, 40.0)
        room_h = max(height - pad_top - pad_bottom, 40.0)
        scale = min(room_w / box_w, room_h / box_h, MAX_SCALE)
        self._fit_scale = scale
        self._set_view(
            scale,
            pad_left + (room_w - box_w * scale) / 2.0 - box[0] * scale,
            pad_top + (room_h - box_h * scale) / 2.0 + box[3] * scale,
            rebuild=rebuild,
        )
        self._fitted = True

    def _set_view(self, scale, tx, ty, rebuild=False):
        changed = abs(scale - self._scale) > 1e-12
        self._scale, self._tx, self._ty = scale, tx, ty
        self._translate.X = tx
        self._translate.Y = ty
        if changed or rebuild:
            self._rebuild_scene()
        self._update_readout(None)

    def _zoom_at(self, px, py, factor):
        if self._box is None:
            return
        low = self._fit_scale * 0.25
        scale = max(low, min(MAX_SCALE, self._scale * factor))
        if abs(scale - self._scale) < 1e-12:
            return
        ratio = scale / self._scale
        self._fitted = False
        self._set_view(
            scale, px - (px - self._tx) * ratio, py - (py - self._ty) * ratio
        )

    def _zoom_centre(self, factor):
        width, height = self._viewport_size()
        self._zoom_at(width / 2.0, height / 2.0, factor)

    def _zoom_to_member(self, rebuild=False):
        comp = self._component()
        width, height = self._viewport_size()
        if comp is None or width < 40 or height < 40:
            return
        box = preview.bounds([comp])
        box_w = max(box[2] - box[0], 1.0)
        box_h = max(box[3] - box[1], 1.0)
        scale = min((width - 160.0) / box_w, (height - 140.0) / box_h)
        scale = max(self._fit_scale * 0.25, min(MAX_SCALE, scale))
        self._fitted = False
        self._set_view(
            scale,
            width / 2.0 - (box[0] + box[2]) / 2.0 * scale,
            height / 2.0 + (box[1] + box[3]) / 2.0 * scale,
            rebuild=rebuild,
        )

    def _to_model(self, px, py):
        return (px - self._tx) / self._scale, -(py - self._ty) / self._scale

    def _update_readout(self, position):
        if self._box is None:
            self.readout.Text = ""
            return
        zoom = "zoom {}%".format(int(round(100.0 * self._scale / self._fit_scale)))
        if position is None:
            self.readout.Text = "wheel zoom   drag pan   double-click fit   " + zoom
            return
        x, y = self._to_model(position.X, position.Y)
        self.readout.Text = "X {}   Y {}   {}".format(
            preview.fmt(x), preview.fmt(y), zoom
        )

    def _on_viewport_resized(self, sender, args):
        if self._box is None:
            return
        if self._fitted:
            self._fit(rebuild=True)

    def _on_view_option(self, sender, args):
        self._rebuild_scene()

    # ----------------------------------------------------------------- mouse
    def _on_wheel(self, sender, args):
        position = args.GetPosition(self.viewport)
        self._zoom_at(
            position.X,
            position.Y,
            ZOOM_STEP if args.Delta > 0 else 1.0 / ZOOM_STEP,
        )
        self._update_readout(position)
        args.Handled = True

    def _on_mouse_down(self, sender, args):
        if self._box is None:
            return
        if args.ChangedButton not in (MouseButton.Left, MouseButton.Middle):
            return
        if args.ChangedButton == MouseButton.Left and args.ClickCount == 2:
            self._drag_start = None
            self._fit()
            return
        self._drag_start = args.GetPosition(self.viewport)
        self._drag_origin = (self._tx, self._ty)
        self._dragging = args.ChangedButton == MouseButton.Middle
        self.viewport.CaptureMouse()
        self._hide_tip()

    def _on_mouse_move(self, sender, args):
        position = args.GetPosition(self.viewport)
        if self._drag_start is not None:
            dx = position.X - self._drag_start.X
            dy = position.Y - self._drag_start.Y
            if not self._dragging and math.hypot(dx, dy) >= DRAG_THRESHOLD:
                self._dragging = True
            if self._dragging:
                self.viewport.Cursor = Cursors.SizeAll
                self._fitted = False
                self._tx = self._drag_origin[0] + dx
                self._ty = self._drag_origin[1] + dy
                self._translate.X = self._tx
                self._translate.Y = self._ty
        elif self._box is not None:
            self._hover(position)
        self._update_readout(position)

    def _on_mouse_up(self, sender, args):
        if self._drag_start is None:
            return
        was_drag = self._dragging
        button = args.ChangedButton
        self._drag_start = None
        self._dragging = False
        self.viewport.ReleaseMouseCapture()
        self.viewport.Cursor = Cursors.Cross
        if not was_drag and button == MouseButton.Left:
            self._click(args.GetPosition(self.viewport))

    def _on_capture_lost(self, sender, args):
        self._drag_start = None
        self._dragging = False
        self.viewport.Cursor = Cursors.Cross

    def _on_mouse_leave(self, sender, args):
        self._hide_tip()
        self._update_readout(None)

    def _op_under(self, position):
        """(component index, op index) of the nearest marker, or None."""
        if not self.tooling_toggle.IsChecked:
            return None
        sx, sy = position.X - self._tx, position.Y - self._ty
        best, best_distance = None, PICK_RADIUS
        for px, py, comp_index, op_index in self._op_points:
            distance = math.hypot(px - sx, py - sy)
            if distance < best_distance:
                best, best_distance = (comp_index, op_index), distance
        return best

    def _hover(self, position):
        hit = self._op_under(position)
        if hit is None:
            self._hide_tip()
            return
        comp = self._component(hit[0])
        op = comp["ops"][hit[1]]
        self.hover_text.Text = u"{}   {}   station {}".format(
            comp["label"], op["name"], op["text"]
        )
        self.hover_tip.Visibility = Visibility.Visible
        width, _height = self._viewport_size()
        self.hover_tip.Measure(Size(Double.PositiveInfinity, Double.PositiveInfinity))
        tip_w = self.hover_tip.DesiredSize.Width
        left = position.X + 14.0
        if left + tip_w > width - 6.0:
            left = position.X - tip_w - 10.0
        Canvas.SetLeft(self.hover_tip, max(4.0, left))
        Canvas.SetTop(self.hover_tip, max(4.0, position.Y - 30.0))

    def _hide_tip(self):
        self.hover_tip.Visibility = Visibility.Collapsed

    def _click(self, position):
        if self._entry_now is None or self._entry_now["error"]:
            return
        hit = self._op_under(position)
        if hit is not None:
            self._select(hit[0], hit[1])
            return
        x, y = self._to_model(position.X, position.Y)
        selected = self._component()
        # Only what is drawn can be picked; clicking off a lone member
        # brings the whole panel back.
        visible = (
            [selected] if selected is not None
            else self._entry_now["program"]["components"]
        )
        index = preview.hit_test(visible, x, y, 3.0 / self._scale)
        if index is None and selected is None:
            return
        self._select(index)

    # ----------------------------------------------------------------- scene
    def _pt(self, point):
        return Point(point[0] * self._scale, -point[1] * self._scale)

    def _add_path(self, geometry, fill=None, stroke=None, thickness=1.0):
        geometry.Freeze()
        path = ShapePath()
        path.Data = geometry
        path.Fill = fill
        path.Stroke = stroke
        path.StrokeThickness = thickness
        path.StrokeLineJoin = PenLineJoin.Miter
        path.StrokeEndLineCap = PenLineCap.Flat
        path.IsHitTestVisible = False
        self.scene.Children.Add(path)
        return path

    def _outlines(self, components, fill, thickness, edge, shaped=False):
        """Member outlines, then a heavier line on each web side.

        ``shaped`` steps swages into the outline, cuts the notches out of
        it (their sides leaning with the member they seat) and tints the
        material the tooling removes.
        """
        if not components:
            return
        others = self._entry_now["program"]["components"] if shaped else None
        min_inset = 2.0 / self._scale
        members = GeometryGroup()
        members.FillRule = FillRule.Nonzero
        webs = StreamGeometry()
        web_context = webs.Open()
        groups = {}
        for comp in components:
            if shaped:
                bands = preview.cuts(comp, min_inset, others)
                corners, web = preview.profile(comp, bands)
            else:
                bands = []
                corners = preview.outline(comp)
                web = list(preview.web_edge(comp))
            body = self._polygons([corners])
            notches = [
                preview.band_polygon(comp, band)
                for band in bands if band["name"] in preview.NOTCHES
            ]
            if notches:
                body = Geometry.Combine(
                    body, self._polygons(notches), GeometryCombineMode.Exclude, None
                )
            members.Children.Add(body)
            web_context.BeginFigure(self._pt(web[0]), False, False)
            for point in web[1:]:
                web_context.LineTo(self._pt(point), True, False)
            for band in bands:
                groups.setdefault(band["name"], []).append(
                    preview.band_polygon(comp, band)
                )
        web_context.Close()

        self._add_path(members, fill, INK, thickness)
        if shaped:
            # Clipped to the members, so the line stops at each notch; drawn
            # twice as wide because the clip keeps only its inner half.
            line = self._add_path(webs, None, INK, edge * 2.0)
            line.Clip = members
        else:
            self._add_path(webs, None, INK, edge)
        for name in sorted(groups):
            self._add_path(self._polygons(groups[name]), _cut_brush(name))

    def _polygons(self, polygons):
        geometry = StreamGeometry()
        geometry.FillRule = FillRule.Nonzero
        context = geometry.Open()
        for corners in polygons:
            context.BeginFigure(self._pt(corners[0]), True, True)
            for corner in corners[1:]:
                context.LineTo(self._pt(corner), True, False)
        context.Close()
        return geometry

    def _rebuild_scene(self):
        self.scene.Children.Clear()
        self._op_points = []
        self._boxes = []
        entry = self._entry_now
        if entry is None or entry["error"] or self._box is None:
            return
        components = entry["program"]["components"]
        selected = self._component()
        # A selected member is shown alone so its tooling can be inspected;
        # clearing the selection brings the whole panel back.
        visible = [selected] if selected is not None else components

        # Studs and webs first, then tracks and chords over their ends.
        fill = SELECT_FILL if selected is not None else PAPER
        thickness = 1.6 if selected is not None else 1.0
        edge = 3.0 if selected is not None else 2.2
        shaped = bool(self.tooling_toggle.IsChecked)
        for layer in (0, 1, 2):
            members = [c for c in visible if preview.draw_layer(c) == layer]
            self._outlines(members, fill, thickness, edge, shaped)

        if self.dimensions_toggle.IsChecked:
            self._draw_dimensions(visible)
        if self.tooling_toggle.IsChecked:
            self._draw_operations(visible, selected)
        if self.labels_toggle.IsChecked:
            self._draw_labels(visible, selected)

    # -- operations
    def _frame(self, comp):
        """Screen-space unit vectors: along the member, and toward its lips."""
        side = comp["open_sign"]
        return (
            comp["ux"],
            -comp["uy"],
            comp["nx"] * side,
            -comp["ny"] * side,
        )

    def _draw_operations(self, components, selected):
        scale = self._scale
        groups = {}
        selected_points = []
        for comp in components:
            if not comp["ops"]:
                continue
            ux, uy, nx, ny = self._frame(comp)
            half = comp["flange"] * scale / 2.0
            radius = max(3.0, min(5.5, half * 0.35))
            depth = max(3.5, min(8.0, half * 0.7))
            for op_index, op in enumerate(comp["ops"]):
                px, py = preview.point_at(comp, op["station"])
                cx, cy = px * scale, -py * scale
                kind = _op_style(op["name"])[1]
                mx, my = cx, cy
                if kind == "lip":
                    mx, my = cx + nx * (half - depth / 2.0), cy + ny * (half - depth / 2.0)
                elif kind == "web":
                    mx, my = cx - nx * (half - depth / 2.0), cy - ny * (half - depth / 2.0)
                # Notches and swages are drawn into the member outline.
                if kind not in _SHAPED:
                    groups.setdefault(op["name"], []).append(
                        (kind, cx, cy, ux, uy, nx, ny, half, radius, depth)
                    )
                self._op_points.append((mx, my, comp["index"], op_index))
                # Text is never written over a marker.
                self._boxes.append(
                    (mx - radius, my - radius, mx + radius, my + radius)
                )
                if comp is selected:
                    selected_points.append((mx, my, nx, ny, half, op, op_index))

        for name in sorted(groups):
            kind = groups[name][0][0]
            geometry = StreamGeometry()
            geometry.FillRule = FillRule.Nonzero
            context = geometry.Open()
            for marker in groups[name]:
                self._marker(context, *marker)
            context.Close()
            brush = _op_brush(name)
            if kind in _FILLED:
                self._add_path(geometry, brush, PAPER, 0.6)
            else:
                self._add_path(geometry, None, brush, 2.0 if kind == "bar" else 1.6)

        # The selected operation gets a ring; its member gets station values.
        for mx, my, nx, ny, half, op, op_index in selected_points:
            if op_index == self._selected_op:
                ring = Ellipse()
                ring.Width = ring.Height = 22.0
                ring.Stroke = INK
                ring.StrokeThickness = 1.8
                ring.IsHitTestVisible = False
                Canvas.SetLeft(ring, mx - 11.0)
                Canvas.SetTop(ring, my - 11.0)
                self.scene.Children.Add(ring)

        show_all = bool(self.stations_toggle.IsChecked)
        if selected is not None:
            self._station_texts(selected, TEXT, True)
        if show_all:
            for comp in components:
                if comp is not selected:
                    self._station_texts(comp, INK_SOFT, False)

    def _station_texts(self, comp, brush, strong):
        scale = self._scale
        _ux, _uy, nx, ny = self._frame(comp)
        half = comp["flange"] * scale / 2.0
        last_text = None
        for op in comp["ops"]:
            # Two operations at one station share a single value.
            if op["text"] == last_text:
                continue
            px, py = preview.point_at(comp, op["station"])
            text = op["text"]
            est_w, est_h = len(text) * 6.4 + 4.0, 13.0
            offset = half + 6.0 + (est_w / 2.0) * abs(nx) + (est_h / 2.0) * abs(ny)
            cx = px * scale + nx * offset
            cy = -py * scale + ny * offset
            if not self._reserve(cx, cy, est_w / 2.0, est_h / 2.0):
                continue
            last_text = text
            block = self._text(
                text, 10.5, brush, mono=True, strong=strong, masked=True
            )
            self._place(block, cx, cy)

    def _marker(self, context, kind, cx, cy, ux, uy, nx, ny, half, radius, depth):
        if kind == "dot":
            self._circle(context, cx, cy, radius, True)
        elif kind == "ring":
            self._circle(context, cx, cy, max(radius * 1.7, min(half * 0.7, 9.0)), False)
        elif kind == "ring-small":
            self._circle(context, cx, cy, radius * 1.25, False)
        elif kind == "bar":
            context.BeginFigure(Point(cx - nx * half, cy - ny * half), False, False)
            context.LineTo(Point(cx + nx * half, cy + ny * half), True, False)
        elif kind in ("lip", "web"):
            side = 1.0 if kind == "lip" else -1.0
            ex, ey = cx + nx * half * side, cy + ny * half * side
            along = max(radius, 3.0)
            ix, iy = -nx * depth * side, -ny * depth * side
            context.BeginFigure(Point(ex - ux * along, ey - uy * along), True, True)
            context.LineTo(Point(ex + ux * along, ey + uy * along), True, False)
            context.LineTo(Point(ex + ux * along + ix, ey + uy * along + iy), True, False)
            context.LineTo(Point(ex - ux * along + ix, ey - uy * along + iy), True, False)
        elif kind == "diamond":
            size = radius * 1.5
            context.BeginFigure(Point(cx - size, cy), True, True)
            context.LineTo(Point(cx, cy - size), True, False)
            context.LineTo(Point(cx + size, cy), True, False)
            context.LineTo(Point(cx, cy + size), True, False)
        else:
            size = radius
            context.BeginFigure(Point(cx - size, cy - size), False, False)
            context.LineTo(Point(cx + size, cy + size), True, False)
            context.BeginFigure(Point(cx - size, cy + size), False, False)
            context.LineTo(Point(cx + size, cy - size), True, False)

    @staticmethod
    def _circle(context, cx, cy, radius, filled):
        size = Size(radius, radius)
        context.BeginFigure(Point(cx - radius, cy), filled, True)
        context.ArcTo(
            Point(cx + radius, cy), size, 0.0, False, SweepDirection.Clockwise, True, False
        )
        context.ArcTo(
            Point(cx - radius, cy), size, 0.0, False, SweepDirection.Clockwise, True, False
        )

    # -- text
    def _text(self, text, size, brush, mono=False, strong=False, masked=False):
        block = TextBlock()
        block.Text = text
        block.FontSize = size
        block.Foreground = brush
        block.IsHitTestVisible = False
        if mono:
            block.FontFamily = MONO
        if strong:
            block.FontWeight = FontWeights.SemiBold
        if masked:
            # Keeps the value readable where it crosses another member.
            block.Background = PAPER
            block.Padding = Thickness(2, 0, 2, 0)
        block.Measure(Size(Double.PositiveInfinity, Double.PositiveInfinity))
        return block

    def _place(self, block, cx, cy, angle=0.0):
        width, height = block.DesiredSize.Width, block.DesiredSize.Height
        if angle:
            block.RenderTransform = RotateTransform(angle, width / 2.0, height / 2.0)
        Canvas.SetLeft(block, cx - width / 2.0)
        Canvas.SetTop(block, cy - height / 2.0)
        self.scene.Children.Add(block)

    def _reserve(self, cx, cy, half_w, half_h, force=False):
        """Claim a screen rectangle for text; False when it would overlap."""
        box = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        if not force:
            for other in self._boxes:
                if (
                    box[0] < other[2]
                    and box[2] > other[0]
                    and box[1] < other[3]
                    and box[3] > other[1]
                ):
                    return False
        self._boxes.append(box)
        return True

    def _draw_labels(self, components, selected):
        scale = self._scale
        ordered = sorted(components, key=lambda c: (c is not selected, -c["span"]))
        for comp in ordered:
            strong = comp is selected
            half = comp["flange"] * scale / 2.0
            outside = 2.0 * half < 17.0
            block = self._text(comp["label"], 11.0, TEXT, strong=strong, masked=outside)
            width, height = block.DesiredSize.Width, block.DesiredSize.Height
            if not strong and comp["span"] * scale < width + 10.0:
                continue
            ux, uy, nx, ny = self._frame(comp)
            angle = math.degrees(math.atan2(uy, ux))
            if angle >= 90.0:
                angle -= 180.0
            elif angle < -90.0:
                angle += 180.0
            mid_x, mid_y = preview.point_at(comp, comp["span"] / 2.0)
            cx, cy = mid_x * scale, -mid_y * scale
            if outside:
                # Too thin to hold its label: write it beside the web edge,
                # which is the outside face of a track, chord or end stud.
                offset = half + height / 2.0 + 3.0
                cx, cy = cx - nx * offset, cy - ny * offset
            radians = math.radians(angle)
            cos_a, sin_a = abs(math.cos(radians)), abs(math.sin(radians))
            half_w = (cos_a * width + sin_a * height) / 2.0 + 2.0
            half_h = (sin_a * width + cos_a * height) / 2.0 + 2.0
            if not self._reserve(cx, cy, half_w, half_h, force=strong):
                continue
            self._place(block, cx, cy, angle)

    # -- dimensions
    def _draw_dimensions(self, components):
        box = preview.overall_bounds(components)
        if box is None:
            return
        scale = self._scale
        left, right = box[0] * scale, box[2] * scale
        top, bottom = -box[3] * scale, -box[1] * scale
        gap, reach, tick = 10.0, 34.0, 5.0

        geometry = StreamGeometry()
        context = geometry.Open()

        def line(x0, y0, x1, y1):
            context.BeginFigure(Point(x0, y0), False, False)
            context.LineTo(Point(x1, y1), True, False)

        # Overall width, below the panel.
        y = bottom + reach
        line(left, bottom + gap, left, y + 6.0)
        line(right, bottom + gap, right, y + 6.0)
        line(left, y, right, y)
        line(left - tick, y + tick, left + tick, y - tick)
        line(right - tick, y + tick, right + tick, y - tick)
        # Overall height, left of the panel.
        x = left - reach
        line(left - gap, top, x - 6.0, top)
        line(left - gap, bottom, x - 6.0, bottom)
        line(x, top, x, bottom)
        line(x - tick, top + tick, x + tick, top - tick)
        line(x - tick, bottom + tick, x + tick, bottom - tick)
        context.Close()
        self._add_path(geometry, None, DIM, 1.0)

        width_text = self._text(
            preview.fmt_overall(box[2] - box[0]), 11.0, INK_SOFT, mono=True
        )
        cx, cy = (left + right) / 2.0, y + 11.0
        self._reserve(cx, cy, width_text.DesiredSize.Width / 2.0, 8.0, force=True)
        self._place(width_text, cx, cy)

        height_text = self._text(
            preview.fmt_overall(box[3] - box[1]), 11.0, INK_SOFT, mono=True
        )
        cx, cy = x - 11.0, (top + bottom) / 2.0
        self._reserve(cx, cy, 8.0, height_text.DesiredSize.Width / 2.0, force=True)
        self._place(height_text, cx, cy, -90.0)

    # --------------------------------------------------------- header actions
    def _show_folder(self):
        self.folder_text.Text = self._out_dir
        self.folder_text.ToolTip = self._out_dir

    def _on_change_folder(self, sender, args):
        if self._pick_folder is None:
            return
        try:
            picked = self._pick_folder()
        except Exception as ex:
            self._status(u"Could not open the folder picker: {}".format(_message(ex)))
            return
        if picked:
            self._out_dir = picked
            self._show_folder()

    def _on_window_key(self, sender, args):
        if args.Key == Key.Escape and self._component() is not None:
            self._select(None)
            args.Handled = True

    def _on_job_key(self, sender, args):
        if args.Key == Key.Enter:
            self._on_job_committed(sender, args)
            args.Handled = True

    def _on_job_committed(self, sender, args):
        """A new job name changes the DETAILS row, so refresh what is shown."""
        if self._current is None or self._job() == self._cache_job:
            return
        if self._verify_timer is not None:
            self._stop_verify("Stopped - the job name changed.")
        self._remember_view()
        self._show_unit(self._current)

    def _status(self, text):
        self.status_text.Text = text

    def _pump(self):
        """Let the window repaint during a long loop (no input is handled)."""
        self.window.Dispatcher.Invoke(DispatcherPriority.Render, Action(lambda: None))

    # ------------------------------------------------- following the model
    def mark_stale(self):
        """The model changed in Revit: what is shown may no longer be true.

        Nothing is read here, so this is safe to call while Revit is in the
        middle of a change. The panels are read again when the window is next
        used; until then a bar across the top says so.
        """
        if self._closed or self._rescan is None:
            return
        self._stale = True
        if self._exporting is not None:
            self._abort_export(
                "The model changed while the programs were being built. "
                "Export stopped - press Export again."
            )
        if self._refreshing:
            return
        if self._in_use():
            self._refresh()
        else:
            self._show_model_bar(
                "The Revit model has changed. The panels are read again when "
                "you come back to this window.",
                True,
            )

    def _in_use(self):
        """True while this window, not Revit, is the one being worked in."""
        return bool(self.window.IsActive)

    def _on_activated(self, sender, args):
        if self._stale and not self._refreshing:
            self._refresh()

    def _show_model_bar(self, text, can_refresh):
        self.model_bar_text.Text = text
        self.model_bar_button.Visibility = (
            Visibility.Visible if can_refresh else Visibility.Collapsed
        )
        self.model_bar.Visibility = Visibility.Visible

    def _refresh(self, then=None):
        """Read the panels from the model again and rebuild what is shown.

        ``then`` runs once the new list is in place.
        """
        if self._rescan is None or self._closed:
            return
        if then is not None:
            self._after_refresh = then
        if self._refreshing:
            return
        self._refreshing = True
        if self._verify_timer is not None:
            self._stop_verify("Stopped - the panels are being read again.")
        if self._exporting is not None:
            self._abort_export(
                "Export stopped - the panels are being read again. Press Export again."
            )
        # Programs still on their way were built from the list being replaced.
        self._generation += 1
        self._waiting.clear()
        generation = self._generation
        rescan = self._rescan
        self._show_model_bar("Reading the panels from Revit...", False)
        self._when_slow(
            lambda: self._refreshing and generation == self._generation,
            lambda: self._show_model_bar(REVIT_BUSY, False),
        )

        def work():
            self._stale = False  # a change after this moment needs another read
            return rescan()

        def done(units, error):
            self._refreshed(units, error)

        self._runner.submit(work, self._safely(done), urgent=True)

    def _refreshed(self, units, error):
        self._refreshing = False
        if self._closed:
            return
        then, self._after_refresh = self._after_refresh, None
        if error is not None:
            self._stale = True
            self._show_model_bar(
                u"The panels could not be read from Revit: {}".format(_message(error)),
                True,
            )
            return

        old_units = self._units
        ticked = set(old_units[index].get("name") for index in self._checked_indexes())
        current = None
        if self._current is not None and self._current < len(old_units):
            current = old_units[self._current].get("name")
        self._remember_view()

        self._units = list(units)
        self._cache.clear()
        self._current = None
        self._build_unit_table(ticked)
        self._on_search(None, None)  # the search text still applies
        self.model_bar.Visibility = Visibility.Collapsed
        self._update_checked_count()
        self.verify_text.Text = "Panels read from Revit at {}.".format(
            DateTime.Now.ToString("HH:mm:ss")
        )

        index = None
        if current is not None:
            for position, unit in enumerate(self._units):
                if unit.get("name") == current:
                    index = position
                    break
        if index is None:
            self._restore = None
            self._clear_program()
            if current is not None:
                self.empty_text.Text = u"{} is no longer in the model.".format(current)
        else:
            self._highlight(index)
            self._show_unit(index)
        if then is not None:
            then()

    def _highlight(self, index):
        """Mark a unit in the list without asking for it to be shown."""
        self._syncing = True
        try:
            item = self._row_view(index)
            if item is not None:
                self.unit_list.SelectedItem = item
                self.unit_list.ScrollIntoView(item)
        finally:
            self._syncing = False

    # ------------------------------------------------------------ verify all
    def _on_verify(self, sender, args):
        if self._verify_timer is not None:
            self._stop_verify("Stopped.")
        elif self._stale or self._refreshing:
            self._refresh(then=self._start_verify)
        else:
            self._start_verify()

    def _start_verify(self):
        self._verify_queue = list(range(len(self._units)))
        self._verify_total = len(self._verify_queue)
        self._verify_flight = 0
        self._verify_token += 1
        self._verify_job = self._job()
        self.verify_button.Content = "Stop"
        timer = DispatcherTimer(DispatcherPriority.Background)
        timer.Interval = TimeSpan.FromMilliseconds(1)
        timer.Tick += self._guard(self._on_verify_tick)
        self._verify_timer = timer
        timer.Start()

    def _on_verify_tick(self, sender, args):
        """Ask for the next program, a few at a time."""
        if not self._verify_queue:
            if not self._verify_flight:
                self._stop_verify(None)
            return
        if self._verify_flight >= VERIFY_IN_FLIGHT:
            return
        index = self._verify_queue.pop(0)
        token = self._verify_token
        self._verify_flight += 1

        def checked(_entry):
            if token != self._verify_token:
                return
            self._verify_flight -= 1
            done = self._verify_total - len(self._verify_queue) - self._verify_flight
            self.verify_text.Text = "Checking {} of {}...".format(
                done, self._verify_total
            )
            if not self._verify_queue and not self._verify_flight:
                self._stop_verify(None)

        self._request_entry(index, self._verify_job, checked)

    def _stop_verify(self, note):
        if self._verify_timer is not None:
            self._verify_timer.Stop()
            self._verify_timer = None
        self._verify_token += 1  # programs still on their way no longer count
        self._verify_flight = 0
        self.verify_button.Content = "Run checks on every program"
        if note is None:
            counts = {"ok": 0, "warning": 0, "error": 0, "failed": 0}
            for index in range(len(self._units)):
                flag = self._unit_row(index)["Status"]
                if flag in counts:
                    counts[flag] += 1
            note = "{} passed, {} with warnings, {} with errors".format(
                counts["ok"], counts["warning"], counts["error"] + counts["failed"]
            )
        self.verify_text.Text = note

    # ---------------------------------------------------------------- export
    def _on_export(self, sender, args):
        if self._exporting is not None or not self._checked_indexes():
            return
        if self._stale or self._refreshing:
            # Export what is in the model now, not what was read earlier.
            self._refresh(then=self._start_export)
        else:
            self._start_export()

    def _start_export(self):
        """Build the program of every ticked unit, then write them."""
        indexes = self._checked_indexes()
        if not indexes or self._exporting is not None:
            return
        if self._verify_timer is not None:
            self._stop_verify("Stopped.")
        job = self._job()
        state = {"job": job, "indexes": indexes, "entries": {}}
        self._exporting = state
        self.window.Cursor = Cursors.Wait
        self.export_button.IsEnabled = False
        self._status("Building program 1 of {}...".format(len(indexes)))
        self._when_slow(
            lambda: self._exporting is state and not state["entries"],
            lambda: self._status(REVIT_BUSY),
        )

        def collect(index):
            return lambda entry: self._export_built(state, index, entry)

        for index in indexes:
            if self._exporting is not state:
                return
            self._request_entry(index, job, collect(index))

    def _export_built(self, state, index, entry):
        if self._exporting is not state:
            return
        state["entries"][index] = entry
        built, total = len(state["entries"]), len(state["indexes"])
        if built < total:
            self._status("Building program {} of {}...".format(built + 1, total))
            self._pump()
            return
        entries = [state["entries"][i] for i in state["indexes"]]
        # Asking and writing happen outside the model call that ended here.
        self._after(lambda: self._finish_export(state, entries))

    def _abort_export(self, note):
        self._exporting = None
        self.window.Cursor = None
        self._update_checked_count()
        self._status(note)

    def _finish_export(self, state, entries):
        if self._exporting is not state or self._closed:
            return
        job = state["job"]
        self._exporting = None
        self.window.Cursor = None
        self._update_checked_count()

        flagged = [
            u"{}".format(e["unit"].get("name"))
            for e in entries
            if e["error"] or e["summary"]["errors"]
        ]
        if flagged:
            shown = u", ".join(flagged[:8])
            if len(flagged) > 8:
                shown += u" and {} more".format(len(flagged) - 8)
            answer = MessageBox.Show(
                self.window,
                u"{} of {} programs have errors:\n{}\n\n"
                u"Export anyway? Programs that could not be built are skipped.".format(
                    len(flagged), len(entries), shown
                ),
                "UNIQUBE - Generate CNC",
                MessageBoxButton.YesNo,
                MessageBoxImage.Warning,
            )
            if answer != MessageBoxResult.Yes:
                self._status("Nothing was exported.")
                return

        result = {"job_name": job, "out_dir": self._out_dir, "items": entries}
        self.result = result
        if self._on_export_cb is None:
            # Shown as a dialog: the caller writes the files once it closes.
            self.window.Close()
            return
        self._status("Writing {} program{}...".format(
            len(entries), "" if len(entries) == 1 else "s"
        ))
        self._pump()
        try:
            report = self._on_export_cb(result)
        except Exception as ex:
            self._status(u"The programs could not be written: {}".format(_message(ex)))
            return
        self._show_report(report)

    # --------------------------------------------------------- export report
    def _show_report(self, report):
        """Say what was written, over the window, until it is dismissed."""
        self.report = report
        info = files.describe(report)
        if info["ok"]:
            self.report_dot.Fill = GOOD
        elif report["written"]:
            self.report_dot.Fill = WARN
        else:
            self.report_dot.Fill = BAD
        self.report_title.Text = info["title"]
        self.report_summary.Text = info["summary"]
        self.report_details.Text = info["details"]
        self.report_details.Visibility = (
            Visibility.Visible if info["details"] else Visibility.Collapsed
        )
        self.report_overlay.Visibility = Visibility.Visible
        self.report_close_button.Focus()
        self._status(info["summary"].split("\n")[0])

    def _hide_report(self):
        self.report_overlay.Visibility = Visibility.Collapsed

    def _on_open_folder(self, sender, args):
        if self.report is None:
            return
        try:
            _process_class().Start(
                "explorer.exe", u'"{}"'.format(self.report["out_dir"])
            )
        except Exception as ex:
            self._status(u"Could not open the folder: {}".format(_message(ex)))

    # ------------------------------------------------------------- lifecycle
    def _on_loaded(self, sender, args):
        if not self._units:
            return
        checked = self._checked_indexes()
        first = checked[0] if checked else 0
        item = self._row_view(first)
        if item is not None:
            self.unit_list.SelectedItem = item
            self.unit_list.ScrollIntoView(item)

    def _on_closed(self, sender, args):
        self._closed = True
        self._exporting = None
        self._waiting.clear()
        if self._verify_timer is not None:
            self._verify_timer.Stop()
            self._verify_timer = None
        if self._on_closed_cb is not None:
            self._on_closed_cb()
