# -*- coding: utf-8 -*-
"""OpenAI tool schemas only — safe to import from any thread (no Revit API)."""

_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_revit_status",
            "description": "Check that Vibe tools can reach the active Revit session.",
            "parameters": _EMPTY,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_revit_model_info",
            "description": "Get comprehensive info about the active Revit model.",
            "parameters": _EMPTY,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_view_info",
            "description": "Get details about the currently active view.",
            "parameters": _EMPTY,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_view_elements",
            "description": "List elements visible in the active view with category counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer"},
                    "include_levels": {"type": "boolean"},
                    "include_location": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_selection",
            "description": "Get currently selected elements (uses cached selection when chat has focus).",
            "parameters": _EMPTY,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_elements",
            "description": (
                "Delete host-model elements. Uses current/cached selection when "
                "use_selection is true. Cannot delete linked-model elements."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "use_selection": {
                        "type": "boolean",
                        "description": "Delete cached/current selection (default true).",
                    },
                    "element_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Optional explicit host element ids.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_levels",
            "description": "List all levels in the model with elevation.",
            "parameters": _EMPTY,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_revit_views",
            "description": "List usable views in the model.",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_families",
            "description": "List family types; optional contains filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contains": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_family_categories",
            "description": "List family categories present in the model.",
            "parameters": _EMPTY,
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_category_parameters",
            "description": "List parameters on elements of a category (e.g. Walls).",
            "parameters": {
                "type": "object",
                "properties": {"category_name": {"type": "string"}},
                "required": ["category_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "color_splash",
            "description": "Color elements in a category by a parameter value.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category_name": {"type": "string"},
                    "parameter_name": {"type": "string"},
                    "use_gradient": {"type": "boolean"},
                },
                "required": ["category_name", "parameter_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clear_colors",
            "description": "Clear graphic overrides for a category in the active view.",
            "parameters": {
                "type": "object",
                "properties": {"category_name": {"type": "string"}},
                "required": ["category_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "place_family",
            "description": "Place a family instance at x,y,z (Revit internal feet).",
            "parameters": {
                "type": "object",
                "properties": {
                    "family_name": {"type": "string"},
                    "type_name": {"type": "string"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "z": {"type": "number"},
                    "level_name": {"type": "string"},
                    "rotation": {"type": "number"},
                },
                "required": ["family_name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_document",
            "description": "Save the active document, or Save As if file_path is provided.",
            "parameters": {
                "type": "object",
                "properties": {"file_path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sync_with_central",
            "description": "Synchronize workshared model with central.",
            "parameters": {
                "type": "object",
                "properties": {
                    "comment": {"type": "string"},
                    "compact": {"type": "boolean"},
                    "relinquish_all": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_document",
            "description": "Open a .rvt/.rfa/.rte file in this Revit session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "detach": {"type": "boolean"},
                    "audit": {"type": "boolean"},
                },
                "required": ["file_path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_document",
            "description": "Close the active document.",
            "parameters": {
                "type": "object",
                "properties": {"save": {"type": "boolean"}},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "execute_revit_code",
            "description": (
                "Execute IronPython in Revit. Globals: doc, uidoc, DB, print. "
                "Wrap model changes in Transaction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["code"],
                "additionalProperties": False,
            },
        },
    },
]
