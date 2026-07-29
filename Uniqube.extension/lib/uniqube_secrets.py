# -*- coding: utf-8 -*-
"""Load Uniqube / Vibe secrets from vibe_secrets.json next to this module."""
from __future__ import print_function

import json
import os

# Production Uniqube API. Must match TLS cert (*.uniqube3d.co) - do NOT use raw ALB DNS.
DEFAULT_LIVE_API = "https://api.uniqube3d.co"
DEFAULT_LIVE_FRONTEND = "https://uniqube3d.co"
DEFAULT_LOCAL_API = "http://127.0.0.1:4000"
DEFAULT_LOCAL_FRONTEND = "http://localhost:3000"


def secrets_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vibe_secrets.json")


def load_secrets():
    path = secrets_path()
    if not os.path.isfile(path):
        return {}
    # IronPython json fails on UTF-8 BOM (e.g. PowerShell Set-Content -Encoding UTF8).
    with open(path, "rb") as f:
        raw = f.read()
    bom = "\xef\xbb\xbf"
    if raw[:3] == bom or raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except Exception:
        text = str(raw)
    # Also strip Unicode BOM if file was read already-decoded somehow
    if text.startswith(u"\ufeff"):
        text = text.lstrip(u"\ufeff")
    text = text.strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception as ex:
        raise Exception(
            "Could not parse vibe_secrets.json ({}):\n{}".format(path, ex)
        )


def uniqube_config():
    """Return (api_url, email, password) - local/default API (backward compatible)."""
    api, email, password, _frontend = uniqube_config_for("local")
    return api, email, password


def uniqube_config_for(target):
    """Return (api_url, email, password, frontend_url) for local or live publish.

    target: 'local' | 'live'
    """
    s = load_secrets()
    email = s.get("uniqube_email") or ""
    password = s.get("uniqube_password") or ""
    t = (target or "local").strip().lower()

    if t == "live":
        api = (
            s.get("uniqube_api_url_live")
            or s.get("uniqube_live_api_url")
            or DEFAULT_LIVE_API
        ).rstrip("/")
        frontend = (
            s.get("uniqube_frontend_url_live")
            or s.get("uniqube_live_frontend_url")
            or DEFAULT_LIVE_FRONTEND
        ).rstrip("/")
    else:
        api = (
            s.get("uniqube_api_url_local")
            or s.get("uniqube_api_url")
            or DEFAULT_LOCAL_API
        ).rstrip("/")
        frontend = (
            s.get("uniqube_frontend_url_local")
            or s.get("uniqube_frontend_url")
            or DEFAULT_LOCAL_FRONTEND
        ).rstrip("/")

    return api, email, password, frontend
