# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Shelves: shareable button bars for scripts and operators.

The shelves are stored as JSON in the add-on's user directory (see :mod:`.library` for the format) and shown
in the 3D Viewport sidebar, *Workbench* tab. Selected text in the Text Editor can be turned into a button.
"""

__all__ = (
    "library",
    "library_filepath_default",
    "register",
    "unregister",
)

import json
import os
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper, ImportHelper

from ..utils import classes_register, classes_unregister, selected_text
from .library import Library, button_new

ADDON_PACKAGE = __package__.rpartition(".")[0]

LIBRARY_FILENAME = "shelves.json"

library = Library()

# Set when the library file could not be read: changes are then not saved, so the file is not overwritten.
library_error = None


def library_filepath_default():
    return os.path.join(bpy.utils.extension_path_user(ADDON_PACKAGE, create=False), LIBRARY_FILENAME)


def library_reload(filepath):
    """Load the shelves from ``filepath``; on error keep an empty library that is not saved."""
    global library_error
    library.filepath = filepath
    try:
        library.load()
        library_error = None
    except (OSError, ValueError) as ex:
        library.filepath = None
        library.shelves = []
        library_error = str(ex)
        print("Workbench Plus: shelves not loaded:", library_error)


def _icon_valid(icon):
    return icon in bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys()


def _shelf_index_valid(index):
    return 0 <= index < len(library.shelves)


def _button_get(shelf_index, button_index):
    if not _shelf_index_valid(shelf_index):
        return None
    buttons = library.shelves[shelf_index]["buttons"]
    return buttons[button_index] if 0 <= button_index < len(buttons) else None


def _settings(context):
    return context.window_manager.wbp_shelves


def _active_shelf_index(context):
    index = _settings(context).active_shelf
    return index if _shelf_index_valid(index) else (0 if library.shelves else -1)


class ShelvesSettings(PropertyGroup):
    active_shelf: IntProperty(name="Active Shelf", description="Index of the shelf shown in the panel", min=0)
    use_edit: BoolProperty(name="Edit", description="Show the controls to reorder and remove buttons and shelves")


# -----------------------------------------------------------------------------
# Running buttons

class WBP_OT_shelf_run(Operator):
    bl_idname = "wbp.shelf_run"
    bl_label = "Run Shelf Button"
    bl_options = {'INTERNAL'}

    shelf: IntProperty(name="Shelf", description="Index of the shelf")
    button: IntProperty(name="Button", description="Index of the button in the shelf")

    @classmethod
    def description(cls, _context, properties):
        button = _button_get(properties.shelf, properties.button)
        if button is None:
            return "Missing shelf button"
        return button.get("tooltip") or (
            "Run " + button["operator"] if button["kind"] == "OPERATOR" else "Run a Python script")

    def execute(self, context):
        button = _button_get(self.shelf, self.button)
        if button is None:
            self.report({'ERROR'}, "Shelf button not found")
            return {'CANCELLED'}
        if button["kind"] == "OPERATOR":
            return self._run_operator(button)
        return self._run_script(button, context)

    def _run_operator(self, button):
        module_name, _, operator_name = button["operator"].partition(".")
        operator = getattr(getattr(bpy.ops, module_name, None), operator_name, None)
        try:
            operator.get_rna_type()
        except (AttributeError, KeyError):
            self.report({'ERROR'}, "Unknown operator {!r}".format(button["operator"]))
            return {'CANCELLED'}
        try:
            operator('INVOKE_DEFAULT', **button.get("properties", {}))
        except (RuntimeError, TypeError, ValueError) as ex:
            self.report({'ERROR'}, "{:s}: {:s}".format(button["label"], str(ex).strip()))
            return {'CANCELLED'}
        return {'FINISHED'}

    def _run_script(self, button, context):
        namespace = {"__name__": "__shelf__", "bpy": bpy, "C": context, "D": bpy.data}
        try:
            exec(compile(button["script"], "<shelf: {:s}>".format(button["label"]), "exec"), namespace)
        except Exception as ex:
            traceback.print_exc()
            self.report({'ERROR'}, "{:s} failed: {:s}: {:s}".format(button["label"], type(ex).__name__, str(ex)))
            return {'CANCELLED'}
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Editing shelves

class WBP_OT_shelf_activate(Operator):
    """Show this shelf"""
    bl_idname = "wbp.shelf_activate"
    bl_label = "Show Shelf"
    bl_options = {'INTERNAL'}

    index: IntProperty(name="Index", description="Index of the shelf to show")

    def execute(self, context):
        if not _shelf_index_valid(self.index):
            return {'CANCELLED'}
        _settings(context).active_shelf = self.index
        return {'FINISHED'}


class WBP_OT_shelf_add(Operator):
    """Add an empty shelf"""
    bl_idname = "wbp.shelf_add"
    bl_label = "Add Shelf"

    name: StringProperty(name="Name", description="Name of the new shelf", default="Shelf")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not self.name.strip():
            self.report({'ERROR'}, "The shelf needs a name")
            return {'CANCELLED'}
        _settings(context).active_shelf = library.shelf_add(self.name)
        return {'FINISHED'}


class WBP_OT_shelf_remove(Operator):
    """Remove the shelf and all its buttons"""
    bl_idname = "wbp.shelf_remove"
    bl_label = "Remove Shelf"

    index: IntProperty(name="Index", description="Index of the shelf to remove")

    def invoke(self, context, _event):
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        if not _shelf_index_valid(self.index):
            self.report({'ERROR'}, "Shelf not found")
            return {'CANCELLED'}
        library.shelf_remove(self.index)
        settings = _settings(context)
        settings.active_shelf = max(0, min(settings.active_shelf, len(library.shelves) - 1))
        return {'FINISHED'}


class WBP_OT_shelf_rename(Operator):
    """Rename the shelf"""
    bl_idname = "wbp.shelf_rename"
    bl_label = "Rename Shelf"

    index: IntProperty(name="Index", description="Index of the shelf to rename")
    name: StringProperty(name="Name", description="New name of the shelf")

    def invoke(self, context, _event):
        if _shelf_index_valid(self.index):
            self.name = library.shelves[self.index]["name"]
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, _context):
        if not _shelf_index_valid(self.index) or not self.name.strip():
            self.report({'ERROR'}, "Shelf not found or empty name")
            return {'CANCELLED'}
        library.shelf_rename(self.index, self.name)
        return {'FINISHED'}


class _ButtonProperties:
    label: StringProperty(name="Label", description="Text of the button")
    icon: StringProperty(name="Icon", description="Icon name, e.g. MESH_CUBE (see the Icon Viewer add-on)",
                         default="NONE")
    tooltip: StringProperty(name="Tooltip", description="Shown when hovering the button")


class WBP_OT_shelf_button_add(_ButtonProperties, Operator):
    """Add a button that runs an operator or a Python script to the shelf"""
    bl_idname = "wbp.shelf_button_add"
    bl_label = "Add Button"

    shelf: IntProperty(name="Shelf", description="Index of the shelf, -1 for the shown one", default=-1)
    kind: EnumProperty(
        name="Kind",
        description="What the button runs",
        items=(
            ('OPERATOR', "Operator", "An operator, with optional properties"),
            ('SCRIPT', "Script", "Python code"),
        ),
    )
    operator: StringProperty(name="Operator", description="Operator to run, e.g. mesh.primitive_cube_add")
    properties: StringProperty(name="Properties", description="Operator properties as JSON, e.g. {\"size\": 2}",
                               default="{}")
    script: StringProperty(name="Script", description="Python code to run")

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = True
        layout.prop(self, "label")
        layout.prop(self, "icon")
        layout.prop(self, "tooltip")
        layout.prop(self, "kind", expand=True)
        if self.kind == 'OPERATOR':
            layout.prop(self, "operator")
            layout.prop(self, "properties")
        else:
            layout.prop(self, "script")

    def execute(self, context):
        shelf_index = _active_shelf_index(context) if self.shelf == -1 else self.shelf
        if not _shelf_index_valid(shelf_index):
            self.report({'ERROR'}, "Add a shelf first")
            return {'CANCELLED'}
        icon = self.icon.strip().upper() or "NONE"
        if not _icon_valid(icon):
            self.report({'WARNING'}, "Unknown icon {!r}, using none".format(icon))
            icon = "NONE"
        try:
            properties = json.loads(self.properties or "{}") if self.kind == 'OPERATOR' else None
            button = button_new(self.label.strip(), kind=self.kind, icon=icon, tooltip=self.tooltip,
                                operator=self.operator.strip(), properties=properties, script=self.script)
            library.button_add(shelf_index, button)
        except (ValueError, TypeError) as ex:
            self.report({'ERROR'}, "Invalid button: {:s}".format(str(ex)))
            return {'CANCELLED'}
        return {'FINISHED'}


class WBP_OT_shelf_button_remove(Operator):
    """Remove the button from the shelf"""
    bl_idname = "wbp.shelf_button_remove"
    bl_label = "Remove Button"
    bl_options = {'INTERNAL'}

    shelf: IntProperty(name="Shelf", description="Index of the shelf")
    button: IntProperty(name="Button", description="Index of the button")

    def execute(self, _context):
        if _button_get(self.shelf, self.button) is None:
            return {'CANCELLED'}
        library.button_remove(self.shelf, self.button)
        return {'FINISHED'}


class WBP_OT_shelf_button_move(Operator):
    """Move the button within its shelf"""
    bl_idname = "wbp.shelf_button_move"
    bl_label = "Move Button"
    bl_options = {'INTERNAL'}

    shelf: IntProperty(name="Shelf", description="Index of the shelf")
    button: IntProperty(name="Button", description="Index of the button")
    offset: IntProperty(name="Offset", description="Positions to move, negative moves earlier", default=1)

    def execute(self, _context):
        if _button_get(self.shelf, self.button) is None:
            return {'CANCELLED'}
        library.button_move(self.shelf, self.button, self.offset)
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Sharing

class WBP_OT_shelf_export(Operator, ExportHelper):
    """Save shelves to a file to share them"""
    bl_idname = "wbp.shelf_export"
    bl_label = "Export Shelves"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    use_all: BoolProperty(name="All Shelves", description="Export every shelf instead of only the shown one",
                          default=False)

    @classmethod
    def poll(cls, _context):
        return bool(library.shelves)

    def execute(self, context):
        indices = None if self.use_all else [_active_shelf_index(context)]
        try:
            library.export_shelves(self.filepath, indices)
        except OSError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class WBP_OT_shelf_import(Operator, ImportHelper):
    """Add the shelves from a file"""
    bl_idname = "wbp.shelf_import"
    bl_label = "Import Shelves"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})
    use_replace: BoolProperty(name="Replace", description="Replace shelves with the same name instead of "
                              "adding renamed copies", default=False)

    def execute(self, _context):
        try:
            count = library.import_shelves(self.filepath, replace=self.use_replace)
        except (OSError, ValueError) as ex:
            self.report({'ERROR'}, "Shelves not imported: {:s}".format(str(ex)))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported {:d} shelves".format(count))
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Text Editor

class WBP_OT_shelf_button_from_text(Operator):
    """Add a shelf button that runs the selected text (or the whole text when nothing is selected)"""
    bl_idname = "wbp.shelf_button_from_text"
    bl_label = "Add to Shelf"

    label: StringProperty(name="Label", description="Text of the new button", default="Script")

    @classmethod
    def poll(cls, context):
        return context.space_data is not None and getattr(context.space_data, "text", None) is not None

    def invoke(self, context, _event):
        self.label = context.space_data.text.name.rpartition(".")[0] or context.space_data.text.name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        script = selected_text(context.space_data.text)
        if not script.strip():
            self.report({'ERROR'}, "Nothing to add, the text is empty")
            return {'CANCELLED'}
        shelf_index = _active_shelf_index(context)
        if shelf_index == -1:
            shelf_index = library.shelf_add("Scripts")
        library.button_add(shelf_index, button_new(self.label.strip() or "Script", kind="SCRIPT",
                                                   icon="SCRIPT", script=script))
        self.report({'INFO'}, "Added {!r} to shelf {!r}".format(self.label, library.shelves[shelf_index]["name"]))
        return {'FINISHED'}


def _text_context_menu_draw(menu, _context):
    menu.layout.separator()
    menu.layout.operator(WBP_OT_shelf_button_from_text.bl_idname, icon='ADD')


# -----------------------------------------------------------------------------
# Panel

class WBP_PT_shelves(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Workbench"
    bl_label = "Shelves"

    def draw_header_preset(self, context):
        self.layout.prop(_settings(context), "use_edit", text="", icon='PREFERENCES', emboss=False)

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        if library_error:
            layout.label(text="Shelves file could not be read:", icon='ERROR')
            layout.label(text=library_error)

        active = _active_shelf_index(context)
        row = layout.row(align=True)
        flow = row.grid_flow(row_major=True, columns=0, even_columns=False, align=True)
        for index, shelf in enumerate(library.shelves):
            flow.operator(WBP_OT_shelf_activate.bl_idname, text=shelf["name"], depress=index == active).index = index
        row.operator(WBP_OT_shelf_add.bl_idname, text="", icon='ADD')
        if active == -1:
            layout.label(text="Add a shelf to start")
        else:
            self._draw_shelf(layout, active, settings.use_edit)

        row = layout.row(align=True)
        row.operator(WBP_OT_shelf_import.bl_idname, text="Import", icon='IMPORT')
        row.operator(WBP_OT_shelf_export.bl_idname, text="Export", icon='EXPORT')

    @staticmethod
    def _draw_shelf(layout, shelf_index, use_edit):
        shelf = library.shelves[shelf_index]
        if use_edit:
            row = layout.row(align=True)
            row.operator(WBP_OT_shelf_rename.bl_idname, text="Rename", icon='GREASEPENCIL').index = shelf_index
            row.operator(WBP_OT_shelf_remove.bl_idname, text="Remove", icon='TRASH').index = shelf_index
            col = layout.column(align=True)
            for button_index, button in enumerate(shelf["buttons"]):
                row = col.row(align=True)
                row.label(text=button["label"], icon=button["icon"] if _icon_valid(button["icon"]) else 'QUESTION')
                for icon, offset in (('TRIA_UP', -1), ('TRIA_DOWN', 1)):
                    props = row.operator(WBP_OT_shelf_button_move.bl_idname, text="", icon=icon)
                    props.shelf, props.button, props.offset = shelf_index, button_index, offset
                props = row.operator(WBP_OT_shelf_button_remove.bl_idname, text="", icon='X')
                props.shelf, props.button = shelf_index, button_index
        else:
            flow = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            for button_index, button in enumerate(shelf["buttons"]):
                icon = button["icon"] if _icon_valid(button["icon"]) else 'QUESTION'
                props = flow.operator(WBP_OT_shelf_run.bl_idname, text=button["label"], icon=icon)
                props.shelf, props.button = shelf_index, button_index
            if not shelf["buttons"]:
                layout.label(text="Empty shelf")
        layout.operator(WBP_OT_shelf_button_add.bl_idname, icon='ADD').shelf = shelf_index


_classes = (
    ShelvesSettings,
    WBP_OT_shelf_run,
    WBP_OT_shelf_activate,
    WBP_OT_shelf_add,
    WBP_OT_shelf_remove,
    WBP_OT_shelf_rename,
    WBP_OT_shelf_button_add,
    WBP_OT_shelf_button_remove,
    WBP_OT_shelf_button_move,
    WBP_OT_shelf_export,
    WBP_OT_shelf_import,
    WBP_OT_shelf_button_from_text,
    WBP_PT_shelves,
)


def register():
    classes_register(_classes)
    bpy.types.WindowManager.wbp_shelves = bpy.props.PointerProperty(type=ShelvesSettings)
    bpy.types.TEXT_MT_context_menu.append(_text_context_menu_draw)
    library_reload(library_filepath_default())


def unregister():
    bpy.types.TEXT_MT_context_menu.remove(_text_context_menu_draw)
    del bpy.types.WindowManager.wbp_shelves
    classes_unregister(_classes)
