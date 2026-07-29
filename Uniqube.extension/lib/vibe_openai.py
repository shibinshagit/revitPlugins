# -*- coding: utf-8 -*-
"""OpenAI Chat Completions helper for Vibe Modeler (IronPython / .NET)."""
from __future__ import print_function

import json
import os

import clr

clr.AddReference("System")
clr.AddReference("System.Core")
clr.AddReference("System.Net")

import System
from System.IO import StreamReader
from System.Text import Encoding

_Net = System.Net
WebRequest = _Net.WebRequest

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"

_SECRETS_NAME = "vibe_secrets.json"
_DIR = os.path.dirname(__file__)

SYSTEM_PROMPT = (
    "You are Vibe Modeler, an AI modeling assistant inside Autodesk Revit "
    "for alubond.ai.\n"
    "You have tools that can read and modify the live Revit model — use them.\n"
    "Rules:\n"
    "1. Prefer tools over guessing about the model.\n"
    "2. For questions about the model/view/selection/levels/families, call tools first.\n"
    "3. For 'what is selected' questions, always call get_selection — it uses a cached "
    "selection because Revit clears highlights when the user types in the chat panel.\n"
    "4. To delete selected elements, call delete_elements with use_selection=true "
    "(after get_selection if unsure). Linked elements cannot be deleted.\n"
    "5. For destructive actions (close_document, mass deletes via code), confirm intent "
    "in your reply if the user was ambiguous; if they were clear, proceed.\n"
    "6. Prefer dedicated tools over execute_revit_code. Use execute_revit_code only "
    "when no other tool fits.\n"
    "7. Keep final answers concise and practical.\n"
    "8. After tool results, summarize what you found or changed.\n"
    "9. If get_selection returns source=cached, tell the user it reflects their last "
    "pick in the view before they clicked the chat box.\n"
    "10. After delete or other model edits, mention the user can click Undo below the chat."
)


def _enable_tls12():
    try:
        spm = _Net.ServicePointManager
        tls = _Net.SecurityProtocolType
        try:
            spm.SecurityProtocol = tls.Tls12
        except Exception:
            try:
                spm.SecurityProtocol = System.Enum.ToObject(tls, 3072)
            except Exception:
                pass
    except Exception:
        pass


def load_api_key():
    env = os.environ.get("OPENAI_API_KEY")
    if env:
        return env.strip()
    path = os.path.join(_DIR, _SECRETS_NAME)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        key = (data.get("openai_api_key") or data.get("api_key") or "").strip()
        return key or None
    except Exception:
        return None


def _http_post_json(payload, timeout_ms=120000):
    api_key = load_api_key()
    if not api_key:
        raise Exception(
            "No OpenAI API key found. Set OPENAI_API_KEY or create lib/vibe_secrets.json"
        )

    body = json.dumps(payload)
    body_bytes = Encoding.UTF8.GetBytes(body)

    _enable_tls12()
    req = WebRequest.Create(API_URL)
    req.Method = "POST"
    req.ContentType = "application/json"
    req.Accept = "application/json"
    req.Timeout = int(timeout_ms)
    try:
        req.ReadWriteTimeout = int(timeout_ms)
    except Exception:
        pass
    req.Headers.Add("Authorization", "Bearer " + api_key)
    req.ContentLength = body_bytes.Length

    try:
        stream = req.GetRequestStream()
        try:
            stream.Write(body_bytes, 0, body_bytes.Length)
        finally:
            stream.Close()
        resp = req.GetResponse()
        try:
            reader = StreamReader(resp.GetResponseStream(), Encoding.UTF8)
            try:
                raw = reader.ReadToEnd()
            finally:
                reader.Close()
        finally:
            resp.Close()
    except Exception as ex:
        detail = str(ex)
        try:
            if hasattr(ex, "Response") and ex.Response is not None:
                err_reader = StreamReader(ex.Response.GetResponseStream(), Encoding.UTF8)
                try:
                    detail = err_reader.ReadToEnd() or detail
                finally:
                    err_reader.Close()
                    try:
                        ex.Response.Close()
                    except Exception:
                        pass
        except Exception:
            pass
        raise Exception("OpenAI request failed: {}".format(detail))

    try:
        data = json.loads(raw)
    except Exception:
        raise Exception("Invalid JSON from OpenAI")

    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        raise Exception(msg or "OpenAI error")
    return data


def normalize_messages_for_api(messages):
    """Ensure OpenAI-compatible message shapes (tool args as JSON strings)."""
    out = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id"),
                    "content": m.get("content") if m.get("content") is not None else "",
                }
            )
            continue
        if role == "assistant":
            msg = {"role": "assistant", "content": m.get("content")}
            if m.get("tool_calls"):
                tcs = []
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments")
                    if args is not None and not isinstance(args, str):
                        args = json.dumps(args)
                    tcs.append(
                        {
                            "id": tc.get("id"),
                            "type": tc.get("type") or "function",
                            "function": {
                                "name": fn.get("name"),
                                "arguments": args if args is not None else "{}",
                            },
                        }
                    )
                msg["tool_calls"] = tcs
            out.append(msg)
            continue
        if role in ("system", "user") and m.get("content") is not None:
            out.append({"role": role, "content": m.get("content")})
    return out


def chat_completion(messages, model=None, tools=None, tool_choice="auto", timeout_ms=120000):
    """
    Call OpenAI chat completions.

    Returns assistant message dict:
      {role, content, tool_calls?}
    """
    api_messages = normalize_messages_for_api(messages)
    payload = {
        "model": model or DEFAULT_MODEL,
        "messages": api_messages,
        "temperature": 0.4,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

    data = _http_post_json(payload, timeout_ms=timeout_ms)
    try:
        msg = data["choices"][0]["message"]
    except Exception:
        raise Exception("Unexpected OpenAI response shape")

    # Normalize to plain dict
    out = {
        "role": msg.get("role") or "assistant",
        "content": msg.get("content"),
    }
    tool_calls = msg.get("tool_calls")
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def build_messages(history, user_text):
    """Build API messages from panel history + new user text."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for item in history or []:
        role = item.get("role")
        if role == "user" and item.get("content"):
            msgs.append({"role": "user", "content": item["content"]})
        elif role == "assistant":
            # Keep prior assistant text only (not prior tool machinery)
            if item.get("content"):
                msgs.append({"role": "assistant", "content": item["content"]})
    msgs.append({"role": "user", "content": user_text})
    return msgs


def assistant_text(message):
    if not message:
        return ""
    content = message.get("content")
    if content is None:
        return ""
    return content.strip() if isinstance(content, str) else str(content)
