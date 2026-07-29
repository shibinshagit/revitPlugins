# -*- coding: utf-8 -*-
"""Dockable Vibe Modeler sidebar — theme-aware UI + tool-calling agent.

Crash-safety:
- HTTP on background Thread only
- Revit API via ExternalEvent only
- WPF updates on DispatcherTimer (UI thread) only
"""
from __future__ import print_function

import os

from pyrevit import forms
import Autodesk.Revit.UI as UI

from System import TimeSpan, Uri, UriKind
from System.Threading import Thread, ThreadStart
from System.Windows import (
    Thickness,
    TextWrapping,
    HorizontalAlignment,
    CornerRadius,
    FontWeights,
    Visibility,
)
from System.Windows.Controls import TextBlock, Border, StackPanel
from System.Windows.Media import SolidColorBrush, Color, Brushes
from System.Windows.Media.Imaging import BitmapImage, BitmapCacheOption
from System.Windows.Threading import DispatcherTimer

_DIR = os.path.dirname(__file__)
_XAML = os.path.join(_DIR, "ui", "VibePanel.xaml")
_LOGO_LIGHT = os.path.join(_DIR, "ui", "logo.png")
_LOGO_DARK = os.path.join(_DIR, "ui", "logo.dark.png")

_MAX_TOOL_ROUNDS = 8

_state = None
try:
    _state = UI.DockablePaneState()
    _state.DockPosition = UI.DockPosition.Right
except Exception:
    _state = None

_ACCENT = Color.FromRgb(0x5B, 0x6C, 0xFF)

_THEMES = {
    "light": {
        "page": Color.FromRgb(0xF4, 0xF5, 0xF7),
        "surface": Color.FromRgb(0xFF, 0xFF, 0xFF),
        "border": Color.FromRgb(0xE2, 0xE5, 0xEB),
        "text": Color.FromRgb(0x1A, 0x1D, 0x23),
        "muted": Color.FromRgb(0x6B, 0x72, 0x80),
        "user_bg": _ACCENT,
        "user_fg": Color.FromRgb(0xFF, 0xFF, 0xFF),
        "bot_bg": Color.FromRgb(0xEE, 0xF0, 0xF4),
        "bot_fg": Color.FromRgb(0x1A, 0x1D, 0x23),
        "sys_bg": Color.FromRgb(0xE8, 0xEC, 0xFF),
        "sys_fg": Color.FromRgb(0x3D, 0x4A, 0xB8),
        "input_fg": Color.FromRgb(0x1A, 0x1D, 0x23),
        "undo_bg": Color.FromRgb(0xFF, 0xF8, 0xE8),
        "undo_border": Color.FromRgb(0xF0, 0xD7, 0x8C),
        "undo_text": Color.FromRgb(0x7A, 0x5C, 0x00),
    },
    "dark": {
        "page": Color.FromRgb(0x1C, 0x1F, 0x26),
        "surface": Color.FromRgb(0x25, 0x2A, 0x33),
        "border": Color.FromRgb(0x3A, 0x41, 0x50),
        "text": Color.FromRgb(0xE8, 0xEA, 0xED),
        "muted": Color.FromRgb(0x9A, 0xA0, 0xA6),
        "user_bg": _ACCENT,
        "user_fg": Color.FromRgb(0xFF, 0xFF, 0xFF),
        "bot_bg": Color.FromRgb(0x32, 0x38, 0x44),
        "bot_fg": Color.FromRgb(0xE8, 0xEA, 0xED),
        "sys_bg": Color.FromRgb(0x2A, 0x30, 0x4A),
        "sys_fg": Color.FromRgb(0xB4, 0xBC, 0xFF),
        "input_fg": Color.FromRgb(0xE8, 0xEA, 0xED),
        "undo_bg": Color.FromRgb(0x3A, 0x32, 0x1A),
        "undo_border": Color.FromRgb(0x6B, 0x5A, 0x2E),
        "undo_text": Color.FromRgb(0xFF, 0xD8, 0x6B),
    },
}


def _brush(color):
    return SolidColorBrush(color)


