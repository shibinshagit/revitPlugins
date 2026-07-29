# -*- coding: utf-8 -*-
"""Export host + loaded Revit links to temporary IFC files for Uniqube publish."""
from __future__ import print_function

import os
import shutil
import tempfile

from Autodesk.Revit.DB import (
    FilteredElementCollector,
    RevitLinkInstance,
    RevitLinkType,
    IFCExportOptions,
    IFCVersion,
    Transaction,
    OpenOptions,
    DetachFromCentralOption,
    ModelPathUtils,
    ExternalFileUtils,
    BuiltInParameter,
    View,
    View3D,
    ViewSheet,
    ViewSchedule,
)


def infer_category(name):
    n = (name or "").lower()
    if "mep" in n or "plumb" in n or "hvac" in n or "pipe" in n:
        return "mep"
    if "elect" in n or "power" in n or "light" in n:
        return "electrical"
    if "struct" in n or "frame" in n or "beam" in n or "column" in n:
        return "structure"
    if "arch" in n:
        return "architecture"
    return "other"


def _safe_filename(name):
    bad = '<>:"/\\|?*'
    out = "".join(c if c not in bad else "_" for c in (name or "model"))
    return out.strip() or "model"


def _view_usable_for_ifc_filter(view):
    """True if this view's VV can drive 'export only visible elements'."""
    if view is None:
        return False
    try:
        if getattr(view, "IsTemplate", False):
            return False
    except Exception:
        pass
    if isinstance(view, ViewSheet) or isinstance(view, ViewSchedule):
        return False
    try:
        vt = view.ViewType
        # Sheets / schedules / browsers / legends are not model content filters
        bad_names = (
            "DrawingSheet",
            "Schedule",
            "Legend",
            "ProjectBrowser",
            "SystemBrowser",
            "Internal",
            "Report",
            "CostReport",
            "LoadsReport",
            "PresureLossReport",
            "PressureLossReport",
            "Walkthrough",
        )
        if str(vt) in bad_names:
            return False
    except Exception:
        pass
    return True


def _pick_filter_view(doc, preferred_view=None):
    """
    Choose the view whose Visibility/Graphics govern IFC contents.
    Prefer the caller's active/preferred view; else first non-template 3D view.
    Returns (view or None, reason string).
    """
    def _same_doc(view):
        try:
            return view.Document.PathName == doc.PathName and (
                (view.Document.Title or "") == (doc.Title or "")
            )
        except Exception:
            try:
                return view.Document is doc
            except Exception:
                return False

    if preferred_view is not None and _view_usable_for_ifc_filter(preferred_view):
        if _same_doc(preferred_view):
            return preferred_view, "active view"
        try:
            owned = doc.GetElement(preferred_view.Id)
            if owned is not None and _view_usable_for_ifc_filter(owned):
                return owned, "active view"
        except Exception:
            pass

    # Same name as preferred (useful when exporting a linked-model copy)
    if preferred_view is not None:
        try:
            want = (preferred_view.Name or "").strip()
            if want:
                for v in FilteredElementCollector(doc).OfClass(View):
                    if not _view_usable_for_ifc_filter(v):
                        continue
                    if (v.Name or "").strip() == want:
                        return v, "view named '{}'".format(want)
        except Exception:
            pass

    # Prefer a 3D view (typical publish target)
    try:
        for v in FilteredElementCollector(doc).OfClass(View3D):
            if _view_usable_for_ifc_filter(v):
                return v, "3D view"
    except Exception:
        pass

    try:
        for v in FilteredElementCollector(doc).OfClass(View):
            if _view_usable_for_ifc_filter(v):
                return v, "model view"
    except Exception:
        pass

    return None, "none"


def _make_ifc_options(filter_view_id=None):
    """
    Build IFC options. When filter_view_id is set, only elements visible in
    that view (category VV, filters, hide-element) are exported — same as
    Revit's 'Export only elements visible in view'.
    """
    options = IFCExportOptions()
    try:
        options.FileVersion = IFCVersion.IFC2x3CV2
    except Exception:
        try:
            options.FileVersion = IFCVersion.IFC2x3
        except Exception:
            pass
    if filter_view_id is not None:
        try:
            options.FilterViewId = filter_view_id
        except Exception as ex:
            print("Uniqube: FilterViewId not set: {}".format(ex))
    return options


def _param_str(el, names):
    """Read first non-empty string parameter by name (case-insensitive)."""
    wanted = set((n or "").strip().lower() for n in names)
    try:
        for p in el.Parameters:
            try:
                pname = (p.Definition.Name or "").strip().lower()
                if pname not in wanted:
                    continue
                if p.HasValue:
                    s = p.AsString()
                    if s is None:
                        try:
                            s = p.AsValueString()
                        except Exception:
                            s = None
                    if s and str(s).strip():
                        return str(s).strip()
            except Exception:
                continue
    except Exception:
        pass
    return None


