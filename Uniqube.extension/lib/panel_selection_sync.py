# -*- coding: utf-8 -*-
"""Keep panel + MEP selected together when clicking in the view.

Uses UIApplication.Idling (works on all Revit versions — UIDocument
does not expose SelectionChanged in older releases).
"""
from System import EventHandler
from System.Collections.Generic import List
from Autodesk.Revit.UI.Events import IdlingEventArgs
from pyrevit import DB

import panel_utils as pu

_uiapp = None
_idling_handler = None
_host_doc = None
_enabled = False
_syncing = False
_last_panel_key = None
_link_framing_cache = None
_link_framing_doc_title = None


def _selection_fingerprint(uidoc):
    parts = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            parts.append("H{}".format(eid.IntegerValue))
    except Exception:
        pass
    try:
        for ref in uidoc.Selection.GetReferences():
            le = ref.LinkedElementId
            le_id = le.IntegerValue if le else -1
            parts.append("L{}:{}".format(ref.ElementId.IntegerValue, le_id))
    except Exception:
        pass
    parts.sort()
    return tuple(parts)


def _panel_key(pid):
    if not pid:
        return None
    return pu.panel_display_name(pid).lower()


def _container_value(elem):
    if elem is None:
        return None
    p = elem.LookupParameter(pu.PARAM_NAME)
    if p and p.HasValue:
        val = p.AsString()
        if val:
            return val
    p = elem.LookupParameter(pu.PANEL_NAME_PARAM)
    if p and p.HasValue:
        val = p.AsString()
        if val:
            return val
    return None


def _host_group_name_for_element(host_doc, elem):
    try:
        gid = elem.GroupId
        if gid is None or gid == DB.ElementId.InvalidElementId:
            return None
        grp = host_doc.GetElement(gid)
        if grp is not None and isinstance(grp, DB.Group):
            return grp.Name
    except Exception:
        pass
    return None


def _host_assembly_for_panel(host_doc, pid):
    for asm in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.AssemblyInstance)
        .ToElements()
    ):
        try:
            asm_name = asm.AssemblyTypeName
        except Exception:
            asm_name = ""
        if pu.assembly_matches_panel(asm_name, pid):
            return asm
    return None


def _host_group_for_panel(host_doc, pid):
    for g in (
        DB.FilteredElementCollector(host_doc)
        .OfClass(DB.Group)
        .ToElements()
    ):
        if pu.group_matches_panel(pu.group_label(g), pid):
            return g
    return None


def _selection_includes_container(uidoc, container):
    if container is None:
        return False
    cid = container.Id.IntegerValue
    try:
        for eid in uidoc.Selection.GetElementIds():
            if eid.IntegerValue == cid:
                return True
    except Exception:
        pass
    try:
        for ref in uidoc.Selection.GetReferences():
            if ref.LinkedElementId != DB.ElementId.InvalidElementId:
                continue
            if ref.ElementId.IntegerValue == cid:
                return True
    except Exception:
        pass
    return False


def _selection_includes_group(uidoc, group):
    return _selection_includes_container(uidoc, group)


