# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Methods of the Command Server, executed on Blender's main thread.
"""

__all__ = (
    "Dispatcher",
    "value_to_json",
)

import contextlib
import io
import math
import traceback

import bpy
import mathutils

from . import protocol
from .protocol import RpcError

EVENTS = frozenset((
    "depsgraph_update",
    "frame_change",
    "load_post",
    "save_post",
    "undo_post",
    "redo_post",
    "progress",
))

# Values nested deeper than this are returned as their `repr`, protecting against cycles.
_VALUE_DEPTH_MAX = 16


# -----------------------------------------------------------------------------
# Values

def value_to_json(value, depth=0):
    """Convert a Python or Blender value to a JSON serializable value (see "Values" in the protocol)."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth > _VALUE_DEPTH_MAX:
        return {"$type": "repr", "repr": repr(value)}
    depth += 1
    if isinstance(value, mathutils.Matrix):
        return [[value_to_json(component, depth) for component in row] for row in value]
    if isinstance(value, (mathutils.Vector, mathutils.Euler, mathutils.Quaternion, mathutils.Color)):
        return [value_to_json(component, depth) for component in value]
    if isinstance(value, bpy.types.ID):
        return {"$type": "ID", "id_type": value.id_type, "name": value.name}
    if isinstance(value, bpy.types.bpy_struct):
        return {"$type": "struct", "rna_type": value.bl_rna.identifier, "repr": repr(value)}
    if isinstance(value, dict):
        return {str(key): value_to_json(item, depth) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted((value_to_json(item, depth) for item in value), key=repr)
    if isinstance(value, (list, tuple, bpy.types.bpy_prop_array, bpy.types.bpy_prop_collection)):
        return [value_to_json(item, depth) for item in value]
    return {"$type": "repr", "repr": repr(value)}


def _python_error(ex):
    """An :class:`RpcError` describing the Python exception ``ex``, with its traceback."""
    return RpcError(
        protocol.PYTHON_EXCEPTION,
        "{:s}: {:s}".format(type(ex).__name__, str(ex)),
        {"type": type(ex).__name__, "traceback": "".join(traceback.format_exception(ex))},
    )


def _param(params, name, types, default=...):
    value = params.get(name, default)
    if value is ...:
        raise RpcError(protocol.INVALID_PARAMS, "Invalid params: missing {!r}".format(name))
    if not isinstance(value, types):
        raise RpcError(protocol.INVALID_PARAMS, "Invalid params: {!r} has the wrong type".format(name))
    return value


# -----------------------------------------------------------------------------
# RNA paths

def _path_root(params):
    root = _param(params, "root", str, "data")
    if root == "data":
        return bpy.data
    if root == "context":
        return bpy.context
    raise RpcError(protocol.INVALID_PARAMS, "Invalid params: 'root' must be 'data' or 'context'")


def _path_split(path):
    """
    Split an RNA path into ``(owner_path, key)`` around its last component, where ``key`` is an attribute
    name (str) or a subscript (``[int]`` or ``["str"]``, returned as a one item list).
    """
    depth = 0
    quote = None
    for i in range(len(path) - 1, -1, -1):
        c = path[i]
        if quote:
            if c == quote and (i == 0 or path[i - 1] != "\\"):
                quote = None
        elif c in "\"'":
            quote = c
        elif c == "]":
            depth += 1
        elif c == "[":
            depth -= 1
            if depth == 0 and path.endswith("]"):
                subscript = path[i + 1:-1].strip()
                if subscript[:1] in "\"'":
                    key = subscript[1:-1]
                else:
                    try:
                        key = int(subscript)
                    except ValueError:
                        raise RpcError(protocol.INVALID_PARAMS, "Invalid params: bad subscript in {!r}".format(path))
                return path[:i], [key]
        elif c == "." and depth == 0:
            return path[:i], path[i + 1:]
    return "", path


def _path_resolve(root, path):
    if not path:
        return root
    try:
        return root.path_resolve(path)
    except ValueError as ex:
        raise RpcError(protocol.INVALID_PARAMS, "Invalid params: {:s}".format(str(ex)))


# -----------------------------------------------------------------------------
# Dispatcher

class Dispatcher:
    """
    Maps method names to their implementation. Owns the namespace that ``exec`` and ``eval`` share,
    and the ``notify`` callback used for ``progress`` events.
    """

    def __init__(self, *, notify, shutdown):
        self._notify = notify
        self._shutdown = shutdown
        self.namespace = {
            "__name__": "__command_server__",
            "bpy": bpy,
            "notify_progress": self._notify_progress,
        }
        self._methods = {
            "ping": self.ping,
            "version": self.version,
            "exec": self.exec,
            "eval": self.eval,
            "call_operator": self.call_operator,
            "get_property": self.get_property,
            "set_property": self.set_property,
            "batch": self.batch,
            "subscribe": self.subscribe,
            "unsubscribe": self.unsubscribe,
            "shutdown": self.shutdown,
        }

    def __call__(self, method, params, connection):
        if method == "auth":
            # Already authenticated (authentication happens before requests are queued).
            return True
        function = self._methods.get(method)
        if function is None:
            raise RpcError(protocol.METHOD_NOT_FOUND, "Method not found: {!r}".format(method))
        return function(params, connection)

    def _notify_progress(self, value, message=""):
        """Send a ``progress`` event to subscribed clients (available to code run with ``exec``)."""
        self._notify("progress", {"value": float(value), "message": str(message)})

    # Methods.

    def ping(self, _params, _connection):
        return "pong"

    def version(self, _params, _connection):
        from . import server_version
        return {
            "protocol": protocol.PROTOCOL_VERSION,
            "server": server_version(),
            "blender": bpy.app.version_string,
        }

    def exec(self, params, _connection):
        code = _param(params, "code", str)
        stdout = io.StringIO()
        try:
            compiled = compile(code, "<command_server>", "exec")
            with contextlib.redirect_stdout(stdout):
                exec(compiled, self.namespace)
        except Exception as ex:
            raise _python_error(ex)
        return {"stdout": stdout.getvalue()}

    def eval(self, params, _connection):
        expression = _param(params, "expression", str)
        try:
            value = eval(compile(expression, "<command_server>", "eval"), self.namespace)
        except Exception as ex:
            raise _python_error(ex)
        return value_to_json(value)

    def call_operator(self, params, _connection, *, undo=True):
        idname = _param(params, "operator", str)
        properties = _param(params, "properties", dict, {})
        execution_context = _param(params, "execution_context", str, "EXEC_DEFAULT")

        module_name, _, operator_name = idname.partition(".")
        operator = getattr(getattr(bpy.ops, module_name, None), operator_name, None) if operator_name else None
        try:
            operator.get_rna_type()
        except (AttributeError, KeyError):
            raise RpcError(protocol.INVALID_PARAMS, "Invalid params: unknown operator {!r}".format(idname))

        try:
            result = operator(execution_context, undo, **properties)
        except RuntimeError as ex:
            raise RpcError(protocol.OPERATOR_FAILED, "Operator failed: {:s}".format(str(ex).strip()))
        except (TypeError, ValueError) as ex:
            raise RpcError(protocol.INVALID_PARAMS, "Invalid params: {:s}".format(str(ex)))
        return {"result": sorted(result)}

    def get_property(self, params, _connection):
        path = _param(params, "path", str)
        return value_to_json(_path_resolve(_path_root(params), path))

    def set_property(self, params, _connection):
        path = _param(params, "path", str)
        if "value" not in params:
            raise RpcError(protocol.INVALID_PARAMS, "Invalid params: missing 'value'")
        value = params["value"]
        owner_path, key = _path_split(path)
        owner = _path_resolve(_path_root(params), owner_path)
        try:
            if isinstance(key, list):
                owner[key[0]] = value
            elif not hasattr(owner, key):
                raise RpcError(protocol.INVALID_PARAMS, "Invalid params: {!r} has no property {!r}".format(
                    owner_path or params.get("root", "data"), key))
            else:
                setattr(owner, key, value)
        except RpcError:
            raise
        except Exception as ex:
            raise _python_error(ex)
        return None

    def batch(self, params, connection):
        calls = _param(params, "calls", list)
        undo_message = _param(params, "undo", str, "Command Server Batch")
        results = []
        try:
            for index, call in enumerate(calls):
                if not isinstance(call, dict) or not isinstance(call.get("method"), str):
                    raise RpcError(protocol.BATCH_FAILED, "Batch failed: call {:d} is not a method call".format(index),
                                   {"index": index})
                method = call["method"]
                call_params = call.get("params", {})
                if not isinstance(call_params, dict):
                    raise RpcError(protocol.BATCH_FAILED, "Batch failed: call {:d} has invalid params".format(index),
                                   {"index": index})
                if method in {"batch", "shutdown", "auth", "subscribe", "unsubscribe"}:
                    raise RpcError(protocol.BATCH_FAILED,
                                   "Batch failed: {!r} is not allowed in a batch".format(method), {"index": index})
                try:
                    if method == "call_operator":
                        # Operators must not push their own undo steps, the batch pushes one at the end.
                        results.append(self.call_operator(call_params, connection, undo=False))
                    else:
                        results.append(self(method, call_params, connection))
                except RpcError as ex:
                    raise RpcError(protocol.BATCH_FAILED, "Batch failed at call {:d}: {:s}".format(index, ex.message),
                                   {"index": index, "error": ex.as_dict(), "results": results})
        finally:
            # Also after a failure: the calls that ran changed the scene and must be undoable together.
            if calls and bpy.ops.ed.undo_push.poll():
                bpy.ops.ed.undo_push(message=undo_message)
        return results

    def subscribe(self, params, connection):
        events = _param(params, "events", list)
        unknown = [event for event in events if event not in EVENTS]
        if unknown:
            raise RpcError(protocol.INVALID_PARAMS, "Invalid params: unknown events {!r}".format(unknown),
                           {"events": sorted(EVENTS)})
        connection.events.update(events)
        return sorted(connection.events)

    def unsubscribe(self, params, connection):
        events = _param(params, "events", list)
        connection.events.difference_update(events)
        return sorted(connection.events)

    def shutdown(self, _params, _connection):
        self._shutdown()
        return None
