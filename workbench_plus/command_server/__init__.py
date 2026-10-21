# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Command Server: remote control of Blender over a local socket (JSON-RPC 2.0, see ``docs/command_server.md``).

Requests are read in background threads and executed on the main thread: by a timer when Blender runs
with its interface, or by :func:`serve` (the ``wbp.command_server_serve`` operator) in background mode.
"""

__all__ = (
    "CommandServerSettings",
    "discovery_filepath_default",
    "draw_settings",
    "is_running",
    "register",
    "serve",
    "server_port",
    "server_pump",
    "server_start",
    "server_stop",
    "server_version",
    "unregister",
)

import json
import os
import secrets
import time
import tomllib

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator, PropertyGroup

from . import protocol
from .methods import Dispatcher, value_to_json
from .server import HOST, Server

ADDON_PACKAGE = __package__.rpartition(".")[0]

DISCOVERY_FILENAME = "command_server.json"

# How often the timer executes queued requests when Blender runs with its interface (seconds).
TIMER_INTERVAL = 0.002


class CommandServerSettings(PropertyGroup):
    port: IntProperty(
        name="Port",
        description="TCP port the server listens on (on 127.0.0.1 only). 0 picks a free port",
        default=0,
        min=0,
        max=65535,
    )
    use_autostart: BoolProperty(
        name="Start Automatically",
        description="Start the server when Blender starts with its interface",
        default=False,
    )


def _settings():
    addon = bpy.context.preferences.addons.get(ADDON_PACKAGE)
    return addon.preferences.command_server if addon else None


# -----------------------------------------------------------------------------
# Server lifecycle

class _State:
    __slots__ = ("server", "dispatcher", "discovery_filepath", "stop_requested")

    def __init__(self, server, dispatcher, discovery_filepath):
        self.server = server
        self.dispatcher = dispatcher
        self.discovery_filepath = discovery_filepath
        self.stop_requested = False


_state = None

_server_version = None


def server_version():
    """The version of the add-on, from its manifest."""
    global _server_version
    if _server_version is None:
        filepath = os.path.join(os.path.dirname(os.path.dirname(__file__)), "blender_manifest.toml")
        with open(filepath, "rb") as fh:
            _server_version = tomllib.load(fh)["version"]
    return _server_version


def discovery_filepath_default():
    return os.path.join(bpy.utils.extension_path_user(ADDON_PACKAGE, create=True), DISCOVERY_FILENAME)


def _discovery_write(filepath, port, token):
    info = {
        "protocol": protocol.PROTOCOL_VERSION,
        "host": HOST,
        "port": port,
        "token": token,
        "pid": os.getpid(),
        "blender": bpy.app.version_string,
    }
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    # Readable by the current user only (where the platform supports it): the token grants full control.
    fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=2)


def _discovery_remove(filepath):
    try:
        os.remove(filepath)
    except FileNotFoundError:
        pass


def is_running():
    return _state is not None


def server_port():
    """The port the server listens on, or None when it is not running."""
    return _state.server.port if _state else None


def _notify(event, params=None):
    if _state is not None:
        _state.server.notify(event, params)


def _shutdown_request():
    # Stop after the response to `shutdown` has been sent, see `server_pump`.
    _state.stop_requested = True


def server_start(*, port=0, discovery_filepath=None):
    """
    Start the server on ``127.0.0.1:port`` and write the discovery file.

    :arg discovery_filepath: Where to write the discovery file (default: :func:`discovery_filepath_default`).
    :return: The port the server listens on.
    """
    global _state
    if _state is not None:
        raise RuntimeError("The Command Server is already running")

    token = secrets.token_urlsafe(32)
    dispatcher = Dispatcher(notify=_notify, shutdown=_shutdown_request)
    server = Server(port=port, token=token, dispatch=dispatcher)
    if discovery_filepath is None:
        discovery_filepath = discovery_filepath_default()
    try:
        _discovery_write(discovery_filepath, server.port, token)
    except OSError:
        server.stop()
        raise
    server.start()
    _state = _State(server, dispatcher, discovery_filepath)

    _handlers_add()
    if not bpy.app.background:
        bpy.app.timers.register(_timer, first_interval=0.0, persistent=True)
    return server.port


def server_stop():
    """Stop the server (if running), close every connection and remove the discovery file."""
    global _state
    if _state is None:
        return
    state, _state = _state, None
    _handlers_remove()
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    state.server.stop()
    _discovery_remove(state.discovery_filepath)


def server_pump(timeout=0.0):
    """
    Execute the queued requests on the calling (main) thread, waiting up to ``timeout`` seconds for one.

    :return: The number of requests executed.
    """
    if _state is None:
        return 0
    count = _state.server.pump(timeout)
    if _state is not None and _state.stop_requested:
        server_stop()
    return count


def serve(timeout=None):
    """
    Execute requests until the server stops (a client calls ``shutdown``) or ``timeout`` seconds pass.

    For background mode, where timers do not run. Blocks the calling thread.
    """
    time_end = None if timeout is None else time.monotonic() + timeout
    while _state is not None:
        wait = 0.1 if time_end is None else min(0.1, time_end - time.monotonic())
        if wait <= 0.0:
            break
        server_pump(wait)


def _timer():
    if _state is None:
        return None
    server_pump()
    return TIMER_INTERVAL if _state is not None else None


# -----------------------------------------------------------------------------
# Events

def _is_subscribed(event):
    return _state is not None and any(event in connection.events for connection in _state.server.connections)


@persistent
def _on_depsgraph_update_post(_scene, depsgraph):
    if _is_subscribed("depsgraph_update"):
        ids = [{"id_type": update.id.id_type, "name": update.id.name} for update in depsgraph.updates]
        _notify("depsgraph_update", {"ids": ids})


@persistent
def _on_frame_change_post(scene, _depsgraph):
    if _is_subscribed("frame_change"):
        _notify("frame_change", {"frame": scene.frame_current})


@persistent
def _on_load_post(filepath, *_args):
    _notify("load_post", {"filepath": value_to_json(filepath)})


@persistent
def _on_save_post(filepath, *_args):
    _notify("save_post", {"filepath": value_to_json(filepath)})


@persistent
def _on_undo_post(*_args):
    _notify("undo_post")


@persistent
def _on_redo_post(*_args):
    _notify("redo_post")


_HANDLERS = (
    ("depsgraph_update_post", _on_depsgraph_update_post),
    ("frame_change_post", _on_frame_change_post),
    ("load_post", _on_load_post),
    ("save_post", _on_save_post),
    ("undo_post", _on_undo_post),
    ("redo_post", _on_redo_post),
)


def _handlers_add():
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function not in handlers:
            handlers.append(function)


def _handlers_remove():
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)


# -----------------------------------------------------------------------------
# Operators

class WBP_OT_command_server_start(Operator):
    """Start the Command Server, so other programs can control Blender"""
    bl_idname = "wbp.command_server_start"
    bl_label = "Start Command Server"

    @classmethod
    def poll(cls, _context):
        return not is_running()

    def execute(self, _context):
        settings = _settings()
        try:
            port = server_start(port=settings.port if settings else 0)
        except OSError as ex:
            self.report({'ERROR'}, "Command Server could not start: {:s}".format(str(ex)))
            return {'CANCELLED'}
        self.report({'INFO'}, "Command Server listening on {:s}:{:d}".format(HOST, port))
        return {'FINISHED'}


class WBP_OT_command_server_stop(Operator):
    """Stop the Command Server and close every connection"""
    bl_idname = "wbp.command_server_stop"
    bl_label = "Stop Command Server"

    @classmethod
    def poll(cls, _context):
        return is_running()

    def execute(self, _context):
        server_stop()
        return {'FINISHED'}


class WBP_OT_command_server_serve(Operator):
    """Run the Command Server until a client asks it to shut down (background mode only)"""
    bl_idname = "wbp.command_server_serve"
    bl_label = "Serve Commands"

    port: IntProperty(
        name="Port",
        description="TCP port to listen on, -1 uses the add-on preferences, 0 picks a free port",
        default=-1,
        min=-1,
        max=65535,
    )
    timeout: FloatProperty(
        name="Timeout",
        description="Stop after this many seconds, 0 serves until a client calls 'shutdown'",
        default=0.0,
        min=0.0,
        subtype='TIME_ABSOLUTE',
        unit='TIME_ABSOLUTE',
    )

    @classmethod
    def poll(cls, _context):
        return bpy.app.background

    def execute(self, _context):
        if not is_running():
            port = self.port
            if port == -1:
                settings = _settings()
                port = settings.port if settings else 0
            try:
                port = server_start(port=port)
            except OSError as ex:
                self.report({'ERROR'}, "Command Server could not start: {:s}".format(str(ex)))
                return {'CANCELLED'}
            print("Command Server listening on {:s}:{:d}".format(HOST, port), flush=True)
        serve(timeout=self.timeout or None)
        return {'FINISHED'}


def draw_settings(layout, settings):
    """Draw the Command Server section of the add-on preferences."""
    col = layout.column()
    row = col.row()
    if is_running():
        row.label(text="Listening on {:s}:{:d}".format(HOST, server_port()), icon='CHECKMARK')
        row.operator(WBP_OT_command_server_stop.bl_idname, text="Stop")
    else:
        row.label(text="Stopped", icon='RADIOBUT_OFF')
        row.operator(WBP_OT_command_server_start.bl_idname, text="Start")
    col.prop(settings, "port")
    col.prop(settings, "use_autostart")


_classes = (
    WBP_OT_command_server_start,
    WBP_OT_command_server_stop,
    WBP_OT_command_server_serve,
)


def _autostart():
    settings = _settings()
    if settings is not None and settings.use_autostart and not is_running():
        bpy.ops.wbp.command_server_start()
    return None


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    if not bpy.app.background:
        # Deferred: the preferences and a full context are not available while add-ons are being enabled.
        bpy.app.timers.register(_autostart, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_autostart):
        bpy.app.timers.unregister(_autostart)
    server_stop()
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
