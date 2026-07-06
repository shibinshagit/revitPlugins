# -*- coding: utf-8 -*-
"""Export roll-forming CNC CSV files for truss assemblies and wall panels."""
from __future__ import print_function

import os

from pyrevit import revit, DB, forms, script

import cnc_export as cnc
import panel_utils as pu

from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes

doc = revit.doc
logger = script.get_logger()


def _is_truss_key(key):
    return "[ count " in key


def _alert_no_framing():
    forms.alert(
        "No structural framing with '{}' found in this model.\n\n"
        "Open the truss/panel model directly (not only the host link), "
        "or run Setup BIMSF / IFC Panel Mapper first.".format(pu.PARAM_NAME),
        title="UNIQUBE — CNC Extraction",
    )


class PanelSelector(forms.WPFWindow):
    """Checkbox list of panels/trusses. Truss rows are shown in red."""

    def __init__(self, keys):
        forms.WPFWindow.__init__(self, "SelectPanels.xaml")
        self._checks = []
        for key in keys:
            cb = CheckBox()
            cb.Content = key
            cb.Margin = self._row_margin()
            if _is_truss_key(key):
                cb.Foreground = Brushes.Red
            self.panel_stack.Children.Add(cb)
            self._checks.append((cb, key))
        self.selected = None

    @staticmethod
    def _row_margin():
        from System.Windows import Thickness
        return Thickness(2, 3, 2, 3)

    def select_all_click(self, sender, args):
        all_on = all(cb.IsChecked for cb, _ in self._checks)
        for cb, _ in self._checks:
            cb.IsChecked = not all_on

    def ok_click(self, sender, args):
        self.selected = [key for cb, key in self._checks if cb.IsChecked]
        self.Close()


def choose_panels(panel_ids):
    keys = sorted(panel_ids)
    if not keys:
        return None
    try:
        win = PanelSelector(keys)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed, using default: %s", ex)
        return pu.choose_panels(panel_ids)


def _pick_output_folder():
    try:
        from System.Windows.Forms import FolderBrowserDialog, DialogResult
        dialog = FolderBrowserDialog()
        dialog.Description = "Choose folder for CNC CSV export"
        if doc.PathName:
            dialog.SelectedPath = os.path.dirname(doc.PathName)
        if dialog.ShowDialog() == DialogResult.OK:
            return dialog.SelectedPath
    except Exception as ex:
        logger.debug("Folder picker failed: %s", ex)
    return None


def _clean_export_name(key):
    text = key
    if "[ count " in text:
        text = text.split("[ count ")[0].strip()
    return pu.panel_display_name(text) or text


def _job_name():
    try:
        info = doc.ProjectInformation
        if info:
            name = info.Name
            if name:
                return name
    except Exception:
        pass
    if doc.Title:
        return doc.Title
    return "UNIQUBE Export"


def main():
    groups = cnc.collect_export_groups(doc)
    if not groups:
        _alert_no_framing()
        return

    selected = choose_panels(groups.keys())
    if not selected:
        return

    output_folder = _pick_output_folder()
    if not output_folder:
        forms.alert("Export cancelled — no folder selected.", title="UNIQUBE")
        return

    job_name = _job_name()
    exported = []
    skipped = []

    for key in selected:
        members = groups.get(key, [])
        lines, rows = cnc.build_csv_lines(doc, members, job_name)
        if not lines:
            skipped.append(key)
            continue

        export_name = _clean_export_name(key)
        profile = rows[0]["profile"]
        file_name = cnc.default_csv_name(export_name, profile)
        path = os.path.join(output_folder, file_name)
        if os.path.exists(path):
            base, ext = os.path.splitext(file_name)
            index = 2
            while os.path.exists(path):
                file_name = "{}_{}{}".format(base, index, ext)
                path = os.path.join(output_folder, file_name)
                index += 1

        cnc.write_csv(path, lines)
        exported.append((key, file_name, len(rows)))

    if not exported:
        forms.alert(
            "No CNC CSV files were created.\n\n"
            "Selected groups had no exportable structural framing members.",
            title="UNIQUBE — CNC Extraction",
        )
        return

    msg = "CNC extraction complete.\n\nExported {} file(s) to:\n{}\n\n".format(
        len(exported), output_folder
    )
    for key, file_name, count in exported:
        msg += "- {} ({} members)\n".format(file_name, count)
    if skipped:
        msg += "\nSkipped (no members): {}".format(", ".join(skipped))

    forms.alert(msg, title="UNIQUBE — CNC Extraction")


main()