def _stamp_sheathing_labels_into_mark(doc):
    """
    Revit Label (SH1…) often does not export to IFC.
    Copy Label → Mark for sheathing so IFC Tag carries SH* for Uniqube hide toggle.
    Returns list of (elementId, previousMark) for restore.
    """
    restored = []
    try:
        collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
        for el in collector:
            try:
                label = _param_str(el, ["Label"])
                if not label:
                    continue
                up = label.upper().strip()
                # SH1 / SH12 / SH-01 — Revit Label on gypsum / sheathing boards
                is_sh_mark = len(up) >= 3 and up.startswith("SH") and (
                    up[2].isdigit() or up[2] in "-_"
                )
                if not is_sh_mark:
                    continue

                mark_param = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
                if mark_param is None or mark_param.IsReadOnly:
                    continue
                prev = ""
                try:
                    prev = mark_param.AsString() or ""
                except Exception:
                    prev = ""
                if (prev or "").strip() == label:
                    continue
                mark_param.Set(label)
                restored.append((el.Id, prev))
            except Exception:
                continue
    except Exception as ex:
        print("Uniqube: stamp Label→Mark skipped: {}".format(ex))
    if restored:
        print("Uniqube: stamped Label into Mark for {} element(s)".format(len(restored)))
    return restored


def _restore_marks(doc, previous):
    if not previous:
        return
    for eid, prev in previous:
        try:
            el = doc.GetElement(eid)
            if el is None:
                continue
            mark_param = el.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
            if mark_param is None or mark_param.IsReadOnly:
                continue
            mark_param.Set(prev or "")
        except Exception:
            continue


def _find_new_ifc(folder, before, preferred_base):
    preferred = os.path.join(folder, preferred_base + ".ifc")
    if os.path.isfile(preferred):
        return preferred
    after = set(f for f in os.listdir(folder) if f.lower().endswith(".ifc"))
    new_files = sorted(after - before)
    if not new_files:
        raise Exception("IFC file not found after export (expected {}).".format(preferred))
    return os.path.join(folder, new_files[-1])


def _export_primary_doc_to_ifc(
    doc, folder, base_name, preferred_view=None, use_view_filter=True
):
    """
    Export a primary (non-linked) document. IFC export needs an open transaction.
    When use_view_filter is True, uses preferred/active view visibility so
    VV-hidden categories/elements are excluded from the IFC.
    """
    if getattr(doc, "IsLinked", False):
        raise Exception(
            "Refusing to export linked document '{}'. Open a primary copy first.".format(
                doc.Title
            )
        )

    filter_view = None
    filter_id = None
    if use_view_filter:
        filter_view, how = _pick_filter_view(doc, preferred_view)
        filter_id = filter_view.Id if filter_view is not None else None
        if filter_view is not None:
            print(
                "Uniqube: IFC visibility filter = '{}' ({})".format(
                    filter_view.Name, how
                )
            )
        else:
            print(
                "Uniqube: WARNING — no suitable view for visibility filter; "
                "exporting entire model for '{}'".format(doc.Title)
            )
    else:
        print("Uniqube: IFC full model export (no view visibility filter)")

    options = _make_ifc_options(filter_view_id=filter_id)
    before = set(f for f in os.listdir(folder) if f.lower().endswith(".ifc"))

    t = Transaction(doc, "Uniqube IFC Export")
    t.Start()
    stamped = []
    try:
        stamped = _stamp_sheathing_labels_into_mark(doc)
        ok = doc.Export(folder, base_name, options)
        _restore_marks(doc, stamped)
        t.Commit()
    except Exception:
        try:
            _restore_marks(doc, stamped)
        except Exception:
            pass
        if t.HasStarted():
            t.RollBack()
        raise

    if not ok:
        raise Exception("IFC export returned False for {}".format(base_name))
    return _find_new_ifc(folder, before, base_name), filter_view


def _resolve_link_path(host_doc, link_instance, link_doc):
    """Best-effort absolute path to the linked RVT on disk."""
    # 1) Linked document PathName
    try:
        p = link_doc.PathName
        if p and os.path.isfile(p):
            return p
        if p:
            return p
    except Exception:
        pass

    # 2) RevitLinkType external file reference
    try:
        link_type = host_doc.GetElement(link_instance.GetTypeId())
        if link_type and isinstance(link_type, RevitLinkType):
            try:
                ext_ref = ExternalFileUtils.GetExternalFileReference(host_doc, link_type.Id)
                if ext_ref:
                    model_path = ext_ref.GetPath()
                    visible = ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path)
                    if visible:
                        return visible
            except Exception:
                pass
            try:
                # Older API: AbsolutePath / PathType
                if hasattr(link_type, "GetExternalFileReference"):
                    ext_ref = link_type.GetExternalFileReference()
                    model_path = ext_ref.GetPath()
                    visible = ModelPathUtils.ConvertModelPathToUserVisiblePath(model_path)
                    if visible:
                        return visible
            except Exception:
                pass
    except Exception:
        pass

    return None