def detect_panel_from_selection(uidoc, host_doc):
    """Return one panel id if the current selection belongs to a single panel."""
    panels = []

    for eid in uidoc.Selection.GetElementIds():
        el = host_doc.GetElement(eid)
        if el is None:
            continue
        if isinstance(el, DB.Group):
            panels.append(pu.panel_display_name(el.Name))
            continue
        if isinstance(el, DB.AssemblyInstance):
            try:
                asm_name = el.AssemblyTypeName
                if asm_name.startswith("BIMSF_Panel_"):
                    asm_name = asm_name[len("BIMSF_Panel_"):]
                panels.append(pu.panel_display_name(asm_name))
            except Exception:
                pass
            continue
        c = _container_value(el)
        if c:
            panels.append(c)
            continue
        gname = _host_group_name_for_element(host_doc, el)
        if gname:
            panels.append(pu.panel_display_name(gname))
            continue
        try:
            asm_id = el.AssemblyInstanceId
            if asm_id is not None and asm_id != DB.ElementId.InvalidElementId:
                asm = host_doc.GetElement(asm_id)
                if isinstance(asm, DB.AssemblyInstance):
                    asm_name = asm.AssemblyTypeName
                    if asm_name.startswith("BIMSF_Panel_"):
                        asm_name = asm_name[len("BIMSF_Panel_"):]
                    panels.append(pu.panel_display_name(asm_name))
        except Exception:
            pass

    invalid = DB.ElementId.InvalidElementId
    for ref in uidoc.Selection.GetReferences():
        try:
            link_elem_id = ref.LinkedElementId
        except Exception:
            link_elem_id = invalid
        if link_elem_id is None or link_elem_id == invalid:
            continue
        link_inst = host_doc.GetElement(ref.ElementId)
        if link_inst is None:
            continue
        link_doc = link_inst.GetLinkDocument()
        if link_doc is None:
            continue
        elem = link_doc.GetElement(link_elem_id)
        if elem is None:
            continue
        if isinstance(elem, DB.Group):
            panels.append(pu.panel_display_name(elem.Name))
            continue
        c = _container_value(elem)
        if c:
            panels.append(c)

    if not panels:
        return None

    displays = set()
    canonical = []
    for p in panels:
        d = pu.panel_display_name(p).lower()
        if d not in displays:
            displays.add(d)
            canonical.append(p)

    if len(displays) == 1:
        return canonical[0]
    return None


def _cached_link_framing(doc):
    global _link_framing_cache, _link_framing_doc_title
    title = doc.Title
    if _link_framing_cache is None or _link_framing_doc_title != title:
        _link_framing_cache = pu.map_link_framing_by_container(doc)
        _link_framing_doc_title = title
    return _link_framing_cache


def _process_selection(uidoc):
    global _syncing, _last_panel_key
    if _syncing or not _enabled:
        return

    doc = uidoc.Document
    if _host_doc is not None and doc.Title != _host_doc.Title:
        return

    fp = _selection_fingerprint(uidoc)
    if not fp:
        _last_panel_key = None
        return

    pid = detect_panel_from_selection(uidoc, doc)
    if not pid:
        _last_panel_key = None
        return

    panel_key = _panel_key(pid)
    if panel_key == _last_panel_key:
        return

    host_asm = _host_assembly_for_panel(doc, pid)
    if host_asm is not None and _selection_includes_container(uidoc, host_asm):
        _last_panel_key = panel_key
        return

    host_group = _host_group_for_panel(doc, pid)
    if _selection_includes_group(uidoc, host_group):
        _last_panel_key = panel_key
        return

    try:
        _syncing = True
        link_framing = _cached_link_framing(doc)
        if host_asm is not None:
            uidoc.Selection.SetElementIds(
                List[DB.ElementId]([host_asm.Id])
            )
        else:
            pu.select_panel_pair(uidoc, doc, pid, link_framing)
        _last_panel_key = panel_key
    except Exception:
        _last_panel_key = panel_key
    finally:
        _syncing = False


def _on_idling(sender, args):
    if not _enabled:
        return
    uidoc = sender.ActiveUIDocument
    if uidoc is None:
        return
    _process_selection(uidoc)


def is_enabled():
    return _enabled


def enable(uidoc):
    global _uiapp, _idling_handler, _host_doc, _enabled, _last_panel_key
    global _link_framing_cache, _link_framing_doc_title
    disable(uidoc)
    _host_doc = uidoc.Document
    _uiapp = uidoc.Application
    _idling_handler = EventHandler[IdlingEventArgs](_on_idling)
    _uiapp.Idling += _idling_handler
    _enabled = True
    _last_panel_key = None
    _link_framing_cache = None
    _link_framing_doc_title = None
    return True


def disable(uidoc):
    global _uiapp, _idling_handler, _host_doc, _enabled, _last_panel_key
    global _link_framing_cache, _link_framing_doc_title
    if _idling_handler is not None and _uiapp is not None:
        try:
            _uiapp.Idling -= _idling_handler
        except Exception:
            pass
    _idling_handler = None
    _uiapp = None
    _host_doc = None
    _enabled = False
    _last_panel_key = None
    _link_framing_cache = None
    _link_framing_doc_title = None


def toggle(uidoc):
    if _enabled:
        disable(uidoc)
        return False
    enable(uidoc)
    return True
