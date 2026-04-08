# -*- coding: utf-8 -*-
"""Highlight MEP that crosses Space/Room boundaries; group panel equipment."""
from __future__ import print_function

from pyrevit import revit, DB, forms, script

from System.Collections.Generic import List

logger = script.get_logger()
doc = revit.doc
active_view = doc.ActiveView

RED = DB.Color(255, 0, 0)


def _eid_key(eid):
    """Stable int id for ElementId (Revit 2024+ uses Value)."""
    if eid is None:
        return None
    v = getattr(eid, "Value", None)
    if v is not None:
        return int(v)
    return int(eid.IntegerValue)


def _pt_in_bbox(pt, bbox):
    if bbox is None or bbox.Min is None or bbox.Max is None:
        return True
    mn, mx = bbox.Min, bbox.Max
    return (
        mn.X <= pt.X <= mx.X
        and mn.Y <= pt.Y <= mx.Y
        and mn.Z <= pt.Z <= mx.Z
    )


def _collect_spaces_and_rooms():
    """Revit rejects OfClass(Space)/OfClass(Room) in newer APIs; use SpatialElement."""
    spaces = []
    rooms = []
    cat_mep = int(DB.BuiltInCategory.OST_MEPSpaces)
    cat_room = int(DB.BuiltInCategory.OST_Rooms)
    col = DB.FilteredElementCollector(doc).OfClass(DB.SpatialElement)
    for se in col:
        if se is None or se.Location is None:
            continue
        cat = se.Category
        if cat is None:
            continue
        cid = _eid_key(cat.Id)
        if cid == cat_mep:
            spaces.append(se)
        elif cid == cat_room:
            rooms.append(se)
    return spaces, rooms


def _zone_at_point(pt, spaces, rooms):
    for s in spaces:
        if not _pt_in_bbox(pt, s.get_BoundingBox(None)):
            continue
        try:
            if s.IsPointInSpace(pt):
                return s.Id
        except Exception:
            pass
    for r in rooms:
        if not _pt_in_bbox(pt, r.get_BoundingBox(None)):
            continue
        try:
            if r.IsPointInRoom(pt):
                return r.Id
        except Exception:
            pass
    return None


def _zones_along_curve(curve, spaces, rooms):
    zone_keys = set()
    for t in (0.0, 0.5, 1.0):
        try:
            pt = curve.Evaluate(t, True)
        except Exception:
            continue
        zid = _zone_at_point(pt, spaces, rooms)
        if zid is not None:
            zone_keys.add(_eid_key(zid))
    return zone_keys


def _connected_mep_neighbors(elem):
    ids = []
    try:
        mm = elem.MEPModel
        if mm is None:
            return ids
        cm = mm.ConnectorManager
        if cm is None:
            return ids
        for c in cm.Connectors:
            if not c.IsConnected:
                continue
            for rf in c.AllRefs:
                try:
                    o = rf.Owner
                    if o is not None and o.Id != elem.Id:
                        ids.append(o.Id)
                except Exception:
                    pass
    except Exception:
        pass
    return ids


def _is_mep_fitting(elem):
    try:
        cat = elem.Category
        if cat is None:
            return False
        cid = _eid_key(cat.Id)
        return cid in (
            int(DB.BuiltInCategory.OST_DuctFitting),
            int(DB.BuiltInCategory.OST_PipeFitting),
            int(DB.BuiltInCategory.OST_ConduitFitting),
            int(DB.BuiltInCategory.OST_CableTrayFitting),
        )
    except Exception:
        return False


def _group_panels_in_view(view):
    """Create one model group from electrical equipment visible in the view."""
    col = (
        DB.FilteredElementCollector(doc, view.Id)
        .OfCategory(DB.BuiltInCategory.OST_ElectricalEquipment)
        .WhereElementIsNotElementType()
    )
    elems = [e for e in col if e.IsValidObject]
    if not elems:
        return None, 0, "No electrical equipment found in this view."
    id_list = List[DB.ElementId]()
    for e in elems:
        id_list.Add(e.Id)
    try:
        grp = doc.Create.NewGroup(id_list)
        return grp, len(elems), None
    except Exception as ex:
        return None, len(elems), str(ex)


def main():
    if isinstance(active_view, DB.ViewSheet):
        forms.alert("Open a model view (plan, 3D, section), not a sheet.", title="uniqube")
        return

    spaces, rooms = _collect_spaces_and_rooms()
    if not spaces and not rooms:
        forms.alert(
            "No Spaces or Rooms found in this model. Place MEP Spaces or Rooms "
            "to define panel boundaries, then run again.",
            title="uniqube",
        )
        return

    do_group = forms.alert(
        "Group all electrical equipment in this view into one Revit model group first?\n\n"
        "If Revit refuses (rules vary by content), choose No and only the red highlight will run.",
        title="uniqube",
        yes=True,
        no=True,
    )
    if do_group is None:
        return

    group_msg = ""
    if do_group:
        with revit.Transaction("uniqube: group panels"):
            grp, count, err = _group_panels_in_view(active_view)
        if err:
            group_msg = (
                "Could not create model group ({0} equipment in view).\n{1}\n\n"
                "Continuing with highlight only.".format(count, err)
            )
            forms.alert(group_msg, title="uniqube — grouping")
        elif grp is not None:
            group_msg = "Grouped {0} electrical equipment instance(s).".format(count)

    mep_cats = (
        DB.BuiltInCategory.OST_DuctCurves,
        DB.BuiltInCategory.OST_PipeCurves,
        DB.BuiltInCategory.OST_Conduit,
        DB.BuiltInCategory.OST_CableTray,
    )
    crossing_elems = []
    crossing_ids = set()

    for cat in mep_cats:
        col = (
            DB.FilteredElementCollector(doc, active_view.Id)
            .OfCategory(cat)
            .WhereElementIsNotElementType()
        )
        for el in col:
            loc = el.Location
            if not isinstance(loc, DB.LocationCurve):
                continue
            curve = loc.Curve
            if curve is None or curve.Length < 1e-6:
                continue
            zones = _zones_along_curve(curve, spaces, rooms)
            if len(zones) < 2:
                continue
            crossing_elems.append(el)
            crossing_ids.add(_eid_key(el.Id))
            for nid in _connected_mep_neighbors(el):
                ne = doc.GetElement(nid)
                if ne is not None and _is_mep_fitting(ne):
                    crossing_ids.add(_eid_key(nid))

    ogs = DB.OverrideGraphicSettings()
    ogs.SetProjectionLineColor(RED)
    ogs.SetCutLineColor(RED)
    try:
        ogs.SetProjectionLineWeight(6)
        ogs.SetCutLineWeight(6)
    except Exception:
        pass

    with revit.Transaction("uniqube: redline cross-panel MEP"):
        for eid_int in crossing_ids:
            try:
                eid = DB.ElementId(int(eid_int))
                active_view.SetElementOverrides(eid, ogs)
            except Exception as ex:
                logger.debug("Override failed for %s: %s", eid_int, ex)

    summary = []
    if group_msg:
        summary.append(group_msg)
    summary.append(
        "Crossing MEP (different Space/Room along run): {0} curve element(s).".format(
            len(crossing_elems)
        )
    )
    summary.append(
        "Red override applied to those elements and connected fittings in the active view."
    )
    forms.alert("\n".join(summary), title="uniqube")


main()