def _open_rvt_copy_as_primary(app, source_path, temp_dir, title_hint):
    """
    Copy the RVT to a temp path and open THAT copy.

    Opening the original while it is already loaded as a link often returns
    the linked document (transactions forbidden). A file copy is always primary.
    """
    if not source_path:
        raise Exception("No file path for linked model '{}'.".format(title_hint))

    # Cloud / non-local paths may not be copyable
    if not os.path.isfile(source_path):
        raise Exception(
            "Linked model path is not a local file:\n{}\n"
            "Save a local copy of the link and reload it.".format(source_path)
        )

    dest = os.path.join(
        temp_dir,
        "{}_{}".format(_safe_filename(title_hint), os.path.basename(source_path)),
    )
    # Avoid collision
    if os.path.exists(dest):
        root, ext = os.path.splitext(dest)
        dest = "{}_{}{}".format(root, os.getpid(), ext)

    shutil.copy2(source_path, dest)

    open_opts = OpenOptions()
    try:
        open_opts.DetachFromCentralOption = DetachFromCentralOption.DetachAndPreserveWorksets
    except Exception:
        pass

    try:
        model_path = ModelPathUtils.ConvertUserVisiblePathToModelPath(dest)
        opened = app.OpenDocumentFile(model_path, open_opts)
    except Exception:
        opened = app.OpenDocumentFile(dest)

    if opened is None:
        raise Exception("Failed to open temp copy of '{}'.".format(title_hint))
    if getattr(opened, "IsLinked", False):
        try:
            opened.Close(False)
        except Exception:
            pass
        raise Exception(
            "Opened document for '{}' is still marked as linked. "
            "Try unloading the link, then publish again.".format(title_hint)
        )
    return opened, dest


def collect_export_targets(host_doc):
    """
    Returns list of dicts for host + each loaded link.
    """
    targets = []
    host_title = host_doc.Title or "Host"
    targets.append(
        {
            "doc": host_doc,
            "link_instance": None,
            "title": host_title,
            "category": infer_category(host_title),
            "is_host": True,
        }
    )

    links = FilteredElementCollector(host_doc).OfClass(RevitLinkInstance).ToElements()
    for link in links:
        link_doc = link.GetLinkDocument()
        if not link_doc:
            continue
        title = link_doc.Title or link.Name or "Link"
        targets.append(
            {
                "doc": link_doc,
                "link_instance": link,
                "title": title,
                "category": infer_category(title),
                "is_host": False,
            }
        )
    return targets


def export_targets_to_temp(targets, preferred_view=None, use_view_filter=True):
    """
    Export each target to a temp folder.
    Host: export in-place with Transaction.
    When use_view_filter is True, IFC is filtered by preferred_view VV.
    Links: copy RVT → open copy as primary → export (match view by name / 3D) → close.
    Returns (results, folder).
    """
    if not targets:
        raise Exception("No documents to export.")

    host_doc = None
    for t in targets:
        if t.get("is_host"):
            host_doc = t["doc"]
            break
    if host_doc is None:
        host_doc = targets[0]["doc"]

    if getattr(host_doc, "IsLinked", False):
        raise Exception(
            "Active document is a link. Open the HOST project (Bathroom Pod 1 - MEP) "
            "and run Publish from that document."
        )

    app = host_doc.Application
    folder = tempfile.mkdtemp(prefix="uniqube_ifc_")
    rvt_copy_dir = os.path.join(folder, "_rvt_copies")
    os.makedirs(rvt_copy_dir)
    results = []

    for i, t in enumerate(targets):
        base = "{}_{}".format(i, _safe_filename(t["title"]))
        if t["category"] and t["category"] != "other":
            base = "{}_{}".format(base, t["category"].upper())

        filter_view = None
        if t.get("is_host"):
            path, filter_view = _export_primary_doc_to_ifc(
                t["doc"],
                folder,
                base,
                preferred_view=preferred_view,
                use_view_filter=use_view_filter,
            )
        else:
            source = _resolve_link_path(host_doc, t.get("link_instance"), t["doc"])
            opened = None
            try:
                opened, _copied = _open_rvt_copy_as_primary(
                    app, source, rvt_copy_dir, t["title"]
                )
                # Linked copies: try same view name as host active, else 3D
                path, filter_view = _export_primary_doc_to_ifc(
                    opened,
                    folder,
                    base,
                    preferred_view=preferred_view,
                    use_view_filter=use_view_filter,
                )
            finally:
                if opened is not None:
                    try:
                        opened.Close(False)
                    except Exception:
                        pass

        results.append(
            {
                "path": path,
                "category": t["category"],
                "title": t["title"],
                "is_host": t["is_host"],
                "filter_view_name": filter_view.Name if filter_view else None,
            }
        )

    return results, folder
