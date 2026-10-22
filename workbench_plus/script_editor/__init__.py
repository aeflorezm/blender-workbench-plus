# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Script Editor: echo of the Python commands behind UI actions.

Uses the Text Editor as a unified script editor: *Run* executes the selection (or the whole text)
and writes the code and its output to the *Script Log* text, where operators run from the interface
and property changes are echoed as Python that repeats them.
"""

__all__ = (
    "LOG_TEXT_NAME",
    "log_append",
    "log_text",
    "register",
    "run_code",
    "unregister",
)

import contextlib
import io
import traceback

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty
from bpy.types import Operator, Panel, PropertyGroup

from ..utils import classes_register, classes_unregister, selected_text
from .echo import WATCHED_PROPERTIES, PropertyWatcher, ReportReader

LOG_TEXT_NAME = "Script Log"

# Older lines are dropped beyond this.
LOG_LINES_MAX = 5000

# How often reports are read while echoing operators (seconds).
ECHO_INTERVAL = 0.25

# Operators of this feature are not echoed, the log already shows what they ran.
_ECHO_IGNORE_PREFIXES = ("bpy.ops.wbp.script_",)

_report_reader = ReportReader()
_property_watcher = PropertyWatcher()

# Owner of the `bpy.msgbus` subscriptions, cleared to unsubscribe.
_msgbus_owner = object()

_namespace = {}


def log_text(create=True):
    text = bpy.data.texts.get(LOG_TEXT_NAME)
    if text is None and create:
        text = bpy.data.texts.new(LOG_TEXT_NAME)
    return text


def log_append(lines):
    """Append ``lines`` to the *Script Log* text, keeping at most :data:`LOG_LINES_MAX` lines."""
    if not lines:
        return
    text = log_text()
    content = text.as_string()
    log_lines = (content.split("\n") if content else []) + list(lines)
    del log_lines[:-LOG_LINES_MAX]
    text.from_string("\n".join(log_lines))
    # Keep the cursor at the end, so editors showing the log follow it.
    text.cursor_set(len(text.lines) - 1, character=len(text.lines[-1].body))


def _namespace_get(context):
    if not _namespace:
        _namespace.update({"__name__": "__script_editor__", "bpy": bpy})
    _namespace["C"] = context
    _namespace["D"] = bpy.data
    return _namespace


def run_code(code, label, context=None):
    """
    Execute ``code`` in a namespace kept between runs (with ``bpy``, ``C``, ``D``), writing the code and
    its output (or traceback) to the log. Returns the exception raised by the code, or None.
    """
    output = io.StringIO()
    error = None
    try:
        compiled = compile(code, "<{:s}>".format(label), "exec")
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            exec(compiled, _namespace_get(context or bpy.context))
    except (Exception, SystemExit) as ex:
        error = ex
        output.write("".join(traceback.format_exception(ex)))

    lines = ["# >>> " + label] + code.rstrip("\n").split("\n")
    lines += ["# " + line for line in output.getvalue().rstrip("\n").split("\n") if output.getvalue()]
    log_append(lines)
    return error


# -----------------------------------------------------------------------------
# Echo

def _echo_operators_timer():
    settings = _settings(bpy.context)
    if settings is None or not settings.use_echo_operators:
        return None
    lines = [line for line in _report_reader.read(bpy.context.window_manager.reports)
             if not line.startswith(_ECHO_IGNORE_PREFIXES)]
    log_append(lines)
    return ECHO_INTERVAL


def _echo_operators_set(state):
    if state:
        _report_reader.skip_existing(bpy.context.window_manager.reports)
        if not bpy.app.timers.is_registered(_echo_operators_timer):
            bpy.app.timers.register(_echo_operators_timer, first_interval=ECHO_INTERVAL, persistent=True)
    elif bpy.app.timers.is_registered(_echo_operators_timer):
        bpy.app.timers.unregister(_echo_operators_timer)


def _on_property_changed(attr, collection_name):
    log_append(_property_watcher.check(attr, collection_name))


def _echo_properties_subscribe():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    for cls, attr, collection_name in WATCHED_PROPERTIES:
        bpy.msgbus.subscribe_rna(
            key=(cls, attr),
            owner=_msgbus_owner,
            args=(attr, collection_name),
            notify=_on_property_changed,
        )
    _property_watcher.reset()


@persistent
def _on_depsgraph_update_post(_scene, _depsgraph):
    # Data-blocks created since the subscription start with the value they had when first evaluated,
    # so that later changes to them are echoed.
    _property_watcher.track_new()


def _echo_properties_set(state):
    handlers = bpy.app.handlers.depsgraph_update_post
    if state:
        _echo_properties_subscribe()
        if _on_depsgraph_update_post not in handlers:
            handlers.append(_on_depsgraph_update_post)
    else:
        bpy.msgbus.clear_by_owner(_msgbus_owner)
        if _on_depsgraph_update_post in handlers:
            handlers.remove(_on_depsgraph_update_post)


@persistent
def _on_load_post(*_args):
    # Loading a file clears the subscriptions and replaces the data.
    settings = _settings(bpy.context)
    if settings is not None and settings.use_echo_properties:
        _echo_properties_subscribe()


def _settings(context):
    return getattr(context.window_manager, "wbp_script_editor", None)


class ScriptEditorSettings(PropertyGroup):
    use_echo_operators: BoolProperty(
        name="Echo Operators",
        description="Write the operators run from the interface to the Script Log, as Python",
        default=False,
        update=lambda self, _context: _echo_operators_set(self.use_echo_operators),
    )
    use_echo_properties: BoolProperty(
        name="Echo Property Changes",
        description="Write changes of object transforms, visibility and the current frame to the Script Log, "
                    "as Python assignments",
        default=False,
        update=lambda self, _context: _echo_properties_set(self.use_echo_properties),
    )


# -----------------------------------------------------------------------------
# Operators

def _text_editor_text(context):
    space = context.space_data
    return space.text if space is not None and space.type == 'TEXT_EDITOR' else None


class WBP_OT_script_run(Operator):
    """Run the selected lines (or the whole text) and write the code and its output to the Script Log"""
    bl_idname = "wbp.script_run"
    bl_label = "Run Selection"

    @classmethod
    def poll(cls, context):
        return _text_editor_text(context) is not None

    def execute(self, context):
        text = _text_editor_text(context)
        code = selected_text(text)
        if not code.strip():
            self.report({'WARNING'}, "Nothing to run")
            return {'CANCELLED'}
        error = run_code(code, text.name, context)
        if error is not None:
            self.report({'ERROR'}, "{:s}: {:s} (see {:s})".format(type(error).__name__, str(error), LOG_TEXT_NAME))
            return {'CANCELLED'}
        return {'FINISHED'}


class WBP_OT_script_log_show(Operator):
    """Show the Script Log in this Text Editor"""
    bl_idname = "wbp.script_log_show"
    bl_label = "Show Log"

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and context.space_data.type == 'TEXT_EDITOR'

    def execute(self, context):
        context.space_data.text = log_text()
        return {'FINISHED'}


class WBP_OT_script_log_clear(Operator):
    """Remove everything from the Script Log"""
    bl_idname = "wbp.script_log_clear"
    bl_label = "Clear Log"

    def execute(self, _context):
        text = log_text(create=False)
        if text is not None:
            text.clear()
        return {'FINISHED'}


class WBP_PT_script_editor(Panel):
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Workbench"
    bl_label = "Script Editor"

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        layout.operator(WBP_OT_script_run.bl_idname, icon='PLAY')
        row = layout.row(align=True)
        row.operator(WBP_OT_script_log_show.bl_idname, icon='TEXT')
        row.operator(WBP_OT_script_log_clear.bl_idname, text="", icon='TRASH')
        col = layout.column(heading="Echo")
        col.prop(settings, "use_echo_operators", text="Operators")
        col.prop(settings, "use_echo_properties", text="Property Changes")


_classes = (
    ScriptEditorSettings,
    WBP_OT_script_run,
    WBP_OT_script_log_show,
    WBP_OT_script_log_clear,
    WBP_PT_script_editor,
)

_keymaps = []


def register():
    classes_register(_classes)
    bpy.types.WindowManager.wbp_script_editor = bpy.props.PointerProperty(type=ScriptEditorSettings)
    bpy.app.handlers.load_post.append(_on_load_post)

    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig is not None:
        keymap = keyconfig.keymaps.new(name="Text", space_type='TEXT_EDITOR')
        item = keymap.keymap_items.new(WBP_OT_script_run.bl_idname, 'RET', 'PRESS', ctrl=True, shift=True)
        _keymaps.append((keymap, item))


def unregister():
    for keymap, item in _keymaps:
        keymap.keymap_items.remove(item)
    _keymaps.clear()

    _echo_operators_set(False)
    _echo_properties_set(False)
    bpy.app.handlers.load_post.remove(_on_load_post)
    _namespace.clear()
    del bpy.types.WindowManager.wbp_script_editor
    classes_unregister(_classes)
