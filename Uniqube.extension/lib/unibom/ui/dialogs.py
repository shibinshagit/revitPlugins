# -*- coding: utf-8 -*-
"""UNIBOM Run BOQ window (WPF). Product chrome; Revit stays usable when modeless."""
from __future__ import print_function

import io
import json
import os

import clr

clr.AddReference("PresentationFramework")
clr.AddReference("PresentationCore")
clr.AddReference("WindowsBase")
clr.AddReference("System.Xaml")

from System.IO import File
from System.Windows import Visibility, WindowState
from System.Windows.Interop import WindowInteropHelper
from System.Windows.Markup import XamlReader

import uniqube_brand as brand
from uniqube_cnc_window import ImmediateRunner

UI_DIR = os.path.dirname(os.path.abspath(__file__))
XAML_NAME = "RunBoqDialog.xaml"
PREFS_PATH = os.path.join(
    os.environ.get("APPDATA") or os.path.expanduser("~"), "UNIBOM", "preferences.json")

SCOPE_MODEL, SCOPE_VIEW, SCOPE_SELECTION = "model", "view", "selection"
METHOD_COUNTED, METHOD_RECIPE, METHOD_BOTH = "counted", "recipe", "both"


def load_prefs():
    try:
        with io.open(PREFS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_prefs(prefs):
    try:
        folder = os.path.dirname(PREFS_PATH)
        if not os.path.isdir(folder):
            os.makedirs(folder)
        with io.open(PREFS_PATH, "w", encoding="utf-8") as fh:
            fh.write(u"" + json.dumps(prefs, indent=2))
    except Exception:
        pass


class RunBoqOptions(object):
    def __init__(self, scope, method, csv, html, folder):
        self.scope = scope
        self.method = method
        self.csv = csv
        self.html = html
        self.folder = folder


class RunBoqDialog(object):
    """Scope, method and output choices for Run BOQ.

    Shown beside Revit when ``stay_open`` (default). ``on_run(options)`` must
    return a report dict; the window asks the runner to call it in Revit.
    """

    def __init__(
        self,
        model_title,
        view_name,
        selection_count,
        default_folder,
        owner_handle=None,
        runner=None,
        on_run=None,
        on_closed=None,
        stay_open=True,
        pick_folder=None,
        xaml_path=None,
    ):
        self.result = None
        self.errors = []
        self._closed = False
        self._running = False
        self._on_run = on_run
        self._on_closed = on_closed
        self._pick_folder = pick_folder
        self._runner = runner if runner is not None else ImmediateRunner()
        self._stay_open = stay_open
        self._default_folder = default_folder or ""

        if xaml_path is None:
            xaml_path = os.path.join(UI_DIR, XAML_NAME)
        self.window = XamlReader.Parse(File.ReadAllText(xaml_path))
        if owner_handle:
            try:
                WindowInteropHelper(self.window).Owner = owner_handle
            except Exception:
                pass

        find = self.window.FindName
        self.header_bar = find("HeaderBar")
        self.brand_logo = find("BrandLogo")
        self.model_text = find("model_text")
        self.view_text = find("view_text")
        self.selection_text = find("selection_text")
        self.rb_model = find("rb_model")
        self.rb_view = find("rb_view")
        self.rb_selection = find("rb_selection")
        self.rb_counted = find("rb_counted")
        self.rb_recipe = find("rb_recipe")
        self.rb_both = find("rb_both")
        self.cb_csv = find("cb_csv")
        self.cb_html = find("cb_html")
        self.folder_text = find("folder_text")
        self.status_text = find("status_text")
        self.cancel_button = find("cancel_button")
        self.run_button = find("run_button")
        self.folder_button = find("folder_button")
        self.report_overlay = find("ReportOverlay")
        self.report_dot = find("ReportDot")
        self.report_title = find("ReportTitle")
        self.report_summary = find("ReportSummary")
        self.report_open_button = find("ReportOpenButton")
        self.report_close_button = find("ReportCloseButton")
        self.model_bar = find("ModelBar")
        self.model_bar_text = find("ModelBarText")

        brand.load_logo(self.brand_logo, dark=True, header=True)
        self.model_text.Text = model_title or "Model"
        if view_name:
            self.view_text.Text = u"Only what '{}' shows.".format(view_name)
        if selection_count:
            self.selection_text.Text = (
                u"{} element(s) selected. Walls bring their whole panel.".format(
                    selection_count))
            self.rb_selection.IsChecked = True
        else:
            self.rb_selection.IsEnabled = False

        prefs = load_prefs()
        method = prefs.get("method", METHOD_COUNTED)
        {METHOD_COUNTED: self.rb_counted, METHOD_RECIPE: self.rb_recipe,
         METHOD_BOTH: self.rb_both}.get(method, self.rb_counted).IsChecked = True
        self.cb_csv.IsChecked = prefs.get("csv", True)
        self.cb_html.IsChecked = prefs.get("html", True)
        folder = prefs.get("folder") or default_folder
        self.folder_text.Text = folder if os.path.isdir(folder) else default_folder

        if self.header_bar is not None:
            self.header_bar.MouseLeftButtonDown += self._guard(self._header_drag)
        self.folder_button.Click += self._guard(self._folder_click)
        self.cancel_button.Click += self._guard(self._cancel_click)
        self.run_button.Click += self._guard(self._run_click)
        self.report_open_button.Click += self._guard(self._open_folder)
        self.report_close_button.Click += self._guard(lambda s, e: self._hide_report())
        self.window.Closed += self._guard(self._on_closed_event)

    # --------------------------------------------------------------- helpers
    def _fault(self, ex):
        try:
            text = u"{}".format(ex)
        except Exception:
            text = "unknown error"
        self.errors.append(text)
        self.status_text.Text = u"Something went wrong: {}".format(text)

    def _guard(self, handler):
        def guarded(sender, args):
            try:
                handler(sender, args)
            except Exception as ex:
                self._fault(ex)
        return guarded

    def _safely(self, callback):
        def safe(*args):
            try:
                callback(*args)
            except Exception as ex:
                self._fault(ex)
        return safe

    # ---------------------------------------------------------------- show
    def show(self):
        self.cancel_button.Content = "Close"
        self.cancel_button.IsCancel = False
        self.window.Show()

    def show_dialog(self, notice=None):
        if notice and self.model_bar is not None:
            self.model_bar_text.Text = notice
            self.model_bar.Visibility = Visibility.Visible
        if self._on_run is not None:
            self.cancel_button.Content = "Close"
        self.window.ShowDialog()
        return self.result

    def activate(self):
        if self.window.WindowState == WindowState.Minimized:
            self.window.WindowState = WindowState.Normal
        self.window.Activate()

    def close(self):
        if not self._closed:
            self.window.Close()

    @property
    def is_open(self):
        return not self._closed

    # --------------------------------------------------------------- events
    def _header_drag(self, sender, args):
        try:
            # Only drag from the chrome, not from cards / buttons.
            if args.LeftButton.ToString() == "Pressed":
                self.window.DragMove()
        except Exception:
            pass

    def _folder_click(self, sender, args):
        pick = self._pick_folder
        if pick is None:
            try:
                from pyrevit import forms
                pick = lambda: forms.pick_folder(title="UNIBOM - output folder")
            except Exception:
                return

        def work():
            return pick()

        def done(picked, error):
            if error is not None:
                self.status_text.Text = u"Could not open the folder picker: {}".format(error)
                return
            if picked:
                self.folder_text.Text = picked

        self._runner.submit(work, self._safely(done), urgent=True)

    def _cancel_click(self, sender, args):
        self.result = None
        self.window.Close()

    def _options(self):
        if self.rb_selection.IsChecked:
            scope = SCOPE_SELECTION
        elif self.rb_view.IsChecked:
            scope = SCOPE_VIEW
        else:
            scope = SCOPE_MODEL
        if self.rb_recipe.IsChecked:
            method = METHOD_RECIPE
        elif self.rb_both.IsChecked:
            method = METHOD_BOTH
        else:
            method = METHOD_COUNTED
        return RunBoqOptions(
            scope, method, bool(self.cb_csv.IsChecked), bool(self.cb_html.IsChecked),
            self.folder_text.Text)

    def _run_click(self, sender, args):
        if self._running:
            return
        options = self._options()
        save_prefs({"method": options.method, "csv": options.csv,
                    "html": options.html, "folder": options.folder})
        self.result = options
        if self._on_run is None:
            self.window.Close()
            return

        self._running = True
        self.run_button.IsEnabled = False
        self.status_text.Text = "Reading the model..."
        self._hide_report()
        takeoff = self._on_run

        def work():
            return takeoff(options)

        def done(report, error):
            self._running = False
            self.run_button.IsEnabled = True
            if error is not None:
                self.status_text.Text = u"Takeoff failed: {}".format(error)
                return
            self._show_report(report)

        self._runner.submit(work, self._safely(done), urgent=True)

    def _show_report(self, report):
        if report is None:
            self.status_text.Text = "Takeoff finished."
            return
        self.report_title.Text = report.get("title") or "Takeoff finished"
        self.report_summary.Text = report.get("summary") or ""
        try:
            from System.Windows.Media import SolidColorBrush, ColorConverter
            colour = brand.ORANGE if not report.get("ok", True) else "#FF15803D"
            self.report_dot.Fill = SolidColorBrush(ColorConverter.ConvertFromString(colour))
        except Exception:
            pass
        self.report_overlay.Visibility = Visibility.Visible
        self.status_text.Text = (report.get("summary") or "").split("\n")[0]
        self._report = report

    def _hide_report(self):
        if self.report_overlay is not None:
            self.report_overlay.Visibility = Visibility.Collapsed

    def _open_folder(self, sender, args):
        folder = getattr(self, "_report", {}).get("folder") or self.folder_text.Text
        if not folder:
            return
        try:
            try:
                clr.AddReference("System.Diagnostics.Process")
            except Exception:
                pass
            from System.Diagnostics import Process as Proc
            Proc.Start("explorer.exe", u'"{}"'.format(folder))
        except Exception as ex:
            self.status_text.Text = u"Could not open the folder: {}".format(ex)

    def _on_closed_event(self, sender, args):
        self._closed = True
        if self._on_closed is not None:
            self._on_closed()


# Back-compat for anything that still expects a modal ask.
def ask_run_boq(model_title, view_name, selection_count, default_folder):
    dialog = RunBoqDialog(model_title, view_name, selection_count, default_folder,
                          stay_open=False)
    dialog.show_dialog()
    return dialog.result


class _NoProgress(object):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def busy(title):
    """Indeterminate pyRevit progress bar; silently skipped if unavailable."""
    try:
        from pyrevit import forms
        return forms.ProgressBar(title=title, indeterminate=True)
    except Exception:
        return _NoProgress()
