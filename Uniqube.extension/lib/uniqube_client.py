# -*- coding: utf-8 -*-
"""HTTP client for Uniqube API (IronPython / .NET)."""
from __future__ import print_function

import json
import time

import clr

clr.AddReference("System")
from System import Array, Byte
from System.IO import File, StreamReader, MemoryStream
from System.Net import HttpWebRequest, WebException, ServicePointManager, SecurityProtocolType
from System.Text import Encoding

try:
    from uniqube_text import (
        as_unicode,
        as_net_string,
        exception_text,
        json_ascii_text,
        safe_json,
    )
except Exception:
    as_unicode = None
    as_net_string = None
    exception_text = None
    json_ascii_text = None
    safe_json = None


def _json_field(obj):
    """ASCII JSON that survives Revit names outside the ASCII range."""
    if safe_json is not None:
        return safe_json(obj)
    return json.dumps(obj, ensure_ascii=True)


def _json_parse(text):
    """json.loads on a response that may carry non-ASCII project names."""
    if json_ascii_text is not None:
        return json.loads(json_ascii_text(text))
    return json.loads(text)


def _ensure_tls():
    """Prefer TLS 1.2+ for HTTPS (AWS ALB / modern APIs)."""
    try:
        # Bitwise OR keeps existing flags; values are stable on .NET Framework.
        ServicePointManager.SecurityProtocol = (
            SecurityProtocolType.Tls12 | SecurityProtocolType.Tls11 | SecurityProtocolType.Tls
        )
    except Exception:
        try:
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12
        except Exception:
            pass


def _read_response(resp):
    stream = resp.GetResponseStream()
    reader = StreamReader(stream)
    try:
        return reader.ReadToEnd()
    finally:
        reader.Close()
        stream.Close()


def _format_web_error(ex, context):
    """Turn WebException into a short actionable message (SSL vs HTTP)."""
    msg = str(ex)
    low = msg.lower()
    if "trust" in low or "ssl" in low or "tls" in low or "certificate" in low:
        return (
            "{} failed (SSL/TLS):\n{}\n\n"
            "Use https://api.uniqube3d.co - not the raw *.elb.amazonaws.com host "
            "(certificate is issued for *.uniqube3d.co)."
        ).format(context, msg)
    err = ""
    if ex.Response:
        try:
            err = _read_response(ex.Response)
        except Exception:
            err = ""
    return "{} failed: {}".format(context, err or msg)


def login(api_url, email, password):
    """POST /api/auth/login -> access token string."""
    _ensure_tls()
    url = api_url.rstrip("/") + "/api/auth/login"
    req = HttpWebRequest.Create(url)
    req.Method = "POST"
    req.ContentType = "application/json"
    req.Timeout = 30000
    req.ReadWriteTimeout = 30000
    body = Encoding.UTF8.GetBytes(
        _json_field({"email": email, "password": password})
    )
    req.ContentLength = body.Length
    stream = req.GetRequestStream()
    stream.Write(body, 0, body.Length)
    stream.Close()
    try:
        resp = req.GetResponse()
    except WebException as ex:
        raise Exception(_format_web_error(ex, "Login"))
    text = _read_response(resp)
    resp.Close()
    if not text or not str(text).strip():
        raise Exception("Login failed: empty response from {}".format(url))
    try:
        data = _json_parse(text)
    except Exception as ex:
        raise Exception(
            "Login failed: invalid JSON from {} ({})\n{}".format(
                url, ex, str(text)[:300]
            )
        )
    token = data.get("token") or data.get("accessToken") or (data.get("data") or {}).get("token")
    if not token:
        if isinstance(data.get("user"), dict) and data.get("accessToken"):
            token = data["accessToken"]
        if not token:
            raise Exception("Login response missing token: {}".format(text[:300]))
    return token


def project_exists(api_url, token, project_id):
    """Return True if GET /api/projects/:id succeeds."""
    _ensure_tls()
    url = api_url.rstrip("/") + "/api/projects/" + str(int(project_id))
    req = HttpWebRequest.Create(url)
    req.Method = "GET"
    req.Headers.Add("Authorization", "Bearer {}".format(token))
    req.Timeout = 30000
    try:
        resp = req.GetResponse()
        _read_response(resp)
        resp.Close()
        return True
    except WebException as ex:
        code = 0
        try:
            if ex.Response:
                code = int(ex.Response.StatusCode)
                _read_response(ex.Response)
        except Exception:
            pass
        if code == 404:
            return False
        raise Exception("Could not verify project {}: {}".format(project_id, ex))


def list_projects(api_url, token, limit=100):
    """
    GET /api/projects?limit=N
    Returns list of {id, name, displayNumber, ...} dicts.
    """
    _ensure_tls()
    url = api_url.rstrip("/") + "/api/projects?limit={}".format(int(limit))
    req = HttpWebRequest.Create(url)
    req.Method = "GET"
    req.Headers.Add("Authorization", "Bearer {}".format(token))
    req.Timeout = 60000
    try:
        resp = req.GetResponse()
    except WebException as ex:
        raise Exception(_format_web_error(ex, "List projects"))
    text = _read_response(resp)
    resp.Close()
    data = _json_parse(text)
    projects = data.get("projects") or data.get("data") or []
    if not isinstance(projects, list):
        raise Exception("Unexpected projects response: {}".format(text[:300]))
    return projects