def _is_dark_theme():
    try:
        return UI.UIThemeManager.CurrentTheme == UI.UITheme.Dark
    except Exception:
        return False


def _import_openai():
    import vibe_openai

    return vibe_openai


def _import_schemas():
    import vibe_tool_schemas

    return vibe_tool_schemas


def _import_tools():
    import vibe_tools

    return vibe_tools


def _load_model_name():
    path = os.path.join(_DIR, "vibe_secrets.json")
    default = "gpt-4o-mini"
    if not os.path.isfile(path):
        return default
    try:
        import json

        with open(path, "r") as f:
            data = json.load(f)
        return (data.get("model") or default).strip()
    except Exception:
        return default


class VibeModelerPanel(forms.WPFPanel):
    """Native dockable sidebar for Vibe Modeler + GPT tool agent."""

    panel_title = "Vibe Modeler"
    panel_id = "a7c3e91f-4b2d-4e8a-9f1c-6d5e8b0a2c4f"
    panel_source = _XAML
    initial_state = _state

    def __init__(self):
        forms.WPFPanel.__init__(self)
        self._theme_key = None
        self._booted = False
        self._busy = False
        self._history = []
        self._model = _load_model_name()
        self._thinking_wrap = None

        # Agent state
        self._agent_msgs = None
        self._agent_user = None
        self._agent_round = 0
        self._phase = None  # 'openai' | 'tools'
        self._openai_result = None
        self._tool_wait_ticks = 0
        self._mutation_this_turn = False

        self._boot = DispatcherTimer()
        self._boot.Interval = TimeSpan.FromMilliseconds(300)
        self._boot.Tick += self._on_boot
        self._boot.Start()

        self._tick = DispatcherTimer()
        self._tick.Interval = TimeSpan.FromMilliseconds(200)
        self._tick.Tick += self._on_tick

    def _on_boot(self, sender, args):
        try:
            self._boot.Stop()
        except Exception:
            pass
        try:
            self._apply_theme(force=True)
            if not self._booted:
                self._booted = True
                tools_ok = False
                try:
                    vibe_tools = _import_tools()
                    h, e = vibe_tools.get_external_event()
                    tools_ok = e is not None
                except Exception:
                    tools_ok = False

                try:
                    vibe_openai = _import_openai()
                    key_ok = bool(vibe_openai.load_api_key())
                except Exception as ex:
                    self._add_system_message(
                        "OpenAI helper load issue: {}. Retry Send.".format(ex)
                    )
                    return

                if key_ok:
                    msg = "Vibe Modeler online ({}).".format(self._model)
                    if tools_ok:
                        msg += " Model tools ready — ask about this project."
                    else:
                        msg += " Click Open Vibe once more after reload if tools fail."
                    self._add_system_message(msg)
                else:
                    self._add_system_message(
                        "No API key found. Add lib/vibe_secrets.json or set OPENAI_API_KEY."
                    )
        except Exception as ex:
            try:
                self._add_system_message("Panel boot warning: {}".format(ex))
            except Exception:
                pass

    def _load_logo(self, dark=False):
        if not hasattr(self, "BrandLogo") or self.BrandLogo is None:
            return
        path = _LOGO_DARK if dark else _LOGO_LIGHT
        if not os.path.isfile(path):
            path = _LOGO_LIGHT
        if not os.path.isfile(path):
            return
        try:
            bmp = BitmapImage()
            bmp.BeginInit()
            bmp.UriSource = Uri(path, UriKind.Absolute)
            bmp.CacheOption = BitmapCacheOption.OnLoad
            bmp.EndInit()
            try:
                bmp.Freeze()
            except Exception:
                pass
            self.BrandLogo.Source = bmp
        except Exception:
            pass

    def _apply_theme(self, force=False):
        try:
            key = "dark" if _is_dark_theme() else "light"
            if not force and key == self._theme_key:
                return
            self._theme_key = key
            t = _THEMES[key]
            self._load_logo(dark=(key == "dark"))
            try:
                self.Background = _brush(t["page"])
            except Exception:
                pass
            self._set_bg("ChatSurface", t["surface"])
            self._set_border("ChatSurface", t["border"])
            self._set_bg("ComposerBar", t["surface"])
            self._set_border("ComposerBar", t["border"])
            if hasattr(self, "TitleText") and self.TitleText is not None:
                self.TitleText.Foreground = _brush(t["text"])
            if hasattr(self, "SubtitleText") and self.SubtitleText is not None:
                self.SubtitleText.Foreground = _brush(t["muted"])
            if hasattr(self, "PromptInput") and self.PromptInput is not None:
                self.PromptInput.Foreground = _brush(t["input_fg"])
                try:
                    self.PromptInput.CaretBrush = _brush(t["input_fg"])
                except Exception:
                    pass
                try:
                    self.PromptInput.Background = Brushes.Transparent
                except Exception:
                    pass
            self._set_bg("UndoBar", t.get("undo_bg", t["surface"]))
            self._set_border("UndoBar", t.get("undo_border", t["border"]))
            if hasattr(self, "UndoHintText") and self.UndoHintText is not None:
                self.UndoHintText.Foreground = _brush(
                    t.get("undo_text", t["muted"])
                )
        except Exception:
            pass

    def _show_undo_bar(self):
        try:
            if hasattr(self, "UndoBar") and self.UndoBar is not None:
                self.UndoBar.Visibility = Visibility.Visible
        except Exception:
            pass

    def _hide_undo_bar(self):
        try:
            if hasattr(self, "UndoBar") and self.UndoBar is not None:
                self.UndoBar.Visibility = Visibility.Collapsed
        except Exception:
            pass

    def _note_tool_mutation(self, tool_results):
        import json

        for tr in tool_results or []:
            try:
                data = json.loads(tr.get("content") or "{}")
                if not data.get("error") and data.get("mutated_model"):
                    self._mutation_this_turn = True
                    return
            except Exception:
                pass

    def _set_bg(self, name, color):
        el = getattr(self, name, None)
        if el is not None:
            el.Background = _brush(color)

    def _set_border(self, name, color):
        el = getattr(self, name, None)
        if el is not None:
            el.BorderBrush = _brush(color)

    def _set_busy(self, busy):
        self._busy = busy
        try:
            if hasattr(self, "SendButton") and self.SendButton is not None:
                self.SendButton.IsEnabled = not busy
            if hasattr(self, "PromptInput") and self.PromptInput is not None:
                self.PromptInput.IsEnabled = not busy
        except Exception:
            pass

    def prompt_keydown(self, sender, args):
        try:
            if args.Key.ToString() == "Return" and not args.KeyboardDevice.Modifiers:
                args.Handled = True
                self._send()
        except Exception:
            pass

    def send_click(self, sender, args):
        self._send()

    def undo_click(self, sender, args):
        try:
            if self._busy:
                return
            if not self._ensure_tool_bridge():
                self._add_system_message("Tool bridge not ready. Click Open Vibe first.")
                return
            self._set_busy(True)
            self._show_status("Undoing...")
            vibe_tools = _import_tools()
            vibe_tools.raise_undo()
            self._phase = "undo"
            self._tool_wait_ticks = 0
            self._tick.Start()
        except Exception as ex:
            self._finish_error("Undo failed: {}".format(ex))

    def _ensure_tool_bridge(self):
        try:
            vibe_tools = _import_tools()
            _handler, event = vibe_tools.get_external_event()
            if event is not None:
                return True
            try:
                from pyrevit import HOST_APP

                if HOST_APP and getattr(HOST_APP, "uiapp", None):
                    vibe_tools.ensure_external_event()
            except Exception:
                pass
            _handler, event = vibe_tools.get_external_event()
            return event is not None
        except Exception:
            return False

    def _send(self):
        try:
            if self._busy:
                return
            if not hasattr(self, "PromptInput") or self.PromptInput is None:
                return

            text = (self.PromptInput.Text or "").strip()
            if not text:
                return

            if not self._ensure_tool_bridge():
                self._add_system_message(
                    "Tip: click Open Vibe on the ribbon once so model tools can run."
                )

            self._hide_undo_bar()
            self._mutation_this_turn = False

            self._add_user_message(text)
            self.PromptInput.Text = ""
            self._scroll_to_end()
            self._set_busy(True)
            self._show_status("Thinking...")
            self._tool_wait_ticks = 0

            vibe_openai = _import_openai()
            self._agent_user = text
            self._agent_msgs = vibe_openai.build_messages(list(self._history), text)
            self._agent_round = 0
            self._phase = "openai"
            self._openai_result = None
            self._start_openai_round()
            self._tick.Start()
        except Exception as ex:
            self._finish_error("Send failed: {}".format(ex))

    def _start_openai_round(self):
        self._openai_result = None
        msgs = list(self._agent_msgs)
        model = self._model

        def run():
            try:
                vibe_openai = _import_openai()
                schemas = _import_schemas()
                message = vibe_openai.chat_completion(
                    msgs,
                    model=model,
                    tools=schemas.TOOL_SCHEMAS,
                    tool_choice="auto",
                )
                self._openai_result = {"ok": True, "message": message}
            except Exception as ex:
                self._openai_result = {"ok": False, "error": str(ex)}

        thread = Thread(ThreadStart(run))
        thread.IsBackground = True
        thread.Start()

    def _on_tick(self, sender, args):
        try:
            if self._phase == "openai":
                self._poll_openai()
            elif self._phase == "tools":
                self._poll_tools()
            elif self._phase == "undo":
                self._poll_undo()
        except Exception as ex:
            self._finish_error("Agent tick failed: {}".format(ex))

    def _poll_openai(self):
        result = self._openai_result
        if result is None:
            return
        self._openai_result = None

        if not result.get("ok"):
            self._finish_error(result.get("error") or "OpenAI error")
            return

        message = result.get("message") or {}
        tool_calls = message.get("tool_calls")

        # Append assistant message (may include tool_calls)
        self._agent_msgs.append(message)

        if tool_calls:
            self._agent_round += 1
            if self._agent_round > _MAX_TOOL_ROUNDS:
                self._finish_error("Stopped: too many tool rounds.")
                return

            names = []
            for tc in tool_calls:
                fn = tc.get("function") or {}
                names.append(fn.get("name") or "?")
            self._show_status("Running tools: {}".format(", ".join(names)))

            try:
                if not self._ensure_tool_bridge():
                    self._finish_error(
                        "Revit tool bridge not ready. Click Open Vibe on the ribbon, then retry."
                    )
                    return
                vibe_tools = _import_tools()
                vibe_tools.raise_tools(tool_calls)
                self._phase = "tools"
                self._tool_wait_ticks = 0
            except Exception as ex:
                self._finish_error("Could not run tools: {}".format(ex))
            return

        # Final text answer
        vibe_openai = _import_openai()
        reply = vibe_openai.assistant_text(message) or "(No text reply)"
        self._finish_success(reply)

    def _poll_tools(self):
        try:
            vibe_tools = _import_tools()
            handler, _event = vibe_tools.get_external_event()
        except Exception as ex:
            self._finish_error("Tool bridge missing: {}".format(ex))
            return

        if handler is None:
            self._finish_error("Tool bridge missing. Click Open Vibe on the ribbon.")
            return

        if not handler.done:
            self._tool_wait_ticks += 1
            if self._tool_wait_ticks > 450:  # ~90s at 200ms
                self._finish_error("Tool execution timed out.")
            return

        if handler.error and not handler.results:
            self._finish_error("Tool execution failed: {}".format(handler.error))
            return

        for tr in handler.results or []:
            # OpenAI tool message format
            self._agent_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": tr.get("tool_call_id"),
                    "content": tr.get("content") or "",
                }
            )

        self._note_tool_mutation(handler.results)

        handler.done = False
        self._show_status("Thinking...")
        self._phase = "openai"
        self._start_openai_round()

    def _poll_undo(self):
        try:
            vibe_tools = _import_tools()
            handler, _event = vibe_tools.get_external_event()
        except Exception as ex:
            self._finish_error("Undo bridge missing: {}".format(ex))
            return

        if handler is None or not handler.done:
            self._tool_wait_ticks += 1
            if self._tool_wait_ticks > 150:
                self._finish_error("Undo timed out.")
            return

        import json

        ok = False
        err = None
        for tr in handler.results or []:
            try:
                data = json.loads(tr.get("content") or "{}")
                if data.get("undone"):
                    ok = True
                if data.get("error"):
                    err = data.get("error")
            except Exception:
                pass

        handler.done = False
        try:
            self._tick.Stop()
        except Exception:
            pass
        self._phase = None
        self._hide_status()
        self._set_busy(False)
        self._hide_undo_bar()

        if ok:
            self._add_system_message("Undone — last model change reverted.")
        else:
            self._add_system_message("Undo failed: {}".format(err or "unknown"))
        self._scroll_to_end()

    def _finish_success(self, reply):
        try:
            self._tick.Stop()
        except Exception:
            pass
        self._phase = None
        self._hide_status()
        self._set_busy(False)

        user = self._agent_user or ""
        self._history.append({"role": "user", "content": user})
        self._history.append({"role": "assistant", "content": reply})
        if len(self._history) > 24:
            self._history = self._history[-24:]
        self._add_assistant_message(reply)
        self._scroll_to_end()
        if self._mutation_this_turn:
            self._show_undo_bar()
        self._agent_msgs = None
        self._agent_user = None

    def _finish_error(self, message):
        try:
            self._tick.Stop()
        except Exception:
            pass
        self._phase = None
        self._hide_status()
        self._set_busy(False)
        self._add_system_message("Error: {}".format(message))
        self._scroll_to_end()
        self._agent_msgs = None
        self._agent_user = None

    def _show_status(self, text):
        self._hide_status()
        self._thinking_wrap = self._add_bubble(text, kind="sys", return_wrap=True)
        self._scroll_to_end()

    def _hide_status(self):
        if self._thinking_wrap is not None and hasattr(self, "MessageList"):
            try:
                self.MessageList.Children.Remove(self._thinking_wrap)
            except Exception:
                pass
        self._thinking_wrap = None

    def _add_user_message(self, text):
        self._add_bubble(text, kind="user")

    def _add_assistant_message(self, text):
        self._add_bubble(text, kind="bot")

    def _add_system_message(self, text):
        self._add_bubble(text, kind="sys")

    def _add_bubble(self, text, kind="bot", return_wrap=False):
        if not hasattr(self, "MessageList") or self.MessageList is None:
            return None
        try:
            t = _THEMES[self._theme_key or ("dark" if _is_dark_theme() else "light")]
            if kind == "user":
                bg, fg = t["user_bg"], t["user_fg"]
                align = HorizontalAlignment.Right
                label_prefix = None
            elif kind == "sys":
                bg, fg = t["sys_bg"], t["sys_fg"]
                align = HorizontalAlignment.Left
                label_prefix = "Vibe"
            else:
                bg, fg = t["bot_bg"], t["bot_fg"]
                align = HorizontalAlignment.Left
                label_prefix = "Vibe"

            stack = StackPanel()
            if label_prefix and kind != "user":
                meta = TextBlock()
                meta.Text = label_prefix
                meta.FontSize = 10
                meta.FontWeight = FontWeights.SemiBold
                meta.Foreground = _brush(t["muted"])
                meta.Margin = Thickness(4, 0, 4, 4)
                stack.Children.Add(meta)

            label = TextBlock()
            label.Text = text if text is not None else ""
            label.TextWrapping = TextWrapping.Wrap
            label.Foreground = _brush(fg)
            label.FontSize = 12.5
            label.Margin = Thickness(12, 8, 12, 8)

            bubble = Border()
            bubble.Background = _brush(bg)
            bubble.CornerRadius = CornerRadius(10)
            bubble.Child = label
            stack.Children.Add(bubble)

            wrap = Border()
            wrap.Margin = Thickness(0, 0, 0, 10)
            wrap.HorizontalAlignment = align
            wrap.MaxWidth = 300
            wrap.Child = stack
            self.MessageList.Children.Add(wrap)
            if return_wrap:
                return wrap
            return None
        except Exception:
            return None

    def _scroll_to_end(self):
        try:
            if hasattr(self, "MessageScroll") and self.MessageScroll is not None:
                self.MessageScroll.ScrollToEnd()
        except Exception:
            pass
