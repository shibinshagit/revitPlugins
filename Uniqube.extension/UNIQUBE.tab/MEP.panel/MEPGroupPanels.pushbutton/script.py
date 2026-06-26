# -*- coding: utf-8 -*-
"""MEP panel grouping with linked framing models (Task 5).

Reads panel zones from structural framing BIMSF_Container in the host
model and/or linked models, lists every panel with a host MEP count,
then groups host MEP (and any host framing) per panel. Sets
BIMSF_Container and Panel Name on assigned MEP elements for schedules
and shop drawings.

Linked framing cannot be grouped from the host — only host MEP is
grouped. Panel boundaries come from the linked framing bounding boxes.
"""
from pyrevit import revit, DB, forms, script
from System.Collections.Generic import List
from System.Windows.Controls import CheckBox
from System.Windows.Media import Brushes
from System.Windows import Thickness
import panel_utils as pu

doc = revit.doc
logger = script.get_logger()


def _row_label(row):
    parts = [
        row["display"],
        "[ MEP: {} ]".format(row["mep_count"]),
    ]
    if row["source"] == "link" and row["link_name"]:
        parts.append("(link: {})".format(row["link_name"]))
    elif row["source"] == "host + link":
        parts.append("(host + link)")
    return "  ".join(parts)


class MEPPanelSelector(forms.WPFWindow):
    """Checkbox list of panels with MEP counts and link source."""

    def __init__(self, rows, crossing_count):
        forms.WPFWindow.__init__(self, "SelectMEPPanels.xaml")
        self._checks = []
        self.selected = None

        link_panels = sum(1 for r in rows if "link" in r["source"])
        self.summary_text.Text = (
            "{0} panel(s) found — {1} from linked model(s). "
            "{2} host MEP element(s) cross panel boundaries "
            "(left ungrouped; check manually).".format(
                len(rows), link_panels, crossing_count
            )
        )

        for row in rows:
            cb = CheckBox()
            cb.Content = _row_label(row)
            cb.Margin = Thickness(2, 3, 2, 3)
            cb.IsChecked = row["mep_count"] > 0 or row["host_framing"] > 0
            if row["source"] == "link":
                cb.Foreground = Brushes.DarkBlue
            elif row["mep_count"] == 0:
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


def _group_selected(selected, panel_elements, link_zones):
    mep_assignments, link_assignments, link_stats = pu.assign_mep_to_panels(
        doc, panel_elements, link_zones
    )

    stats = {
        "groups": 0,
        "mep_tagged": 0,
        "host_framing": 0,
        "skipped_empty": 0,
        "link_matched": link_stats.get("link_matched", 0),
    }

    crossing_ids = {
        eid for eid, pids in mep_assignments.items() if len(pids) > 1
    }
    stats["skipped_crossing"] = len(crossing_ids)

    for pid in selected:
        group_ids = List[DB.ElementId]()

        for el in panel_elements.get(pid, []):
            group_ids.Add(el.Id)
            stats["host_framing"] += 1

        for eid, pids in mep_assignments.items():
            if len(pids) != 1:
                continue
            if list(pids)[0] != pid:
                continue
            el = doc.GetElement(eid)
            if el is None:
                continue
            group_ids.Add(eid)
            pu.set_panel_labels(el, pid)
            stats["mep_tagged"] += 1

        if group_ids.Count > 1:
            try:
                new_grp = doc.Create.NewGroup(group_ids)
                new_grp.GroupType.Name = pu.panel_group_name(pid)
                stats["groups"] += 1
            except Exception as ex:
                logger.debug("Group error for %s: %s", pid, ex)
        elif group_ids.Count == 0:
            stats["skipped_empty"] += 1

    return stats


def main():
    rows, panel_elements, link_zones = pu.build_panel_catalog(doc)
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
        stats = _group_selected(selected, panel_elements, link_zones)

    forms.alert(
        "Done.\n\n"
        "Panels selected: {}\n"
        "Revit groups created: {}\n"
        "Host MEP tagged (BIMSF_Container + Panel Name): {}\n"
        "Host framing in groups: {}\n"
        "Crossing MEP skipped: {}\n"
        "Panels with nothing to group: {}\n"
        "Linked framing matched (reference only): {}".format(
            len(selected),
            stats["groups"],
            stats["mep_tagged"],
            stats["host_framing"],
            stats["skipped_crossing"],
            stats["skipped_empty"],
            stats["link_matched"],
        ),
        title="UNIQUBE — MEP Group Panels",
    )


main()
