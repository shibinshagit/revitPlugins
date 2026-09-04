# -*- coding: utf-8 -*-
"""Write BIMSF + colour sidecar JSON for an already-uploaded Uniqube project.

No IFC export, no drawings, no upload. Fast enough to run over AnyDesk.
The JSON is the same shape the publish plugin already sends.
"""

from __future__ import print_function

import json
import os
import re
from datetime import datetime

from pyrevit import forms, revit, script

import uniqube_bimsf_export as bimsf_export
import uniqube_color_export as color_export


def _safe_stem(text):
    raw = (text or "model").strip() or "model"
    cleaned = re.sub(r"[^\w\-.]+", "_", raw)
    return cleaned[:80] or "model"


def _guess_category(doc, bimsf_map):
    title = ((doc.Title or "") + " " + (bimsf_map.get("documentTitle") or "")).lower()
    if any(w in title for w in ("mep", "mechanical", "plumbing", "electrical")):
        return "MEP"
    if any(w in title for w in ("acp", "arch", "architecture", "interior")):
        return "ARCHITECTURE"
    if any(w in title for w in ("fram", "struct", "mwf", "cfs")):
        return "STRUCTURE"

    cats = {}
    for el in bimsf_map.get("elements") or []:
        name = (el.get("category") or "").lower()
        if not name:
            continue
        cats[name] = cats.get(name, 0) + 1
    joined = " ".join(cats.keys())
    if any(w in joined for w in ("pipe", "duct", "conduit", "fixture", "mechanical")):
        return "MEP"
    if "wall" in joined and "framing" not in joined:
        return "ARCHITECTURE"
    return "STRUCTURE"


def export_sidecars(doc, out_dir):
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    bimsf_map = bimsf_export.build_bimsf_map(doc)
    color_map = color_export.build_color_map(doc)
    category = _guess_category(doc, bimsf_map)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = "{}_{}_{}".format(_safe_stem(doc.Title), category, stamp)

    bimsf_path = os.path.join(out_dir, stem + "_bimsf.json")
    color_path = os.path.join(out_dir, stem + "_color.json")
    manifest_path = os.path.join(out_dir, stem + "_manifest.json")

    payload = {
        "version": 1,
        "kind": "uniqube-sidecar",
        "exportedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "documentTitle": doc.Title,
        "category": category,
        "bimsf": {
            "count": bimsf_map.get("count") or 0,
            "panelCount": bimsf_map.get("panelCount") or 0,
            "panels": [p.get("displayName") or p.get("id") for p in (bimsf_map.get("panels") or [])],
        },
        "color": {"count": color_map.get("count") or 0},
        "files": {
            "bimsf": os.path.basename(bimsf_path),
            "color": os.path.basename(color_path),
        },
    }

    with open(bimsf_path, "w") as handle:
        json.dump(bimsf_map, handle)
    with open(color_path, "w") as handle:
        json.dump(color_map, handle)
    with open(manifest_path, "w") as handle:
        json.dump(payload, handle, indent=2)

    return {
        "category": category,
        "bimsfPath": bimsf_path,
        "colorPath": color_path,
        "manifestPath": manifest_path,
        "bimsf": bimsf_map,
        "color": color_map,
        "manifest": payload,
    }


def run():
    doc = revit.doc
    if doc is None:
        forms.alert("Open a Revit model first.", title="Export Maps")
        return

    out_dir = forms.pick_folder(title="Folder for BIMSF / colour JSON (Desktop is fine)")
    if not out_dir:
        return

    output = script.get_output()
    output.print_md("### Exporting maps from **{}**".format(doc.Title))
    result = export_sidecars(doc, out_dir)
    panels = result["manifest"]["bimsf"]["panels"]
    sample = ", ".join(panels[:12]) if panels else "(none — Fill BIMSF first if this is MEP)"

    forms.alert(
        "Exported {category}\n\n"
        "BIMSF elements: {count}\n"
        "Panels: {panels}\n"
        "Sample: {sample}\n\n"
        "Colour entries: {colors}\n\n"
        "Saved to:\n{folder}\n\n"
        "Send the three JSON files to Uniqube. No publish needed.".format(
            category=result["category"],
            count=result["manifest"]["bimsf"]["count"],
            panels=result["manifest"]["bimsf"]["panelCount"],
            sample=sample,
            colors=result["manifest"]["color"]["count"],
            folder=out_dir,
        ),
        title="Export Maps",
    )
    output.print_md(
        "- Category: **{}**\n- BIMSF: `{}`\n- Colour: `{}`\n- Manifest: `{}`".format(
            result["category"],
            result["bimsfPath"],
            result["colorPath"],
            result["manifestPath"],
        )
    )
