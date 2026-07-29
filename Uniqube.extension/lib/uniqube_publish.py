# -*- coding: utf-8 -*-
"""Shared Uniqube publish flow (IFC + Project Browser + CAD).

Called by Publish Local / Publish Live ribbon buttons with target='local'|'live'.
"""
from __future__ import print_function

import os
import shutil
import tempfile

from pyrevit import revit, forms, script

logger = script.get_logger()

DISCIPLINE_OPTIONS = [
    "Architecture",
    "MEP",
    "Structure",
]

DISCIPLINE_TO_CATEGORY = {
    "Structure": "structure",
    "MEP": "mep",
    "Architecture": "architecture",
}


def _status(msg):
    """Write into the pyRevit output window so it is never a blank white pane."""
    try:
        script.get_output().print_html(
            "<div style='font-family:Consolas,monospace;margin:2px 0'>{}</div>".format(
                str(msg).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            )
        )
    except Exception:
        try:
            print(msg)
        except Exception:
            pass


def _pick_one(options, title, button_name="Continue"):
    """Reliable single-select (CommandSwitchWindow often returns None / blank UI)."""
    return forms.SelectFromList.show(
        list(options),
        title=title,
        button_name=button_name,
        multiselect=False,
    )


def _guess_discipline_label(doc_title, host_category):
    """Pick a default discipline row."""
    cat = (host_category or "").lower()
    title = (doc_title or "").lower()
    if cat == "mep" or "mep" in title:
        return "MEP"
    if cat == "architecture" or "arch" in title:
        return "Architecture"
    if cat == "structure" or "struct" in title:
        return "Structure"
    return "Structure"


def _pick_publish_target(client, api_url, token, bound_id, dialog_title):
    """
    Ask: new project vs existing.
    Returns (project_id or None, project_name or None, mode_label).
    """
    options = [
        "Create new Uniqube project",
        "Publish to existing Uniqube project",
    ]
    if bound_id:
        options.insert(
            0,
            "Update bound project ({})".format(bound_id),
        )

    choice = _pick_one(
        options,
        title="{} - where to publish?".format(dialog_title),
        button_name="Next",
    )
    if not choice:
        forms.alert("Publish cancelled (no destination selected).", title=dialog_title)
        return None, None, None

    if choice.startswith("Update bound project"):
        if client.project_exists(api_url, token, bound_id):
            return int(bound_id), None, "bound"
        forms.alert(
            "Bound project {} no longer exists on Uniqube.\n"
            "Choose another existing project or create a new one.".format(bound_id),
            title=dialog_title,
        )
        return _pick_publish_target(client, api_url, token, None, dialog_title)

    if choice.startswith("Create new"):
        return None, None, "new"

    try:
        projects = client.list_projects(api_url, token, limit=200)
    except Exception as ex:
        forms.alert("Could not load projects:\n\n{}".format(ex), title=dialog_title)
        return None, None, None

    if not projects:
        forms.alert(
            "No Uniqube projects found for this account.\nCreate a new project instead.",
            title=dialog_title,
        )
        return None, None, "new"

    try:
        projects = sorted(
            projects,
            key=lambda p: p.get("updatedAt") or p.get("createdAt") or "",
            reverse=True,
        )
    except Exception:
        pass

    labels = []
    by_label = {}
    for p in projects:
        pid = p.get("id")
        name = p.get("name") or "Project"
        disp = p.get("displayNumber")
        label = "{}  -  #{}  (id {})".format(name, disp if disp is not None else "?", pid)
        labels.append(label)
        by_label[label] = p

    selected = _pick_one(
        labels,
        title="Select Uniqube project",
        button_name="Publish here",
    )
    if not selected:
        forms.alert("Publish cancelled (no project selected).", title=dialog_title)
        return None, None, None

    proj = by_label.get(selected)
    if not proj:
        forms.alert("Could not resolve selected project.", title=dialog_title)
        return None, None, None
    return int(proj["id"]), proj.get("name"), "existing"


def run_publish(target="local"):
    """Run the full publish flow.

    target: 'local' -> local API (dev); 'live' -> production API.
    """
    t = (target or "local").strip().lower()
    if t == "live":
        dialog_title = "Publish Live"
        progress_title = "Publishing to Uniqube Live"
        target_label = "LIVE"
    else:
        dialog_title = "Publish Local"
        progress_title = "Publishing to Uniqube Local"
        target_label = "LOCAL"
        t = "local"

    try:
        _run_publish_inner(t, dialog_title, progress_title, target_label)
    except Exception as ex:
        logger.error(str(ex))
        _status("ERROR: {}".format(ex))
        forms.alert("Publish failed:\n\n{}".format(ex), title=dialog_title)


