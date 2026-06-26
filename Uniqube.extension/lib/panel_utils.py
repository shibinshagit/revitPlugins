# -*- coding: utf-8 -*-
"""Shared helpers for BIMSF panel scripts — framing map, MEP zone, link support."""
import random

from pyrevit import revit, DB
from System.Collections.Generic import List


PARAM_NAME = "BIMSF_Container"
PANEL_NAME_PARAM = "Panel Name"

# Legacy group type prefixes (ours and MWF) — panel name only in UI.
_GROUP_PREFIXES = (
    "BIMSF Panel ",
    "BIMSF_Panel_",
    "BIMSF Panel_",
    "BIMSF_Panel ",
)


def strip_group_prefix(name):
    """Remove 'BIMSF Panel …' / 'BIMSF_Panel_…' from a group type name."""
    if not name:
        return ""
    text = name.strip()
    for prefix in _GROUP_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def panel_display_name(raw):
    """Clean panel name for lists/schedules — no group prefix, no leading '*'."""
    name = strip_group_prefix(raw)
    if name.startswith("*"):
        name = name[1:]
    return name.strip()


def panel_group_name(container):
    """Group type name — clean panel id (e.g. ELB-1001)."""
    name = panel_display_name(container)
    return name or (container or "").strip()


def panel_ids_match(a, b):
    """True if two BIMSF_Container values are the same panel."""
    if not a or not b:
        return False
    return panel_display_name(a).lower() == panel_display_name(b).lower()


def merge_framing_for_panel(framing_map, pid):
    """Collect host framing for a panel (handles * prefix variants)."""
    elements = []
    seen = set()
    for key, items in framing_map.items():
        if not panel_ids_match(key, pid):
            continue
        for el in items:
            eid = el.Id.IntegerValue
            if eid not in seen:
                seen.add(eid)
                elements.append(el)
    return elements


def merge_link_framing_for_panel(link_framing, pid):
    """Collect linked framing for a panel (handles * prefix variants)."""
    pairs = []
    seen = set()
    for key, items in link_framing.items():
        if not panel_ids_match(key, pid):
            continue
        for link_inst, elem in items:
            uid = elem.UniqueId
            if uid not in seen:
                seen.add(uid)
                pairs.append((link_inst, elem))
    return pairs


def _assignment_matches_panel(assigned_pids, pid):
    if len(assigned_pids) != 1:
        return False
    return panel_ids_match(list(assigned_pids)[0], pid)


def _assignment_crosses_panel(assigned_pids, pid):
    if len(assigned_pids) <= 1:
        return False
    return any(panel_ids_match(p, pid) for p in assigned_pids)


def group_matches_panel(group_name, panel_id):
    """True if a group type name belongs to the given panel id."""
    if not group_name or not panel_id:
        return False
    return panel_ids_match(strip_group_prefix(group_name), panel_id)


MEP_CATS = [
    DB.BuiltInCategory.OST_Conduit,
    DB.BuiltInCategory.OST_ConduitFitting,
    DB.BuiltInCategory.OST_PipeCurves,
    DB.BuiltInCategory.OST_PipeFitting,
    DB.BuiltInCategory.OST_PipeInsulations,
    DB.BuiltInCategory.OST_ElectricalFixtures,
    DB.BuiltInCategory.OST_CableTray,
    DB.BuiltInCategory.OST_CableTrayFitting,
    DB.BuiltInCategory.OST_DuctCurves,
    DB.BuiltInCategory.OST_DuctFitting,
    DB.BuiltInCategory.OST_DuctAccessory,
    DB.BuiltInCategory.OST_FlexDuctCurves,
    DB.BuiltInCategory.OST_FlexPipeCurves,
    DB.BuiltInCategory.OST_LightingFixtures,
    DB.BuiltInCategory.OST_LightingDevices,
    DB.BuiltInCategory.OST_ElectricalEquipment,
    DB.BuiltInCategory.OST_MechanicalEquipment,
    DB.BuiltInCategory.OST_Sprinklers,
]

# Host + link spatial assignment for panel grouping workflows.
LINK_ASSIGN_CATS = MEP_CATS + [DB.BuiltInCategory.OST_StructuralFraming]

ZONE_PAD_FT = 0.2


def get_mep_filter():
    return DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](MEP_CATS))


def get_link_assign_filter():
    return DB.ElementMulticategoryFilter(List[DB.BuiltInCategory](LINK_ASSIGN_CATS))


