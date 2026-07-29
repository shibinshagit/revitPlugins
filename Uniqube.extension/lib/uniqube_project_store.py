# -*- coding: utf-8 -*-
"""Extensible Storage: bind host document to a Uniqube project id."""
from __future__ import print_function

from System import Guid, String
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema,
    SchemaBuilder,
    Entity,
    AccessLevel,
)

SCHEMA_GUID = Guid("b8e4c2a1-9f3d-4e7b-8c1a-2d5f6a0b9e3c")
SCHEMA_NAME = "UniqubePublishBinding"
VENDOR_ID = "Uniqube"
FIELD_PROJECT_ID = "ProjectId"
FIELD_API_URL = "ApiUrl"


def _get_or_create_schema():
    existing = Schema.Lookup(SCHEMA_GUID)
    if existing:
        return existing
    builder = SchemaBuilder(SCHEMA_GUID)
    builder.SetSchemaName(SCHEMA_NAME)
    builder.SetReadAccessLevel(AccessLevel.Public)
    builder.SetWriteAccessLevel(AccessLevel.Public)
    builder.SetVendorId(VENDOR_ID)
    builder.AddSimpleField(FIELD_PROJECT_ID, String)
    builder.AddSimpleField(FIELD_API_URL, String)
    return builder.Finish()


def get_binding(doc):
    """Return dict with projectId (int or None) and apiUrl (str or None)."""
    schema = Schema.Lookup(SCHEMA_GUID)
    if not schema:
        return {"projectId": None, "apiUrl": None}
    entity = doc.ProjectInformation.GetEntity(schema)
    if not entity or not entity.IsValid():
        return {"projectId": None, "apiUrl": None}
    try:
        field_pid = schema.GetField(FIELD_PROJECT_ID)
        field_api = schema.GetField(FIELD_API_URL)
        pid_raw = entity.Get[String](field_pid)
        api = entity.Get[String](field_api)
        pid = int(pid_raw) if pid_raw and str(pid_raw).strip().isdigit() else None
        return {"projectId": pid, "apiUrl": str(api) if api else None}
    except Exception:
        return {"projectId": None, "apiUrl": None}


def set_binding(doc, project_id, api_url=None):
    """Write Uniqube project id onto ProjectInformation (transaction required)."""
    schema = _get_or_create_schema()
    entity = Entity(schema)
    entity.Set[String](schema.GetField(FIELD_PROJECT_ID), str(int(project_id)))
    entity.Set[String](schema.GetField(FIELD_API_URL), api_url or "")
    doc.ProjectInformation.SetEntity(entity)


def clear_binding(doc):
    """Remove Uniqube project binding from ProjectInformation (transaction required)."""
    schema = Schema.Lookup(SCHEMA_GUID)
    if not schema:
        return
    try:
        doc.ProjectInformation.DeleteEntity(schema)
    except Exception:
        # Fallback: overwrite with empty ids
        entity = Entity(schema)
        entity.Set[String](schema.GetField(FIELD_PROJECT_ID), "")
        entity.Set[String](schema.GetField(FIELD_API_URL), "")
        doc.ProjectInformation.SetEntity(entity)