def _run_publish_inner(t, dialog_title, progress_title, target_label):
    _status("{} starting...".format(dialog_title))

    doc = revit.doc
    if doc is None or doc.IsFamilyDocument:
        forms.alert("Open a project document (host) first.", title=dialog_title)
        return

    import uniqube_secrets
    import uniqube_project_store
    import uniqube_ifc_export
    import uniqube_client
    import uniqube_browser_snapshot
    import uniqube_drawing_export

    api_url, email, password, frontend_url = uniqube_secrets.uniqube_config_for(t)
    _status("API: {}".format(api_url))
    if not email or not password:
        forms.alert(
            "Missing uniqube_email / uniqube_password in vibe_secrets.json.\n\n"
            "See vibe_secrets.example.json",
            title=dialog_title,
        )
        return

    targets = uniqube_ifc_export.collect_export_targets(doc)
    host_guess = None
    for tgt in targets:
        if tgt.get("is_host"):
            host_guess = tgt.get("category")
            break

    # 1) Discipline for THIS file (Structure / MEP / Architecture)
    default_disc = _guess_discipline_label(doc.Title, host_guess)
    ordered = [default_disc] + [d for d in DISCIPLINE_OPTIONS if d != default_disc]
    discipline = _pick_one(
        ordered,
        title="{} - which discipline is this model?".format(dialog_title),
        button_name="Next",
    )
    if not discipline:
        forms.alert("Publish cancelled (no discipline selected).", title=dialog_title)
        return
    if discipline not in DISCIPLINE_TO_CATEGORY:
        forms.alert(
            "Unexpected discipline value: {!r}".format(discipline),
            title=dialog_title,
        )
        return
    _status("Discipline: {}".format(discipline))

    host_category = DISCIPLINE_TO_CATEGORY[discipline]
    for tgt in targets:
        if tgt.get("is_host"):
            tgt["category"] = host_category

    # Uniqube expects separate Structure / MEP publishes. If Structure is still
    # linked in the MEP file, default to publishing the host only.
    link_targets = [tgt for tgt in targets if not tgt.get("is_host")]
    if link_targets:
        HOST_ONLY = "This model only (recommended - separate Structure/MEP publish)"
        HOST_AND_LINKS = "This model + loaded links"
        scope = _pick_one(
            [HOST_ONLY, HOST_AND_LINKS],
            title="{} - {} link(s) detected".format(dialog_title, len(link_targets)),
            button_name="Next",
        )
        if not scope:
            forms.alert("Publish cancelled (no scope selected).", title=dialog_title)
            return
        if scope == HOST_ONLY:
            targets = [tgt for tgt in targets if tgt.get("is_host")]

    lines = []
    for tgt in targets:
        role = "HOST" if tgt["is_host"] else "LINK"
        lines.append(
            "{} | {} -> {}".format(role, tgt["title"], tgt["category"].upper())
        )

    binding = uniqube_project_store.get_binding(doc)
    bound_id = binding.get("projectId")

    _status("Logging in to {} ...".format(api_url))
    try:
        token = uniqube_client.login(api_url, email, password)
    except Exception as login_ex:
        forms.alert(
            "Could not reach Uniqube {} API:\n{}\n\n"
            "Live URL must be https://api.uniqube3d.co\n"
            "(not the raw ALB *.elb.amazonaws.com hostname)."
            .format(target_label, login_ex),
            title=dialog_title,
        )
        return
    _status("Login OK")

    # Clear stale binding if needed (non-blocking verify)
    if bound_id:
        try:
            if not uniqube_client.project_exists(api_url, token, bound_id):
                from Autodesk.Revit.DB import Transaction as TxClear

                tclear = TxClear(doc, "Clear Uniqube Binding")
                tclear.Start()
                try:
                    uniqube_project_store.clear_binding(doc)
                    tclear.Commit()
                except Exception:
                    if tclear.HasStarted():
                        tclear.RollBack()
                    raise
                bound_id = None
                forms.alert(
                    "Previously bound Uniqube project was deleted.\n"
                    "Choose a new or existing project on the next step.",
                    title=dialog_title,
                )
        except Exception as verify_ex:
            logger.warning("Project verify failed: {}".format(verify_ex))

    # 2) New vs existing project
    project_id, existing_name, mode = _pick_publish_target(
        uniqube_client, api_url, token, bound_id, dialog_title
    )
    if mode is None:
        return

    project_name = doc.Title or "Revit Project"
    if mode == "new":
        name_in = forms.ask_for_string(
            default=project_name,
            prompt="Name for the NEW Uniqube project:",
            title=dialog_title,
        )
        if not name_in:
            forms.alert("Publish cancelled (no project name).", title=dialog_title)
            return
        project_name = name_in.strip()
        project_id = None
    else:
        # Existing / bound - keep name for job label
        project_name = existing_name or project_name

    # Ask: exclude VV-hidden (default) vs publish full model
    EXCLUDE_VISIBLE = "Exclude hidden (only visible in active view)"
    INCLUDE_ALL = "Include everything (full model)"
    visibility_choice = _pick_one(
        [EXCLUDE_VISIBLE, INCLUDE_ALL],
        title="{} - IFC model content?".format(dialog_title),
        button_name="Next",
    )
    if not visibility_choice:
        forms.alert("Publish cancelled (no visibility option).", title=dialog_title)
        return
    use_view_filter = visibility_choice == EXCLUDE_VISIBLE

    filter_view = None
    filter_how = None
    if use_view_filter:
        active_view = None
        try:
            uidoc = revit.uidoc
            if uidoc is not None:
                active_view = uidoc.ActiveView
        except Exception:
            active_view = None

        filter_view, filter_how = uniqube_ifc_export._pick_filter_view(doc, active_view)
        if filter_view is None:
            forms.alert(
                "Open a model view (e.g. your 3D framing view) before publishing "
                "with 'Exclude hidden'.\n\n"
                "Or choose 'Include everything' to publish the full model.",
                title=dialog_title,
            )
            return

    if use_view_filter:
        visibility_line = (
            "IFC visibility: only elements visible in\n"
            "  '{}' ({})\n"
            "(VV / hide-element / view filters apply)\n\n"
        ).format(filter_view.Name, filter_how)
    else:
        visibility_line = (
            "IFC visibility: FULL MODEL\n"
            "(hidden / VV-off elements are included)\n\n"
        )

    confirm = (
        "Destination: {} ({})\n"
        "Discipline: {}\n\n"
        "Will export:\n{}\n\n"
        "{}"
        "Also: Project Browser + CAD drawings.\n"
        "BIMSF_Container map is captured from THIS file only "
        "(viewer joins Structure + MEP by panel id).\n\n"
    ).format(target_label, api_url, discipline, "\n".join(lines), visibility_line)
    if project_id:
        confirm += "Target: EXISTING Uniqube project id {}\n".format(project_id)
        confirm += "Only this discipline's model version will be updated.\n"
        confirm += "Other disciplines on that project stay as they are.\n"
    else:
        confirm += "Target: CREATE new Uniqube project \"{}\"\n".format(project_name)

    if not forms.alert(confirm + "\nContinue?", title=dialog_title, ok=False, yes=True, no=True):
        _status("Cancelled at confirm.")
        return

    folder = None
    draw_folder = None
    try:
        with forms.ProgressBar(title=progress_title, cancellable=False) as pb:
            pb.update_progress(3, 100)

            pb.update_progress(5, 100)
            snapshot = uniqube_browser_snapshot.build_browser_snapshot(doc)
            logger.info(
                "Browser snapshot: {} views, {} sheets, {} schedules".format(
                    len(snapshot.get("views") or []),
                    len(snapshot.get("sheets") or []),
                    len(snapshot.get("schedules") or []),
                )
            )

            pb.update_progress(10, 100)

            import uniqube_color_export
            import uniqube_bimsf_export

            # Capture colours + BIMSF before IFC export (link docs must still be loaded)
            color_maps = []
            bimsf_maps = []
            for tgt in targets:
                src_doc = tgt.get("doc") or doc
                title = tgt.get("title") or "model"
                try:
                    cmap = uniqube_color_export.build_color_map(src_doc)
                    color_maps.append(cmap)
                    logger.info(
                        "Color map for {}: {} coloured elements".format(
                            title,
                            cmap.get("count") or 0,
                        )
                    )
                except Exception as cex:
                    logger.warning("Color map failed for {}: {}".format(title, cex))
                    color_maps.append({
                        "version": 1,
                        "count": 0,
                        "byElementId": {},
                        "byIfcGuid": {},
                        "byUniqueId": {},
                        "elements": [],
                    })
                try:
                    bmap = uniqube_bimsf_export.build_bimsf_map(src_doc)
                    bimsf_maps.append(bmap)
                    logger.info(
                        "BIMSF map for {}: {} elements, {} panels".format(
                            title,
                            bmap.get("count") or 0,
                            bmap.get("panelCount") or 0,
                        )
                    )
                except Exception as bex:
                    logger.warning("BIMSF map failed for {}: {}".format(title, bex))
                    bimsf_maps.append({
                        "version": 1,
                        "count": 0,
                        "panelCount": 0,
                        "byElementId": {},
                        "byIfcGuid": {},
                        "byUniqueId": {},
                        "byPanel": {},
                        "panels": [],
                        "elements": [],
                    })

            exports, folder = uniqube_ifc_export.export_targets_to_temp(
                targets,
                preferred_view=filter_view,
                use_view_filter=use_view_filter,
            )
            for e in exports:
                logger.info(
                    "IFC {} filter view: {}".format(
                        e.get("title"),
                        e.get("filter_view_name") or "(full model)",
                    )
                )
            paths = [e["path"] for e in exports]
            categories = [e["category"] for e in exports]
            pb.update_progress(35, 100)

            draw_folder = tempfile.mkdtemp(prefix="uniqube_draw_")

            def draw_progress(cur, total, message):
                frac = float(cur) / float(max(total, 1))
                pb.update_progress(int(35 + frac * 20), 100)
                logger.info(message)

            manifest, draw_errors = uniqube_drawing_export.export_drawings(
                doc, draw_folder, progress_cb=draw_progress
            )
            for err in draw_errors:
                logger.warning(err)
            logger.info("Exported {} CAD drawing(s)".format(len(manifest)))

            drawing_files = []
            for i, m in enumerate(manifest):
                if m.get("dxfPath") and os.path.isfile(m["dxfPath"]):
                    drawing_files.append({
                        "field": "drawingDxf{}".format(i),
                        "path": m["dxfPath"],
                        "filename": m.get("dxfFileName") or os.path.basename(m["dxfPath"]),
                    })
                if m.get("dwgPath") and os.path.isfile(m["dwgPath"]):
                    drawing_files.append({
                        "field": "drawingDwg{}".format(i),
                        "path": m["dwgPath"],
                        "filename": m.get("dwgFileName") or os.path.basename(m["dwgPath"]),
                    })

            pb.update_progress(55, 100)

            result = uniqube_client.publish_files(
                api_url,
                token,
                paths,
                categories,
                project_id=project_id,
                project_name=project_name,
                browser_snapshot=snapshot,
                drawing_manifest=manifest,
                drawing_files=drawing_files,
                color_maps=color_maps,
                bimsf_maps=bimsf_maps,
            )
            job_id = result.get("jobId")
            if not job_id:
                raise Exception("No jobId returned: {}".format(result))

            pb.update_progress(65, 100)
            status = uniqube_client.wait_for_job(api_url, token, job_id)
            pb.update_progress(95, 100)

            if status.get("status") != "completed":
                raise Exception(status.get("error") or status.get("message") or "Publish failed")

            out_project_id = status.get("projectId") or (status.get("projectData") or {}).get("id")
            if out_project_id:
                from Autodesk.Revit.DB import Transaction

                tx_bind = Transaction(doc, "Bind Uniqube Project")
                tx_bind.Start()
                try:
                    uniqube_project_store.set_binding(doc, out_project_id, api_url)
                    tx_bind.Commit()
                except Exception:
                    if tx_bind.HasStarted():
                        tx_bind.RollBack()
                    raise

            summaries = status.get("remapSummaries") or []
            summary_txt = "\n".join(
                [
                    "  {category} v{version}: remapped={remapped}, orphaned={orphaned}, created={created}".format(
                        **{
                            "category": s.get("category"),
                            "version": s.get("version"),
                            "remapped": s.get("remapped"),
                            "orphaned": s.get("orphaned"),
                            "created": s.get("created"),
                        }
                    )
                    for s in summaries
                ]
            )
            forms.alert(
                "Publish complete ({})\n\n"
                "Discipline: {}\n"
                "Uniqube project id: {}\n"
                "Drawings uploaded: {}\n"
                "API: {}\n"
                "Open project:\n{}/projects/{}\n\n"
                "{}".format(
                    target_label,
                    discipline,
                    out_project_id,
                    len(manifest),
                    api_url,
                    frontend_url.rstrip("/"),
                    out_project_id,
                    summary_txt or status.get("message", ""),
                ),
                title=dialog_title,
            )
            pb.update_progress(100, 100)
            _status("Publish complete. Project id: {}".format(out_project_id))

    except Exception as ex:
        logger.error(str(ex))
        _status("ERROR: {}".format(ex))
        forms.alert("Publish failed:\n\n{}".format(ex), title=dialog_title)
    finally:
        for d in (folder, draw_folder):
            if d and os.path.isdir(d):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass


def main():
    """Backward-compatible entry - defaults to local."""
    run_publish("local")


if __name__ == "__main__":
    main()
