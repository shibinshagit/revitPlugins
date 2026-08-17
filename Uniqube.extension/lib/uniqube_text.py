# -*- coding: utf-8 -*-
"""IronPython-safe text conversion for Revit / .NET strings.

Do NOT call sys.setdefaultencoding / reload(sys) here - that hangs pyRevit
output (blank PublishLive window).
"""
from __future__ import print_function

try:
    unicode
except NameError:
    unicode = str

try:
    unichr
except NameError:
    unichr = chr

try:
    long
except NameError:
    long = int

try:
    from System import String as NetString
    from System import Char, Array
except Exception:
    NetString = None
    Char = None
    Array = None


def as_unicode(val):
    """Python unicode from None / str / System.String / exception."""
    if val is None:
        return u""
    try:
        if isinstance(val, unicode):
            return val
    except Exception:
        pass

    if NetString is not None:
        try:
            if isinstance(val, NetString) or val.__class__.__name__ == "String":
                return u"".join(unichr(ord(c)) for c in val)
        except Exception:
            pass
        try:
            ts = val.ToString()
            if ts is not None:
                return u"".join(unichr(ord(c)) for c in ts)
        except Exception:
            pass

    if isinstance(val, str):
        for enc in ("utf-8", "cp1252", "latin-1"):
            try:
                return val.decode(enc)
            except Exception:
                continue
        try:
            return u"".join(unichr(ord(c) & 0xFF) for c in val)
        except Exception:
            return u""

    try:
        return u"".join(unichr(ord(c)) for c in val)
    except Exception:
        pass
    return u""


def ascii_message(val):
    """Plain ASCII str safe for TaskDialog / forms.alert / logger."""
    text = as_unicode(val)
    out = []
    for c in text:
        o = ord(c)
        if o in (9, 10, 13) or 32 <= o <= 126:
            out.append(c)
        else:
            out.append(u"?")
    s = u"".join(out)
    try:
        return str(s)
    except Exception:
        return "Error"


def as_ascii_name(val, fallback="model"):
    """ASCII filename safe to pass into Revit Export / os.path."""
    text = as_unicode(val)
    out = []
    for c in text:
        o = ord(c)
        if o < 32 or o > 126 or c in u'<>:"/\\|?*':
            out.append(u"_")
        else:
            out.append(c)
    s = u"".join(out).strip(u" ._")
    while u"__" in s:
        s = s.replace(u"__", u"_")
    if not s:
        s = fallback
    try:
        return str(s)
    except Exception:
        return str(fallback)


def as_net_string(val):
    """System.String via UTF-16 Char array (avoids IronPython 'unknown' codec)."""
    text = as_unicode(val)
    if NetString is None or Char is None or Array is None:
        return ascii_message(val)
    try:
        n = len(text)
        arr = Array.CreateInstance(Char, n)
        i = 0
        while i < n:
            arr[i] = Char(ord(text[i]))
            i += 1
        return NetString(arr)
    except Exception:
        try:
            return NetString(ascii_message(val))
        except Exception:
            return ascii_message(val)


_JSON_ESC = {
    u'"': u'\\"',
    u"\\": u"\\\\",
    u"\n": u"\\n",
    u"\r": u"\\r",
    u"\t": u"\\t",
    u"\b": u"\\b",
    u"\f": u"\\f",
}


def _json_escape_char(codepoint, out):
    if codepoint > 0xFFFF:
        codepoint -= 0x10000
        out.append(u"\\u%04x" % (0xD800 + (codepoint >> 10)))
        out.append(u"\\u%04x" % (0xDC00 + (codepoint & 0x3FF)))
    else:
        out.append(u"\\u%04x" % codepoint)


def _json_string(val, out):
    out.append(u'"')
    for c in as_unicode(val):
        esc = _JSON_ESC.get(c)
        if esc is not None:
            out.append(esc)
            continue
        o = ord(c)
        if 32 <= o <= 126:
            out.append(c)
        else:
            _json_escape_char(o, out)
    out.append(u'"')


def _json_write(obj, out, depth):
    if depth > 40 or obj is None:
        out.append(u"null")
        return
    if isinstance(obj, bool):
        out.append(u"true" if obj else u"false")
        return
    if isinstance(obj, (int, long)):
        out.append(as_unicode(str(obj)))
        return
    if isinstance(obj, float):
        text = repr(obj)
        # NaN / Infinity are not valid JSON
        out.append(as_unicode(text) if text[-1:].isdigit() else u"null")
        return
    if isinstance(obj, dict):
        out.append(u"{")
        first = True
        for key, value in obj.items():
            if not first:
                out.append(u",")
            first = False
            _json_string(key if isinstance(key, (str, unicode)) else str(key), out)
            out.append(u":")
            _json_write(value, out, depth + 1)
        out.append(u"}")
        return
    if isinstance(obj, (list, tuple, set)):
        out.append(u"[")
        first = True
        for value in obj:
            if not first:
                out.append(u",")
            first = False
            _json_write(value, out, depth + 1)
        out.append(u"]")
        return
    _json_string(obj, out)


def safe_json(obj):
    """ASCII JSON for any Revit-derived structure, without the stdlib encoder.

    json/encoder.py runs s.decode('utf-8') on every string holding a character
    above 0x7f. Under IronPython str IS unicode, so Revit names such as U+00E1
    reach that decode and blow up with the 'unknown' code-page codec. Emitting
    the \\uXXXX escapes here keeps stdlib json out of the path entirely.
    """
    out = []
    _json_write(obj, out, 0)
    return str(u"".join(out))


def json_ascii_text(text):
    """Make a JSON document pure ASCII so json.loads never decodes bytes.

    Replacing a character inside a JSON string literal with its \\uXXXX escape
    produces an equivalent document, and JSON carries no non-ASCII outside
    string literals.
    """
    out = []
    for c in as_unicode(text):
        o = ord(c)
        if o <= 126:
            out.append(c)
        else:
            _json_escape_char(o, out)
    return str(u"".join(out))


def safe_traceback():
    """ASCII traceback of the exception being handled ('' when unavailable)."""
    try:
        import traceback

        return ascii_message(traceback.format_exc())
    except Exception:
        return ""


def exception_text(ex):
    """Safe ASCII message from a Python or .NET exception."""
    if ex is None:
        return "Unknown error"
    parts = []
    try:
        msg = getattr(ex, "Message", None)
        if msg:
            parts.append(as_unicode(msg))
    except Exception:
        pass
    try:
        inner = getattr(ex, "InnerException", None)
        if inner is not None:
            im = getattr(inner, "Message", None)
            if im:
                parts.append(as_unicode(im))
    except Exception:
        pass
    if not parts:
        try:
            parts.append(as_unicode(ex))
        except Exception:
            parts.append(u"Unknown error")
    return ascii_message(u" | ".join(parts))
