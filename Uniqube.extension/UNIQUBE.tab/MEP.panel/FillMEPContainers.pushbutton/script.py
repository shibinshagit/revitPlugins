# -*- coding: utf-8 -*-
"""Auto-fill BIMSF_Container on MEP elements inside panel zones."""
from pyrevit import revit, DB, forms, script
from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes
from System.Windows import Thickness
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


def _row_label(row):
    parts = [row["display"]]
    if row.get("mep_count"):
        parts.append("[ MEP in zone: {} ]".format(row["mep_count"]))
    if row.get("link_framing"):
        parts.append("[ link framing: {} ]".format(row["link_framing"]))
    return "  ".join(parts)


class TagPanelSelector(forms.WPFWindow):
    def __init__(self, rows, crossing_count):
        forms.WPFWindow.__init__(self, "SelectTagPanels.xaml")
        self._checks = []
        self.selected = None
        self.summary_text.Text = (
            "{0} panel(s). {1} crossing MEP (will be cleared). "
            "Fills BIMSF_Container on MEP in each panel zone; items "
            "outside zones inherit from connected pipes/conduits.".format(
                len(rows), crossing_count
            )
        )
        for row in rows:
            cb = CheckBox()
            cb.Content = _row_label(row)
            cb.Margin = Thickness(2, 3, 2, 3)
            cb.IsChecked = row.get("mep_count", 0) > 0 or row.get("link_framing", 0) > 0
            self.panel_stack.Children.Add(cb)
            self._checks.append((cb, row["pid"]))

    def select_all_click(self, sender, args):
        all_on = all(cb.IsChecked for cb, _ in self._checks)
        for cb, _ in self._checks:
            cb.IsChecked = not all_on

    def ok_click(self, sender, args):
        self.selected = [pid for cb, pid in self._checks if cb.IsChecked]
        self.Close()


def choose_panels(rows, crossing_count):
    if not rows:
        return None
    try:
        win = TagPanelSelector(rows, crossing_count)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed: %s", ex)
        return pu.choose_panels([r["pid"] for r in rows])


def main():
    if not hasattr(pu, "fill_mep_bimsf_containers"):
        forms.alert(
            "panel_utils.py is out of date.\n\n"
            "git pull the full revitPlugins folder, then pyRevit → Reload.",
            title="UNIQUBE",
        )
        return

    if doc.IsLinked:
        forms.alert("Open the MEP host model, not a linked file.", title="UNIQUBE")
        return

    rows, panel_elements, link_zones, _ = pu.build_panel_rows(doc)
    if not rows:
        forms.alert(
            "No panels with '{}' found.".format(pu.PARAM_NAME),
            title="UNIQUBE",
        )
        return

    crossing = pu.preview_crossing_mep(doc, panel_elements, link_zones)
    selected = choose_panels(rows, crossing)
    if not selected:
        return

    sync_was_on = False
    try:
        import panel_selection_sync as pss
        if pss.is_enabled():
            sync_was_on = True
            pss.disable(uidoc)
    except Exception:
        pass

    stats = {}
    with revit.Transaction("UNIQUBE: Fill BIMSF_Container"):
        stats = pu.fill_mep_bimsf_containers(
            doc, panel_elements, link_zones, selected=selected
        )

    forms.alert(
        "Done.\n\n"
        "BIMSF_Container filled: {tagged} new, {updated} updated\n"
        "Via connected runs: {prop}\n"
        "Resolved (endpoint/connect): {resolved}\n"
        "Crossing cleared: {cross}\n"
        "Conduit bends cleared: {bends}\n"
        "No writable param: {skip}\n"
        "Still unassigned: {unassigned}".format(
            tagged=stats.get("tagged", 0),
            updated=stats.get("updated", 0),
            prop=stats.get("propagated", 0),
            resolved=stats.get("resolved", 0),
            cross=stats.get("cleared_crossing", 0),
            bends=stats.get("cleared_bends", 0),
            skip=stats.get("skipped_no_param", 0),
            unassigned=stats.get("unassigned", 0),
        ),
        title="UNIQUBE — Fill BIMSF Container",
    )
    if sync_was_on:
        forms.alert(
            "Selection sync was turned OFF during fill to prevent "
            "Revit flickering.\n\n"
            "Use Sync Panel Selection when you want paired click-select again.",
            title="UNIQUBE — Sync Panel Selection",
        )


main()