def publish_files(
    api_url,
    token,
    file_paths,
    categories,
    project_id=None,
    project_name=None,
    browser_snapshot=None,
    drawing_manifest=None,
    drawing_files=None,
    color_maps=None,
    bimsf_maps=None,
):
    """
    Multipart POST /api/revit/publish.
    file_paths: list of absolute IFC paths
    categories: list of category strings
    browser_snapshot: dict (JSON-serializable Project Browser tree)
    drawing_manifest: list of drawing metadata dicts
    drawing_files: list of {field, path, filename} for DXF/DWG parts
      e.g. {"field": "drawingDxf0", "path": "...", "filename": "x.dxf"}
    color_maps: list of per-file color map dicts (same order as file_paths),
      or a single dict. Preserves Revit system/material colours in Uniqube.
    bimsf_maps: list of per-file BIMSF_Container maps (panel ids) for tree select.
    """
    url = api_url.rstrip("/") + "/api/revit/publish"
    boundary = "----UniqubeBoundary{}".format(int(time.time() * 1000))
    _ensure_tls()
    req = HttpWebRequest.Create(url)
    req.Method = "POST"
    req.ContentType = "multipart/form-data; boundary={}".format(boundary)
    req.Headers.Add("Authorization", "Bearer {}".format(token))
    req.Timeout = 1000 * 60 * 30
    req.ReadWriteTimeout = 1000 * 60 * 30

    stream = req.GetRequestStream()
    encoding = Encoding.UTF8

    def write_str(s):
        if as_unicode is not None:
            s = as_unicode(s)
        bytes_ = encoding.GetBytes(s)
        stream.Write(bytes_, 0, bytes_.Length)

    def write_field(name, value):
        write_str("--{}\r\n".format(boundary))
        write_str('Content-Disposition: form-data; name="{}"\r\n\r\n'.format(name))
        write_str("{}\r\n".format(value))

    def write_file(field_name, path, filename=None):
        fname = filename or path.replace("\\", "/").split("/")[-1]
        write_str("--{}\r\n".format(boundary))
        write_str(
            'Content-Disposition: form-data; name="{}"; filename="{}"\r\n'.format(
                field_name, fname
            )
        )
        write_str("Content-Type: application/octet-stream\r\n\r\n")
        file_bytes = File.ReadAllBytes(as_net_string(path) if as_net_string else path)
        stream.Write(file_bytes, 0, file_bytes.Length)
        write_str("\r\n")

    if project_id is not None:
        write_field("projectId", str(int(project_id)))
    if project_name:
        write_field("projectName", project_name)
    write_field("categories", _json_field(list(categories)))

    if browser_snapshot is not None:
        write_field("browserSnapshot", _json_field(browser_snapshot))
    if color_maps is not None:
        write_field("colorMaps", _json_field(color_maps))
    if bimsf_maps is not None:
        write_field("bimsfMaps", _json_field(bimsf_maps))
    if drawing_manifest is not None:
        # Paths are local - strip absolute paths before send; keep filenames/keys
        slim = []
        for m in drawing_manifest:
            slim.append({
                "key": m.get("key"),
                "revitElementId": m.get("revitElementId"),
                "kind": m.get("kind"),
                "name": m.get("name"),
                "sheetNumber": m.get("sheetNumber"),
                "viewType": m.get("viewType"),
                "dxfFileName": m.get("dxfFileName"),
                "dwgFileName": m.get("dwgFileName"),
            })
        write_field("drawingManifest", _json_field(slim))

    for i, path in enumerate(file_paths):
        write_file("file{}".format(i), path)

    if drawing_files:
        for df in drawing_files:
            write_file(df["field"], df["path"], df.get("filename"))

    write_str("--{}--\r\n".format(boundary))
    stream.Close()

    try:
        resp = req.GetResponse()
    except WebException as ex:
        err = ""
        if ex.Response:
            err = _read_response(ex.Response)
        raise Exception("Publish failed: {}".format(
            err or (exception_text(ex) if exception_text else str(ex))
        ))

    text = _read_response(resp)
    resp.Close()
    return _json_parse(text)


def wait_for_job(api_url, token, job_id, timeout_sec=3600, poll_sec=3):
    """Poll GET /api/revit/publish-status/:jobId until completed/failed."""
    _ensure_tls()
    url = api_url.rstrip("/") + "/api/revit/publish-status/" + str(job_id)
    deadline = time.time() + timeout_sec
    last = None
    while time.time() < deadline:
        req = HttpWebRequest.Create(url)
        req.Method = "GET"
        req.Headers.Add("Authorization", "Bearer {}".format(token))
        req.Timeout = 60000
        try:
            resp = req.GetResponse()
            text = _read_response(resp)
            resp.Close()
            last = _json_parse(text)
        except WebException as ex:
            err = ""
            if ex.Response:
                err = _read_response(ex.Response)
            raise Exception("Status poll failed: {}".format(err or str(ex)))

        status = last.get("status")
        if status in ("completed", "failed"):
            return last
        time.sleep(poll_sec)
    raise Exception("Publish timed out. Last status: {}".format(last))
