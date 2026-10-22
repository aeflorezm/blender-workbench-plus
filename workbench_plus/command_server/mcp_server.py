# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
MCP server for the Workbench Plus Command Server: lets AI assistants that speak the
`Model Context Protocol <https://modelcontextprotocol.io>`__ drive a running Blender.

Runs outside Blender, with any Python 3.10+ (Blender's own Python works), and talks MCP over stdio::

   python mcp_server.py --discovery <path to command_server.json>

The add-on preferences copy the exact command (*Command Server → Copy MCP Command*).
Blender does not need to be running when the MCP server starts: every tool call connects to the
Command Server described by the discovery file, so restarting Blender or the server is picked up.
"""

__all__ = (
    "Bridge",
    "main",
)

import argparse
import json
import os
import sys


def _client_module():
    try:
        from . import client
    except ImportError:
        # Run as a script: load `client.py` from next to this file (also with `python -I`, where the
        # directory of the script is not on `sys.path`).
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wbp_command_server_client", os.path.join(os.path.dirname(os.path.abspath(__file__)), "client.py"))
        client = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(client)
    return client


_client = _client_module()

SERVER_NAME = "blender-workbench-plus"
SERVER_VERSION = "1"

# MCP protocol versions this server speaks, newest first.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

DISCOVERY_ENV = "WBP_COMMAND_SERVER_DISCOVERY"

# JSON-RPC error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

# Objects listed by `scene_summary`; larger scenes are truncated (the count is reported).
SCENE_SUMMARY_OBJECTS_MAX = 500

_SCENE_SUMMARY_CODE = '''\
def _wbp_scene_summary():
    import json
    context = bpy.context
    scene = context.scene
    view_layer = context.view_layer
    active = view_layer.objects.active
    objects = list(scene.objects)
    return json.dumps({
        "file": bpy.data.filepath or None,
        "is_dirty": bpy.data.is_dirty,
        "scene": scene.name,
        "frame": scene.frame_current,
        "frame_range": [scene.frame_start, scene.frame_end],
        "mode": context.mode,
        "active_object": active.name if active else None,
        "object_count": len(objects),
        "objects": [{
            "name": ob.name,
            "type": ob.type,
            "data": ob.data.name if ob.data else None,
            "parent": ob.parent.name if ob.parent else None,
            "collections": [collection.name for collection in ob.users_collection],
            "location": [round(value, 4) for value in ob.location],
            "selected": ob.select_get(view_layer=view_layer),
            "visible": ob.visible_get(view_layer=view_layer),
        } for ob in objects[:{objects_max:d}]],
    })


try:
    print(_wbp_scene_summary())
finally:
    del _wbp_scene_summary
'''.replace("{objects_max:d}", str(SCENE_SUMMARY_OBJECTS_MAX))

_START_HINT = (
    "Start the Command Server in Blender (Preferences > Add-ons > Workbench Plus > Command Server > Start), "
    "or run Blender headless with: blender -b --python-expr \"import bpy; bpy.ops.wbp.command_server_serve()\""
)

_ROOT_SCHEMA = {
    "type": "string",
    "enum": ["data", "context"],
    "default": "data",
    "description": "Resolve the path from bpy.data (default) or bpy.context",
}


def _text(value):
    """Tool output as text (JSON)."""
    return json.dumps(value, indent=1, ensure_ascii=False)


def _tool_run_python(blender, arguments):
    result = blender.call("exec", code=arguments.get("code"))
    return result["stdout"] or "(no output)"


def _tool_evaluate(blender, arguments):
    return _text(blender.call("eval", expression=arguments.get("expression")))


def _tool_call_operator(blender, arguments):
    params = {"operator": arguments.get("operator"), "properties": arguments.get("properties", {})}
    if "execution_context" in arguments:
        params["execution_context"] = arguments["execution_context"]
    return _text(blender.call("call_operator", **params))


def _tool_get_property(blender, arguments):
    return _text(blender.call("get_property", path=arguments.get("path"), root=arguments.get("root", "data")))


def _tool_set_property(blender, arguments):
    if "value" not in arguments:
        raise _client.ServerError(INVALID_PARAMS, "Invalid params: missing 'value'")
    blender.call("set_property", path=arguments.get("path"), value=arguments["value"],
                 root=arguments.get("root", "data"))
    return "OK"


def _tool_batch(blender, arguments):
    params = {"calls": arguments.get("calls")}
    if "undo" in arguments:
        params["undo"] = arguments["undo"]
    return _text(blender.call("batch", **params))


def _tool_scene_summary(blender, _arguments):
    return blender.call("exec", code=_SCENE_SUMMARY_CODE)["stdout"].strip()


# `(name, title, description, input schema, read only, handler)`.
_TOOLS = (
    (
        "run_python",
        "Run Python in Blender",
        "Run Python code in the running Blender and return what it prints. `bpy` is imported; variables persist "
        "between calls. Changes made this way are not undoable on their own: prefer `call_operator` or `batch` "
        "for edits the user may want to undo. Use print() to return values.",
        {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python code (statements)"}},
            "required": ["code"],
        },
        False,
        _tool_run_python,
    ),
    (
        "evaluate",
        "Evaluate a Python expression",
        "Evaluate a Python expression in Blender and return its value as JSON (vectors and matrices as lists, "
        "data-blocks as {\"$type\": \"ID\", ...}). Shares variables with `run_python`.",
        {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "A Python expression, e.g. `[ob.name for ob in bpy.context.selected_objects]`",
                },
            },
            "required": ["expression"],
        },
        True,
        _tool_evaluate,
    ),
    (
        "call_operator",
        "Call a Blender operator",
        "Call an operator by its Python name (e.g. `mesh.primitive_cube_add`) with keyword properties. "
        "Pushes an undo step, like using it from the interface. Returns the operator result, "
        "e.g. {\"result\": [\"FINISHED\"]}.",
        {
            "type": "object",
            "properties": {
                "operator": {"type": "string", "description": "Operator idname as in bpy.ops, e.g. `object.delete`"},
                "properties": {
                    "type": "object",
                    "description": "Operator properties, e.g. {\"location\": [0, 0, 1]}",
                    "additionalProperties": True,
                },
                "execution_context": {
                    "type": "string",
                    "description": "Operator execution context (default `EXEC_DEFAULT`)",
                },
            },
            "required": ["operator"],
        },
        False,
        _tool_call_operator,
    ),
    (
        "get_property",
        "Read a property",
        "Read a value by its RNA path from bpy.data or bpy.context, e.g. `objects[\"Cube\"].location` or "
        "`scene.frame_current` (with root `context`).",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "RNA path, e.g. `objects[\"Cube\"].modifiers[0].levels`"},
                "root": _ROOT_SCHEMA,
            },
            "required": ["path"],
        },
        True,
        _tool_get_property,
    ),
    (
        "set_property",
        "Write a property",
        "Set a value by its RNA path from bpy.data or bpy.context, e.g. `objects[\"Cube\"].location` = [0, 0, 2]. "
        "Not undoable on its own: wrap it in `batch` to make it one undo step.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "RNA path of the property"},
                "value": {"description": "New value (JSON: numbers, strings, booleans, lists for vectors)"},
                "root": _ROOT_SCHEMA,
            },
            "required": ["path", "value"],
        },
        False,
        _tool_set_property,
    ),
    (
        "batch",
        "Run several calls as one undo step",
        "Run a list of calls in order, as a single undo step. Each call is {\"method\": ..., \"params\": {...}} "
        "with method `exec`, `eval`, `call_operator`, `get_property` or `set_property` and the same params as the "
        "tools (`exec` takes `code`, `eval` takes `expression`). Stops at the first failing call; the calls "
        "before it stay applied (and undoable together).",
        {
            "type": "object",
            "properties": {
                "calls": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"method": {"type": "string"}, "params": {"type": "object"}},
                        "required": ["method"],
                    },
                },
                "undo": {"type": "string", "description": "Name of the undo step"},
            },
            "required": ["calls"],
        },
        False,
        _tool_batch,
    ),
    (
        "scene_summary",
        "Summarize the scene",
        "Return the open file, current scene, frame, mode, active object and the objects of the scene "
        "(name, type, parent, collections, location, selection, visibility) as JSON. A good first call.",
        {"type": "object", "properties": {}},
        True,
        _tool_scene_summary,
    ),
)

_HANDLERS = {name: handler for name, _title, _description, _schema, _read_only, handler in _TOOLS}


def tools_list():
    """The tool descriptions returned by ``tools/list``."""
    return [
        {
            "name": name,
            "title": title,
            "description": description,
            "inputSchema": schema,
            "annotations": {"title": title, "readOnlyHint": read_only, "openWorldHint": False},
        }
        for name, title, description, schema, read_only, _handler in _TOOLS
    ]


class _RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class Bridge:
    """
    Handles MCP messages, forwarding tool calls to the Command Server described by ``discovery_filepath``.

    The connection is opened on the first tool call and checked (and reopened if needed) before each one.
    """

    def __init__(self, discovery_filepath, *, timeout=None):
        self.discovery_filepath = discovery_filepath
        self.timeout = timeout
        self._blender = None

    def close(self):
        if self._blender is not None:
            self._blender.close()
            self._blender = None

    def _connect(self):
        if self._blender is not None:
            try:
                self._blender.call("ping")
                return self._blender
            except (OSError, ValueError, _client.ServerError):
                # Blender was closed or the server restarted (new port and token): reconnect.
                self.close()
        if not self.discovery_filepath:
            raise ConnectionError(
                "No discovery file given: pass --discovery or set {:s}".format(DISCOVERY_ENV))
        if not os.path.exists(self.discovery_filepath):
            raise ConnectionError("The Command Server is not running (no {:s})".format(self.discovery_filepath))
        try:
            self._blender = _client.Client.from_discovery_file(self.discovery_filepath, timeout=self.timeout)
        except (OSError, ValueError, KeyError, _client.ServerError) as ex:
            raise ConnectionError("Could not connect to the Command Server: {:s}".format(str(ex)))
        return self._blender

    def tool_call(self, name, arguments):
        """Run tool ``name``, return the MCP ``CallToolResult``."""
        handler = _HANDLERS.get(name)
        if handler is None:
            raise _RequestError(INVALID_PARAMS, "Unknown tool: {!r}".format(name))
        if not isinstance(arguments, dict):
            raise _RequestError(INVALID_PARAMS, "Tool arguments must be an object")
        try:
            text = handler(self._connect(), arguments)
            is_error = False
        except ConnectionError as ex:
            text = "{:s}.\n{:s}".format(str(ex).rstrip("."), _START_HINT)
            is_error = True
        except _client.ServerError as ex:
            text = "Error {:d}: {:s}".format(ex.code, ex.message)
            if ex.traceback:
                text += "\n\n" + ex.traceback
            elif isinstance(ex.data, dict) and "error" in ex.data:
                # A failed batch: the error of the failing call.
                text += "\n\n" + _text(ex.data)
            is_error = True
        except OSError as ex:
            # The connection broke during the call: whether it ran is unknown.
            self.close()
            text = "Lost the connection to Blender during the call: {:s}".format(str(ex))
            is_error = True
        return {"content": [{"type": "text", "text": text}], "isError": is_error}

    # MCP methods.

    def _initialize(self, params):
        requested = params.get("protocolVersion")
        return {
            "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "title": "Blender (Workbench Plus)", "version": SERVER_VERSION},
            "instructions": (
                "Controls a running Blender through the Workbench Plus Command Server. Start with "
                "`scene_summary`. Prefer `call_operator` and `batch` for changes the user may want to undo."
            ),
        }

    def _tools_call(self, params):
        name = params.get("name")
        if not isinstance(name, str):
            raise _RequestError(INVALID_PARAMS, "Missing tool name")
        return self.tool_call(name, params.get("arguments") or {})

    def handle(self, message):
        """Handle one JSON-RPC message (already parsed), return the response or None for notifications."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(
                message.get("method"), str):
            if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
                # A response to a request of ours: this server sends none, ignore it.
                return None
            return _error_response(message.get("id") if isinstance(message, dict) else None,
                                   INVALID_REQUEST, "Invalid request")
        method = message["method"]
        params = message.get("params") or {}
        request_id = message.get("id")
        is_notification = "id" not in message
        try:
            if not isinstance(params, dict):
                raise _RequestError(INVALID_PARAMS, "Params must be an object")
            if method == "initialize":
                result = self._initialize(params)
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": tools_list()}
            elif method == "tools/call":
                result = self._tools_call(params)
            elif is_notification:
                # `notifications/initialized`, `notifications/cancelled`... nothing to do.
                return None
            else:
                raise _RequestError(METHOD_NOT_FOUND, "Method not found: {!r}".format(method))
        except _RequestError as ex:
            return None if is_notification else _error_response(request_id, ex.code, ex.message)
        return None if is_notification else {"jsonrpc": "2.0", "id": request_id, "result": result}

    def handle_line(self, line):
        """Handle one line of the stdio transport, return the response line (without newline) or None."""
        try:
            message = json.loads(line)
        except ValueError:
            return json.dumps(_error_response(None, PARSE_ERROR, "Parse error"))
        if isinstance(message, list):
            # JSON-RPC batch (MCP 2025-03-26).
            responses = [response for item in message if (response := self.handle(item)) is not None]
            return json.dumps(responses, ensure_ascii=False) if responses else None
        response = self.handle(message)
        return None if response is None else json.dumps(response, ensure_ascii=False)


def _error_response(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(bridge, stdin, stdout):
    """Serve MCP over the binary streams ``stdin``/``stdout`` (one JSON message per line) until EOF."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        response = bridge.handle_line(line.decode("utf-8", errors="replace"))
        if response is not None:
            stdout.write(response.encode("utf-8") + b"\n")
            stdout.flush()


def main(argv=None):
    parser = argparse.ArgumentParser(description="MCP server (stdio) for the Workbench Plus Command Server.")
    parser.add_argument(
        "--discovery", default=os.environ.get(DISCOVERY_ENV),
        help="Path of the Command Server's command_server.json (default: ${:s})".format(DISCOVERY_ENV))
    parser.add_argument(
        "--timeout", type=float, default=None,
        help="Seconds to wait for Blender to answer a call (default: no limit)")
    args = parser.parse_args(argv)

    bridge = Bridge(args.discovery, timeout=args.timeout)
    try:
        serve(bridge, sys.stdin.buffer, sys.stdout.buffer)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
