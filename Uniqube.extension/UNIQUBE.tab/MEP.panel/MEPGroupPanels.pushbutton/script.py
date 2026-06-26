# -*- coding: utf-8 -*-
"""MEP panel grouping with linked framing models (Task 5).

Lists panels from linked/host framing (BIMSF_Container), groups host
panel framing + MEP per panel, colors linked panel studs/tracks in the
active view (same method as Panel Combine Color), and tags MEP with
BIMSF_Container + Panel Name for shop drawings.

Linked framing cannot be placed in a host Revit group — it is colored
in the view by panel instead.
"""
from pyrevit import revit, DB, forms, script
from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes
from System.Windows import Thickness
import panel_utils as pu

doc = revit.doc
view = doc.ActiveView
logger = script.get_logger()


def _row_label(row):
    parts = [row["display"]]
    if row["mep_count"]:
        parts.append("[ MEP: {} ]".format(row["mep_count"]))
    if row["link_framing"]:
        parts.append("[ panel: {} ]".format(row["link_framing"]))
    elif row["host_framing"]:
        parts.append("[ panel: {} ]".format(row["host_framing"]))
    if row["source"] == "link" and row["link_name"]:
        parts.append("(link: {})".format(row["link_name"]))
    elif row["source"] == "host + link":
        parts.append("(host + link)")
    return "  ".join(parts)


class MEPPanelSelector(forms.WPFWindow):
    """Checkbox list of panels with MEP + framing counts."""

    def __init__(self, rows, crossing_count):
        forms.WPFWindow.__init__(self, "SelectMEPPanels.xaml")
        self._checks = []
        self.selected = None

        link_panels = sum(1 for r in rows if "link" in r["source"])
        self.summary_text.Text = (
            "{0} panel(s) found — {1} from linked model(s). "
            "{2} host MEP element(s) cross panel boundaries "
            "(shown in red; not grouped). Linked panel framing is "
            "colored in the view (cannot be grouped from host).".format(
                len(rows), link_panels, crossing_count
            )
        )

        for row in rows:
            cb = CheckBox()
            cb.Content = _row_label(row)
            cb.Margin = Thickness(2, 3, 2, 3)
            has_content = (
                row["mep_count"] > 0
                or row["host_framing"] > 0
                or row["link_framing"] > 0
            )
            cb.IsChecked = has_content
            if row["source"] == "link":
                cb.Foreground = Brushes.DarkBlue
            elif not has_content:
                cb.Foreground = Brushes.Gray
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
        win = MEPPanelSelector(rows, crossing_count)
        win.ShowDialog()
        return win.selected
    except Exception as ex:
        logger.debug("Custom selector failed: %s", ex)
        return pu.choose_panels([r["pid"] for r in rows])


def _delete_existing_groups(selected):
    all_groups = (
        DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
    )
    for g in all_groups:
        for pid in selected:
            if pu.group_matches_panel(g.Name, pid):
                try:
                    doc.Delete(g.Id)
                except Exception:
                    pass


def main():
    if isinstance(view, DB.ViewSheet):
        forms.alert("Open a model view, not a sheet.", title="UNIQUBE")
        return

    catalog = pu.build_panel_catalog(doc)
    rows, panel_elements, link_zones, link_framing = catalog
    if not rows:
        forms.alert(
            "No panels found.\n\n"
            "Structural framing with '{}' must exist in the host "
            "model or a linked model (e.g. Willow Street_Framing.rvt).".format(
                pu.PARAM_NAME
            ),
            title="UNIQUBE — MEP Group Panels",
        )
        return

    crossing = pu.preview_crossing_mep(doc, panel_elements, link_zones)
    selected = choose_panels(rows, crossing)
    if not selected:
        return

    with revit.Transaction("UNIQUBE: MEP Group Panels"):
        _delete_existing_groups(selected)
        stats = pu.combine_panels_group_color(
            doc,
            view,
            selected,
            panel_elements,
            link_zones,
            link_framing=link_framing,
            tag_mep=True,
        )

    forms.alert(
        "Done.\n\n"
        "Panels selected: {}\n"
        "Revit groups created (host panel + MEP): {}\n"
        "Host MEP tagged (BIMSF_Container + Panel Name): {}\n"
        "Host framing in groups: {}\n"
        "Linked panel framing colored: {}\n"
        "Crossing elements (red): {}\n"
        "Panels with nothing to group: {}".format(
            len(selected),
            stats["groups"],
            stats["mep_tagged"],
            stats["host_framing"],
            stats["link_framing_colored"],
            stats["crossing_count"],
            stats["skipped_empty"],
        ),
        title="UNIQUBE — MEP Group Panels",
    )


main()