def map_framing(doc):
    """Return {panel_id: [element, ...]} from host structural framing."""
    all_framing = (
        DB.FilteredElementCollector(doc)
        .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    panel_elements = {}
    for beam in all_framing:
        p_param = beam.LookupParameter(PARAM_NAME)
        if p_param and p_param.HasValue:
            pid = p_param.AsString()
            if not pid:
                continue
            if pid not in panel_elements:
                panel_elements[pid] = []
            panel_elements[pid].append(beam)
    return panel_elements


def map_framing_from_links(doc):
    """Return {panel_id: [(host_min, host_max), ...]} from linked framing.

    Linked framing cannot be grouped in the host model, but its bounding
    boxes (transformed to host coordinates) define panel zones so host and
    linked MEP/electrical can be assigned to the same panel number.
    """
    link_zones = {}
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        transform = link_inst.GetTotalTransform()
        framing = (
            DB.FilteredElementCollector(link_doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for beam in framing:
            p_param = beam.LookupParameter(PARAM_NAME)
            if p_param and p_param.HasValue:
                pid = p_param.AsString()
                if not pid:
                    continue
                bbox = beam.get_BoundingBox(None)
                if bbox is None:
                    continue
                t_min = transform.OfPoint(bbox.Min)
                t_max = transform.OfPoint(bbox.Max)
                if pid not in link_zones:
                    link_zones[pid] = []
                link_zones[pid].append((t_min, t_max))
    return link_zones


def map_link_framing_by_container(doc):
    """Return {panel_id: [(link_inst, elem), ...]} for linked framing only.

    Each member is matched by its own BIMSF_Container — not the whole link
    model — so only that panel's studs/tracks are highlighted.
    """
    result = {}
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        framing = (
            DB.FilteredElementCollector(link_doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for beam in framing:
            p_param = beam.LookupParameter(PARAM_NAME)
            if p_param and p_param.HasValue:
                pid = p_param.AsString()
                if pid:
                    result.setdefault(pid, []).append((link_inst, beam))
    return result


def count_link_framing(link_framing_map):
    """Return {panel_id: member_count} from map_link_framing_by_container."""
    return {pid: len(items) for pid, items in link_framing_map.items()}


def get_all_panel_ids(panel_elements, link_zones=None):
    ids = set(panel_elements.keys())
    if link_zones:
        ids.update(link_zones.keys())
    return ids


def compute_panel_bbox(elements, link_bboxes=None):
    """Compute combined bounding box for a panel's framing + link bboxes."""
    min_pt = DB.XYZ(10000, 10000, 10000)
    max_pt = DB.XYZ(-10000, -10000, -10000)

    for el in elements:
        bbox = el.get_BoundingBox(None)
        if bbox:
            min_pt = DB.XYZ(
                min(min_pt.X, bbox.Min.X),
                min(min_pt.Y, bbox.Min.Y),
                min(min_pt.Z, bbox.Min.Z),
            )
            max_pt = DB.XYZ(
                max(max_pt.X, bbox.Max.X),
                max(max_pt.Y, bbox.Max.Y),
                max(max_pt.Z, bbox.Max.Z),
            )

    if link_bboxes:
        for bb_min, bb_max in link_bboxes:
            min_pt = DB.XYZ(
                min(min_pt.X, bb_min.X),
                min(min_pt.Y, bb_min.Y),
                min(min_pt.Z, bb_min.Z),
            )
            max_pt = DB.XYZ(
                max(max_pt.X, bb_max.X),
                max(max_pt.Y, bb_max.Y),
                max(max_pt.Z, bb_max.Z),
            )

    return min_pt, max_pt


def _panel_outline(min_pt, max_pt):
    pad = DB.XYZ(ZONE_PAD_FT, ZONE_PAD_FT, ZONE_PAD_FT)
    return DB.Outline(min_pt.Subtract(pad), max_pt.Add(pad))


def _bbox_intersects_outline(bbox, outline):
    if bbox is None:
        return False
    bb_outline = DB.Outline(bbox.Min, bbox.Max)
    return outline.Intersects(bb_outline, 0.001)


def _set_container(elem, pid):
    p = elem.LookupParameter(PARAM_NAME)
    if p and not p.IsReadOnly:
        try:
            p.Set(pid)
            return True
        except Exception:
            return False
    return False


def set_panel_labels(elem, panel_id):
    """Write BIMSF_Container and Panel Name on a host element."""
    display = panel_display_name(panel_id)
    _set_container(elem, panel_id)
    p = elem.LookupParameter(PANEL_NAME_PARAM)
    if p and not p.IsReadOnly:
        try:
            p.Set(display)
        except Exception:
            pass


def map_framing_link_sources(doc):
    """Return {panel_id: link_doc_title} from linked structural framing."""
    sources = {}
    links = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        link_title = link_doc.Title or "linked model"
        framing = (
            DB.FilteredElementCollector(link_doc)
            .OfCategory(DB.BuiltInCategory.OST_StructuralFraming)
            .WhereElementIsNotElementType()
            .ToElements()
        )
        for beam in framing:
            p_param = beam.LookupParameter(PARAM_NAME)
            if p_param and p_param.HasValue:
                pid = p_param.AsString()
                if pid:
                    sources.setdefault(pid, link_title)
    return sources


def preview_mep_counts(doc, panel_elements, link_zones):
    """Return {panel_id: host_mep_count} for elements in exactly one panel."""
    mep_assignments, _, _ = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    counts = {pid: 0 for pid in get_all_panel_ids(panel_elements, link_zones)}
    for _eid, pids in mep_assignments.items():
        if len(pids) == 1:
            pid = list(pids)[0]
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def preview_crossing_mep(doc, panel_elements, link_zones):
    """Return count of host MEP elements assigned to more than one panel."""
    mep_assignments, _, _ = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    return sum(1 for pids in mep_assignments.values() if len(pids) > 1)


def build_panel_catalog(doc):
    """Build panel rows for the MEP grouping UI.

    Each row: pid, display, source, mep_count, link_name, host_framing,
    link_framing.
    """
    panel_elements = map_framing(doc)
    link_zones = map_framing_from_links(doc)
    link_sources = map_framing_link_sources(doc)
    link_framing = map_link_framing_by_container(doc)
    link_framing_counts = count_link_framing(link_framing)
    mep_counts = preview_mep_counts(doc, panel_elements, link_zones)
    all_pids = get_all_panel_ids(panel_elements, link_zones)
    all_pids.update(link_framing.keys())

    rows = []
    for pid in sorted(all_pids, key=lambda x: panel_display_name(x).lower()):
        host_count = len(panel_elements.get(pid, []))
        link_count = link_framing_counts.get(pid, 0)
        in_link = pid in link_zones or link_count > 0
        if host_count and in_link:
            source = "host + link"
        elif in_link:
            source = "link"
        else:
            source = "host"
        rows.append({
            "pid": pid,
            "display": panel_display_name(pid),
            "source": source,
            "mep_count": mep_counts.get(pid, 0),
            "link_name": link_sources.get(pid, ""),
            "host_framing": host_count,
            "link_framing": link_count,
        })
    return rows, panel_elements, link_zones, link_framing


def _host_bbox_in_outline(elem, outline):
    bbox = elem.get_BoundingBox(None)
    return _bbox_intersects_outline(bbox, outline)


def _link_bbox_in_outline(elem, transform, outline):
    bbox = elem.get_BoundingBox(None)
    if bbox is None:
        return False
    t_min = transform.OfPoint(bbox.Min)
    t_max = transform.OfPoint(bbox.Max)
    host_bb = DB.BoundingBoxXYZ()
    host_bb.Min = DB.XYZ(
        min(t_min.X, t_max.X),
        min(t_min.Y, t_max.Y),
        min(t_min.Z, t_max.Z),
    )
    host_bb.Max = DB.XYZ(
        max(t_min.X, t_max.X),
        max(t_min.Y, t_max.Y),
        max(t_min.Z, t_max.Z),
    )
    return _bbox_intersects_outline(host_bb, outline)


def assign_mep_to_panels(doc, panel_elements, link_zones=None, assign_links=True):
    """Assign host + linked disciplines to panels by spatial zone.

    Host elements can receive BIMSF_Container writes. Linked-model elements
    are read-only from the host — they are returned for view coloring only.

    Returns:
      host_assignments: {ElementId: set(panel_ids)} for host elements
      link_assignments: list of (link_inst, elem, set(panel_ids))
      stats: dict with link_matched count (for coloring)
    """
    mep_filter = get_mep_filter()
    assign_filter = get_link_assign_filter()

    all_mep = (
        DB.FilteredElementCollector(doc)
        .WherePasses(mep_filter)
        .WhereElementIsNotElementType()
        .ToElements()
    )
    host_assignments = {}
    for item in all_mep:
        host_assignments[item.Id] = set()

    all_pids = get_all_panel_ids(panel_elements, link_zones)
    panel_outlines = {}
    for pid in all_pids:
        host_elements = panel_elements.get(pid, [])
        lz = link_zones.get(pid, []) if link_zones else []
        min_pt, max_pt = compute_panel_bbox(host_elements, lz)
        panel_outlines[pid] = _panel_outline(min_pt, max_pt)

    for pid, outline in panel_outlines.items():
        nearby = (
            DB.FilteredElementCollector(doc)
            .WherePasses(mep_filter)
            .WherePasses(DB.BoundingBoxIntersectsFilter(outline))
            .ToElements()
        )
        for item in nearby:
            if item.Id in host_assignments:
                host_assignments[item.Id].add(pid)

    link_assignments = []
    stats = {"link_matched": 0}

    if assign_links and link_zones:
        links = (
            DB.FilteredElementCollector(doc)
            .OfClass(DB.RevitLinkInstance)
            .ToElements()
        )
        for link_inst in links:
            link_doc = link_inst.GetLinkDocument()
            if link_doc is None:
                continue
            transform = link_inst.GetTotalTransform()
            candidates = (
                DB.FilteredElementCollector(link_doc)
                .WherePasses(assign_filter)
                .WhereElementIsNotElementType()
                .ToElements()
            )
            for elem in candidates:
                matched = set()
                cat = elem.Category
                is_framing = (
                    cat is not None
                    and cat.BuiltInCategory
                    == DB.BuiltInCategory.OST_StructuralFraming
                )
                if is_framing:
                    # Panel studs/tracks: match BIMSF_Container only (not
                    # spatial zone — avoids coloring the whole link).
                    p_param = elem.LookupParameter(PARAM_NAME)
                    if p_param and p_param.HasValue:
                        pid = p_param.AsString()
                        if pid and pid in all_pids:
                            matched.add(pid)
                else:
                    for pid, outline in panel_outlines.items():
                        if _link_bbox_in_outline(elem, transform, outline):
                            matched.add(pid)
                if not matched:
                    continue
                link_assignments.append((link_inst, elem, matched))
                stats["link_matched"] += 1

    return host_assignments, link_assignments, stats


def _view_color_kit(doc):
    """Fill pattern + red override + factory for random panel colors."""
    fill_pattern = (
        DB.FilteredElementCollector(doc)
        .OfClass(DB.FillPatternElement)
        .FirstElement()
    )
    red_settings = DB.OverrideGraphicSettings()
    if fill_pattern:
        red_settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
        red_settings.SetSurfaceForegroundPatternColor(DB.Color(255, 0, 0))

    def panel_settings():
        r = random.randint(0, 180)
        g = random.randint(50, 255)
        b = random.randint(50, 255)
        settings = DB.OverrideGraphicSettings()
        if fill_pattern:
            settings.SetSurfaceForegroundPatternId(fill_pattern.Id)
            settings.SetSurfaceForegroundPatternColor(DB.Color(r, g, b))
        return settings

    return red_settings, panel_settings


def _delete_groups_in_doc(doc, selected):
    """Dissolve existing panel groups without deleting member elements."""
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements():
        for pid in selected:
            if group_matches_panel(g.Name, pid):
                try:
                    g.UngroupMembers()
                except Exception:
                    try:
                        doc.Delete(g.Id)
                    except Exception:
                        pass


def discover_host_panel_ids(doc):
    """Collect panel ids from host framing, groups, and link zones."""
    panel_elements = map_framing(doc)
    link_zones = map_framing_from_links(doc)
    ids = get_all_panel_ids(panel_elements, link_zones)
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements():
        name = strip_group_prefix(g.Name)
        if name:
            ids.add(name)
    return sorted(ids, key=lambda x: panel_display_name(x).lower())


def count_panel_groups(doc, panel_ids=None):
    """Count Revit groups that match panel naming."""
    if panel_ids is None:
        panel_ids = discover_host_panel_ids(doc)
    count = 0
    for g in DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements():
        for pid in panel_ids:
            if group_matches_panel(g.Name, pid):
                count += 1
                break
    return count


def ungroup_panels_in_host(doc, panel_ids=None):
    """Ungroup host panel groups so individual elements can be selected."""
    if panel_ids is None:
        panel_ids = discover_host_panel_ids(doc)
    ungrouped = 0
    seen = set()
    for g in list(
        DB.FilteredElementCollector(doc).OfClass(DB.Group).ToElements()
    ):
        for pid in panel_ids:
            if not group_matches_panel(g.Name, pid):
                continue
            gid = g.Id.IntegerValue
            if gid in seen:
                break
            try:
                g.UngroupMembers()
                ungrouped += 1
                seen.add(gid)
            except Exception:
                pass
            break
    return {"ungrouped": ungrouped, "panel_ids": list(panel_ids)}


class _CopyUseDestinationTypes(DB.IDuplicateTypeNamesHandler):
    """Auto-resolve duplicate type names during copy (no modal dialog)."""

    def OnDuplicateTypeNamesFound(self, args):
        return DB.DuplicateTypeAction.UseDestinationTypes


def get_link_document_path(host_doc, link_inst):
    """Return the on-disk path for a Revit link instance."""
    link_doc = link_inst.GetLinkDocument()
    if link_doc is not None:
        try:
            path = link_doc.PathName
            if path:
                return path
        except Exception:
            pass
    try:
        ref = DB.ExternalFileUtils.GetExternalFileReference(
            host_doc, link_inst.GetTypeId()
        )
        mp = ref.GetPath()
        return DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(mp)
    except Exception:
        return None


def _document_path(doc):
    try:
        if doc.IsWorkshared:
            mp = doc.GetWorksharingCentralModelPath()
            return DB.ModelPathUtils.ConvertModelPathToUserVisiblePath(mp)
    except Exception:
        pass
    try:
        return doc.PathName
    except Exception:
        return None


def _link_instances_for_path(host_doc, path):
    norm = path.lower()
    result = []
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        lp = get_link_document_path(host_doc, link_inst)
        if lp and lp.lower() == norm:
            result.append(link_inst)
    return result


def _unload_link_instances(host_doc, link_insts):
    """Unload link instances — must run inside a host-document transaction."""
    unloaded = []
    if not link_insts:
        return unloaded
    t = DB.Transaction(host_doc, "UNIQUBE: Unload Link")
    try:
        t.Start()
        for link_inst in link_insts:
            if link_inst.IsLoaded():
                link_inst.Unload()
                unloaded.append(link_inst)
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
    return unloaded


def _load_link_instances(host_doc, link_insts):
    """Reload previously unloaded link instances."""
    count = 0
    if not link_insts:
        return count
    t = DB.Transaction(host_doc, "UNIQUBE: Reload Link")
    try:
        t.Start()
        for link_inst in link_insts:
            if not link_inst.IsLoaded():
                link_inst.Load()
                count += 1
        t.Commit()
    except Exception:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
    return count


def _is_primary_document(doc):
    try:
        return doc is not None and not doc.IsLinked
    except Exception:
        return False


def _open_document_for_edit(app, path):
    """Open a link .rvt as a primary document for editing."""
    if not path:
        return None, False, False
    norm = path.lower()
    for d in app.Documents:
        try:
            if not _is_primary_document(d):
                continue
            dp = _document_path(d)
            if dp and dp.lower() == norm:
                return d, False, False
        except Exception:
            pass
    try:
        mp = DB.ModelPathUtils.ConvertUserVisiblePathToModelPath(path)
        opts = DB.OpenOptions()
        opts.Audit = False
        opened = app.OpenDocumentFile(mp, opts)
        if not _is_primary_document(opened):
            return None, False, False
        return opened, True, True
    except Exception:
        return None, False, False


def group_framing_in_active_doc(doc, selected):
    """Group panel framing in the active (primary) document."""
    framing = map_framing(doc)
    stats = {"link_groups": 0, "errors": []}
    t = DB.Transaction(doc, "UNIQUBE: Group Panel Framing")
    try:
        t.Start()
        _delete_groups_in_doc(doc, selected)
        for pid in selected:
            group_ids = List[DB.ElementId]()
            for el in merge_framing_for_panel(framing, pid):
                group_ids.Add(el.Id)
            if group_ids.Count > 1:
                grp = doc.Create.NewGroup(group_ids)
                grp.GroupType.Name = panel_group_name(pid)
                stats["link_groups"] += 1
        t.Commit()
    except Exception as ex:
        stats["errors"].append(str(ex))
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
    return stats


def group_link_panel_framing(app, host_doc, selected, link_framing):
    """Create Revit groups for panel framing inside each link .rvt file.

    Revit cannot put linked elements in a host group, so each panel's
    studs/tracks are grouped in the link model with the same name as the
    host MEP group (e.g. ELB-1001).
    """
    stats = {
        "link_groups": 0,
        "link_files": 0,
        "reloaded": 0,
        "errors": [],
    }
    if not link_framing or not selected:
        return stats

    by_path = {}
    link_inst_map = {}
    for pid in selected:
        for link_inst, elem in merge_link_framing_for_panel(link_framing, pid):
            path = get_link_document_path(host_doc, link_inst)
            if not path:
                msg = "No file path for link — save the link model locally."
                if msg not in stats["errors"]:
                    stats["errors"].append(msg)
                continue
            if path not in by_path:
                by_path[path] = {}
                link_inst_map[path] = _link_instances_for_path(host_doc, path)
            by_path[path].setdefault(pid, []).append(elem.UniqueId)

    for path, panels in by_path.items():
        link_insts = link_inst_map.get(path, [])
        unloaded = _unload_link_instances(host_doc, link_insts)

        edit_doc, opened_here, close_after = _open_document_for_edit(app, path)
        if edit_doc is None or not _is_primary_document(edit_doc):
            stats["errors"].append(
                "Could not open link file for editing: {}".format(path)
            )
            _load_link_instances(host_doc, unloaded)
            continue

        stats["link_files"] += 1
        t = DB.Transaction(edit_doc, "UNIQUBE: Group Panel Framing")
        try:
            t.Start()
            _delete_groups_in_doc(edit_doc, selected)
            for pid, unique_ids in panels.items():
                group_ids = List[DB.ElementId]()
                seen_uids = set()
                for uid in unique_ids:
                    if uid in seen_uids:
                        continue
                    seen_uids.add(uid)
                    el = edit_doc.GetElement(uid)
                    if el is not None:
                        group_ids.Add(el.Id)
                if group_ids.Count > 1:
                    grp = edit_doc.Create.NewGroup(group_ids)
                    grp.GroupType.Name = panel_group_name(pid)
                    stats["link_groups"] += 1
            t.Commit()
            if opened_here:
                edit_doc.Save()
        except Exception as ex:
            stats["errors"].append("{}: {}".format(path, ex))
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        finally:
            if close_after and edit_doc is not None:
                try:
                    edit_doc.Close(False)
                except Exception:
                    pass
            stats["reloaded"] += _load_link_instances(host_doc, unloaded)

    return stats


def reload_links_for_paths(host_doc, paths):
    """Reload link instances whose source files were edited."""
    if not paths:
        return 0
    norm_paths = set(p.lower() for p in paths)
    count = 0
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )
    for link_inst in links:
        path = get_link_document_path(host_doc, link_inst)
        if path and path.lower() in norm_paths:
            try:
                link_inst.Reload()
                count += 1
            except Exception:
                pass
    return count


def select_panel_pair(uidoc, host_doc, pid, link_framing):
    """Select ONE panel's host group; include link only if framing is still linked."""
    refs = List[DB.Reference]()
    host_framing = map_framing(host_doc)
    host_only = bool(merge_framing_for_panel(host_framing, pid))
    links = (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.RevitLinkInstance)
        .ToElements()
    )

    for g in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.Group)
        .ToElements()
    ):
        if not group_matches_panel(g.Name, pid):
            continue
        try:
            refs.Add(DB.Reference(g))
        except Exception:
            pass
        break

    if host_only:
        if refs.Count > 0:
            uidoc.Selection.SetReferences(refs)
            return refs.Count
        return 0

    link_group_found = False
    for link_inst in links:
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        for g in (
            DB.FilteredElementCollector(link_doc)
            .OfClass(DB.Group)
            .ToElements()
        ):
            if not group_matches_panel(g.Name, pid):
                continue
            try:
                refs.Add(DB.Reference(g).CreateLinkReference(link_inst))
                link_group_found = True
            except Exception:
                pass
            break

    if not link_group_found:
        if link_framing is None:
            link_framing = map_link_framing_by_container(host_doc)
        for link_inst, elem in merge_link_framing_for_panel(link_framing, pid):
            try:
                refs.Add(DB.Reference(elem).CreateLinkReference(link_inst))
            except Exception:
                pass

    if refs.Count > 0:
        uidoc.Selection.SetReferences(refs)
        return refs.Count
    return 0


def select_panels_in_view(uidoc, host_doc, selected, link_framing):
    """Select each panel pair one at a time — use select_panel_pair instead."""
    if not selected:
        return 0
    return select_panel_pair(uidoc, host_doc, selected[0], link_framing)


def combine_panels_group_color(
    doc,
    view,
    selected,
    panel_elements,
    link_zones,
    link_framing=None,
    tag_mep=True,
):
    """Group host panel + MEP and color like Panel Combine (Color).

    Host framing and MEP go into Revit groups. Linked panel framing is
    colored in the active view and should be grouped in the link file via
    group_link_panel_framing(). Crossing MEP is marked red.
    """
    if link_framing is None:
        link_framing = map_link_framing_by_container(doc)

    mep_assignments, link_assignments, link_stats = assign_mep_to_panels(
        doc, panel_elements, link_zones
    )
    red_settings, panel_settings = _view_color_kit(doc)

    stats = {
        "groups": 0,
        "mep_tagged": 0,
        "host_framing": 0,
        "link_framing_colored": 0,
        "crossing_count": 0,
        "skipped_empty": 0,
        "group_errors": [],
        "link_matched": link_stats.get("link_matched", 0),
    }

    processed_crossings = set()
    added_to_group = set()

    for pid in selected:
        settings = panel_settings()
        group_ids = List[DB.ElementId]()

        for el in merge_framing_for_panel(panel_elements, pid):
            view.SetElementOverrides(el.Id, settings)
            eid = el.Id.IntegerValue
            if eid not in added_to_group:
                added_to_group.add(eid)
                group_ids.Add(el.Id)
                stats["host_framing"] += 1

        for link_inst, elem in merge_link_framing_for_panel(link_framing, pid):
            if set_link_element_override(view, link_inst, elem, settings):
                stats["link_framing_colored"] += 1

        for eid, pids in mep_assignments.items():
            el = doc.GetElement(eid)
            if el is None:
                continue
            eid_int = eid.IntegerValue
            if _assignment_matches_panel(pids, pid):
                if eid_int not in added_to_group:
                    added_to_group.add(eid_int)
                    group_ids.Add(eid)
                view.SetElementOverrides(eid, settings)
                if tag_mep:
                    set_panel_labels(el, pid)
                    stats["mep_tagged"] += 1
            elif _assignment_crosses_panel(pids, pid):
                view.SetElementOverrides(eid, red_settings)
                if eid not in processed_crossings:
                    processed_crossings.add(eid)
                    stats["crossing_count"] += 1
                if tag_mep:
                    p = el.LookupParameter(PARAM_NAME)
                    if p and not p.IsReadOnly:
                        try:
                            p.Set("")
                        except Exception:
                            pass

        for link_inst, elem, pids in link_assignments:
            is_framing = (
                elem.Category is not None
                and elem.Category.BuiltInCategory
                == DB.BuiltInCategory.OST_StructuralFraming
            )
            if is_framing:
                continue
            if _assignment_matches_panel(pids, pid):
                set_link_element_override(view, link_inst, elem, settings)
            elif _assignment_crosses_panel(pids, pid):
                set_link_element_override(view, link_inst, elem, red_settings)
                stats["crossing_count"] += 1

        has_link = bool(merge_link_framing_for_panel(link_framing, pid))
        if group_ids.Count > 1:
            try:
                new_grp = doc.Create.NewGroup(group_ids)
                new_grp.GroupType.Name = panel_group_name(pid)
                stats["groups"] += 1
            except Exception as ex:
                stats["group_errors"].append(
                    "{}: {}".format(panel_group_name(pid), ex)
                )
        elif group_ids.Count == 1 and not has_link:
            stats["skipped_empty"] += 1
        elif group_ids.Count == 0 and not has_link:
            stats["skipped_empty"] += 1

    return stats


def set_link_element_override(view, link_inst, elem, override_settings):
    """Apply a graphic override to one element inside a linked model."""
    try:
        link_id = DB.LinkElementId(link_inst.Id, elem.Id)
        view.SetElementOverrides(link_id, override_settings)
        return True
    except Exception:
        return False


def build_panel_rows(doc):
    """Build panel list rows for MEP grouping / copy workflows."""
    panel_elements = map_framing(doc)
    link_zones = map_framing_from_links(doc)
    link_framing = map_link_framing_by_container(doc)
    link_sources = map_framing_link_sources(doc)
    mep_counts = preview_mep_counts(doc, panel_elements, link_zones)
    link_counts = count_link_framing(link_framing)

    all_pids = get_all_panel_ids(panel_elements, link_zones)
    all_pids.update(link_framing.keys())
    all_pids.update(panel_elements.keys())

    rows = []
    for pid in sorted(all_pids, key=lambda x: panel_display_name(x).lower()):
        host_count = len(merge_framing_for_panel(panel_elements, pid))
        link_count = sum(
            link_counts.get(k, 0)
            for k in link_counts
            if panel_ids_match(k, pid)
        )
        in_link = pid in link_zones or link_count > 0
        if host_count and in_link:
            source = "host + link"
        elif in_link:
            source = "link"
        else:
            source = "host"
        rows.append({
            "pid": pid,
            "display": panel_display_name(pid),
            "source": source,
            "mep_count": sum(
                mep_counts.get(k, 0)
                for k in mep_counts
                if panel_ids_match(k, pid)
            ),
            "link_name": link_sources.get(pid, ""),
            "host_framing": host_count,
            "link_framing": link_count,
        })
    return rows, panel_elements, link_zones, link_framing


def _flatten_copied_elements(host_doc, element_ids):
    """Explode copied groups and return all leaf elements."""
    result = []
    pending = list(element_ids)
    exploded = 0
    while pending:
        eid = pending.pop()
        el = host_doc.GetElement(eid)
        if el is None:
            continue
        if isinstance(el, DB.Group):
            try:
                for mid in el.UngroupMembers():
                    pending.append(mid)
                exploded += 1
            except Exception:
                pass
        else:
            result.append(el)
    return result, exploded


def copy_panel_framing_to_host(host_doc, view, selected, link_framing=None, regroup=True):
    """Copy linked panel framing into the host, explode groups, regroup with MEP.

    After this, panel studs/tracks live in the host model and can be grouped
    with MEP in a single Revit group so the structural link can be removed.
    """
    stats = {
        "panels": 0,
        "members_copied": 0,
        "groups_exploded": 0,
        "host_groups": 0,
        "skipped": [],
        "errors": [],
        "verify": [],
        "copied_pids": [],
    }
    if not selected:
        return stats

    if link_framing is None:
        link_framing = map_link_framing_by_container(host_doc)
    if not link_framing:
        return stats

    copy_opts = DB.CopyPasteOptions()
    copy_opts.SetDuplicateTypeNamesHandler(_CopyUseDestinationTypes())
    host_framing = map_framing(host_doc)
    copied_pids = []

    for pid in selected:
        pairs = merge_link_framing_for_panel(link_framing, pid)
        label = panel_display_name(pid)
        if not pairs:
            stats["skipped"].append("{} (no link framing)".format(label))
            continue

        if merge_framing_for_panel(host_framing, pid):
            stats["skipped"].append(
                "{} (host framing already exists)".format(label)
            )
            continue

        by_link = {}
        for link_inst, elem in pairs:
            key = link_inst.Id.IntegerValue
            if key not in by_link:
                by_link[key] = (link_inst, [])
            by_link[key][1].append(elem.Id)

        panel_copied = False
        for link_inst, elem_ids in by_link.values():
            link_doc = link_inst.GetLinkDocument()
            if link_doc is None:
                continue
            transform = link_inst.GetTotalTransform()
            src_ids = List[DB.ElementId]()
            for eid in elem_ids:
                src_ids.Add(eid)
            try:
                new_ids = DB.ElementTransformUtils.CopyElements(
                    link_doc,
                    src_ids,
                    host_doc,
                    transform,
                    copy_opts,
                )
                flat, exploded = _flatten_copied_elements(
                    host_doc, list(new_ids)
                )
                stats["groups_exploded"] += exploded
                for el in flat:
                    set_panel_labels(el, pid)
                    stats["members_copied"] += 1
                    panel_copied = True
            except Exception as ex:
                stats["errors"].append("{}: {}".format(label, ex))

        if panel_copied:
            stats["panels"] += 1
            copied_pids.append(pid)
            stats["copied_pids"] = copied_pids
            host_framing = map_framing(host_doc)

    if regroup and view is not None and copied_pids:
        try:
            regroup_stats = regroup_panels_in_host(
                host_doc, view, copied_pids, tag_mep=True
            )
            stats["host_groups"] = regroup_stats.get("groups", 0)
            stats["errors"].extend(regroup_stats.get("group_errors", []))
        except Exception as ex:
            stats["errors"].append("Regroup: {}".format(ex))

    stats["verify"] = verify_panel_copy(host_doc, copied_pids or selected)
    return stats


def regroup_panels_in_host(host_doc, view, selected, tag_mep=True):
    """Rebuild host panel groups (framing + MEP) after copy or regroup."""
    panel_elements = map_framing(host_doc)
    link_zones = map_framing_from_links(host_doc)
    remaining_link = map_link_framing_by_container(host_doc)
    _delete_groups_in_doc(host_doc, selected)
    return combine_panels_group_color(
        host_doc,
        view,
        selected,
        panel_elements,
        link_zones,
        link_framing=remaining_link,
        tag_mep=tag_mep,
    )


def verify_panel_copy(host_doc, panel_ids):
    """Return per-panel copy status for UI / debugging."""
    host_framing = map_framing(host_doc)
    link_framing = map_link_framing_by_container(host_doc)
    rows = []
    for pid in panel_ids:
        label = panel_display_name(pid)
        host_count = len(merge_framing_for_panel(host_framing, pid))
        link_count = len(merge_link_framing_for_panel(link_framing, pid))
        has_group = False
        for g in (
            DB.FilteredElementCollector(host_doc)
            .OfClass(DB.Group)
            .ToElements()
        ):
            if group_matches_panel(g.Name, pid):
                has_group = True
                break
        if host_count and has_group:
            status = "OK — in host, grouped"
        elif host_count:
            status = "Partial — host framing, no group"
        elif link_count:
            status = "Not copied — still in link only"
        else:
            status = "Missing — no framing found"
        rows.append({
            "panel": label,
            "host_framing": host_count,
            "link_framing": link_count,
            "host_group": has_group,
            "status": status,
        })
    return rows


def choose_panels(panel_ids):
    """Show a dialog letting user pick single panel, multiple, or all."""
    sorted_ids = sorted(panel_ids)
    options = ["All panels ({})".format(len(sorted_ids))] + sorted_ids
    from pyrevit import forms
    selected = forms.SelectFromList.show(
        options,
        title="UNIQUBE — Select Panel(s)",
        multiselect=True,
        button_name="Select",
    )
    if not selected:
        return None
    if any("All panels" in s for s in selected):
        return sorted_ids
    return selected
