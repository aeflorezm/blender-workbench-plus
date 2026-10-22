# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Depsgraph Inspector: shows what the dependency graph re-evaluates, why and how long it takes.

The panels live in the 3D Viewport sidebar, *Workbench* tab. Recording only installs its handlers
while it is on, so it costs nothing otherwise.
"""

__all__ = (
    "is_recording",
    "recorder",
    "recording_set",
    "register",
    "unregister",
)

import json
import time

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras.io_utils import ExportHelper

from ..utils import classes_register, classes_unregister
from . import relations
from .recorder import Recorder

recorder = Recorder()

# Rows shown in the panels.
UPDATES_SHOWN_MAX = 15
RECORDS_SHOWN_MAX = 10

_ID_TYPE_ICONS = {
    'ACTION': 'ACTION',
    'ARMATURE': 'ARMATURE_DATA',
    'CAMERA': 'CAMERA_DATA',
    'COLLECTION': 'OUTLINER_COLLECTION',
    'CURVE': 'CURVE_DATA',
    'CURVES': 'CURVES_DATA',
    'GREASEPENCIL': 'OUTLINER_DATA_GREASEPENCIL',
    'IMAGE': 'IMAGE_DATA',
    'KEY': 'SHAPEKEY_DATA',
    'LATTICE': 'LATTICE_DATA',
    'LIGHT': 'LIGHT_DATA',
    'MATERIAL': 'MATERIAL',
    'MESH': 'MESH_DATA',
    'NODETREE': 'NODETREE',
    'OBJECT': 'OBJECT_DATA',
    'POINTCLOUD': 'POINTCLOUD_DATA',
    'SCENE': 'SCENE_DATA',
    'TEXTURE': 'TEXTURE',
    'VOLUME': 'VOLUME_DATA',
    'WORLD': 'WORLD',
}


def id_type_icon(id_type):
    return _ID_TYPE_ICONS.get(id_type, 'DOT')


# -----------------------------------------------------------------------------
# Recording

def _redraw_sidebars():
    if bpy.app.background:
        return
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


@persistent
def _on_depsgraph_update_pre(_scene, _depsgraph):
    recorder.begin('UPDATE')


@persistent
def _on_depsgraph_update_post(scene, depsgraph):
    if recorder.end('UPDATE', scene.frame_current, depsgraph) is not None:
        _redraw_sidebars()


@persistent
def _on_frame_change_pre(_scene, _depsgraph):
    recorder.begin('FRAME')


@persistent
def _on_frame_change_post(scene, depsgraph):
    if recorder.end('FRAME', scene.frame_current, depsgraph) is not None:
        _redraw_sidebars()


_HANDLERS = (
    ("depsgraph_update_pre", _on_depsgraph_update_pre),
    ("depsgraph_update_post", _on_depsgraph_update_post),
    ("frame_change_pre", _on_frame_change_pre),
    ("frame_change_post", _on_frame_change_post),
)


def is_recording():
    return _on_depsgraph_update_post in bpy.app.handlers.depsgraph_update_post


def recording_set(state):
    """Start (True) or stop (False) recording evaluations."""
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if state and function not in handlers:
            handlers.append(function)
        elif not state and function in handlers:
            handlers.remove(function)


# -----------------------------------------------------------------------------
# Settings

class DepsgraphInspectorSettings(PropertyGroup):
    sort: EnumProperty(
        name="Sort",
        description="Order of the recorded evaluations",
        items=(
            ('DURATION', "Slowest", "Slowest evaluations first"),
            ('FRAME', "Frame", "By frame number"),
            ('ORDER', "Recorded", "In the order they were recorded"),
        ),
        default='DURATION',
    )
    show_unchanged: BoolProperty(
        name="Show Unchanged Data",
        description="Also list data-blocks that were evaluated without transform, geometry or shading changes",
        default=True,
    )


# -----------------------------------------------------------------------------
# Operators

class WBP_OT_depsgraph_record(Operator):
    """Start or stop recording dependency graph evaluations"""
    bl_idname = "wbp.depsgraph_record"
    bl_label = "Record Evaluations"

    state: BoolProperty(name="State", description="Recording on or off", default=True)

    def execute(self, _context):
        recording_set(self.state)
        return {'FINISHED'}


class WBP_OT_depsgraph_clear(Operator):
    """Forget the recorded evaluations"""
    bl_idname = "wbp.depsgraph_clear"
    bl_label = "Clear Recording"

    def execute(self, _context):
        recorder.clear()
        return {'FINISHED'}


class WBP_OT_depsgraph_reevaluate(Operator):
    """Tag a data-block as changed and evaluate the dependency graph again, reporting how long it took"""
    bl_idname = "wbp.depsgraph_reevaluate"
    bl_label = "Re-evaluate"
    bl_options = {'REGISTER', 'INTERNAL'}

    id_type: StringProperty(name="ID Type", description="Type of the data-block, empty for the active object")
    id_name: StringProperty(name="Name", description="Name of the data-block, empty for the active object")

    def execute(self, context):
        if self.id_type and self.id_name:
            id_data = relations.id_find(self.id_type, self.id_name)
        else:
            id_data = context.active_object
        if id_data is None:
            self.report({'ERROR'}, "Data-block not found")
            return {'CANCELLED'}

        if isinstance(id_data, bpy.types.Object):
            id_data.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
        else:
            id_data.update_tag()
        time_start = time.perf_counter()
        context.view_layer.update()
        duration = time.perf_counter() - time_start
        self.report({'INFO'}, "Re-evaluated {:s} in {:.2f} ms".format(id_data.name, duration * 1000.0))
        return {'FINISHED'}


class WBP_OT_depsgraph_profile(Operator):
    """Step through a frame range and record how long each frame takes to evaluate"""
    bl_idname = "wbp.depsgraph_profile"
    bl_label = "Profile Frames"

    frame_start: IntProperty(name="Start", description="First frame, -1 uses the scene start", default=-1)
    frame_end: IntProperty(name="End", description="Last frame, -1 uses the scene end", default=-1)
    use_clear: BoolProperty(name="Clear", description="Clear the previous recording first", default=True)

    def execute(self, context):
        scene = context.scene
        frame_start = scene.frame_start if self.frame_start == -1 else self.frame_start
        frame_end = scene.frame_end if self.frame_end == -1 else self.frame_end
        if frame_end < frame_start:
            self.report({'ERROR'}, "The end frame is before the start frame")
            return {'CANCELLED'}

        frame_current = scene.frame_current
        was_recording = is_recording()
        if self.use_clear:
            recorder.clear()
        recording_set(True)
        try:
            for frame in range(frame_start, frame_end + 1):
                scene.frame_set(frame)
        finally:
            # Restoring the frame is not part of the profile.
            recording_set(False)
            scene.frame_set(frame_current)
            recording_set(was_recording)

        frames = [record for record in recorder.records if record.kind == 'FRAME']
        total = sum(record.duration for record in frames)
        self.report({'INFO'}, "Profiled {:d} frames: {:.2f} ms in total, {:.2f} ms per frame".format(
            len(frames), total * 1000.0, total * 1000.0 / max(len(frames), 1)))
        return {'FINISHED'}


class WBP_OT_depsgraph_export(Operator, ExportHelper):
    """Save the recorded evaluations as JSON, or as a Chrome trace to open in Perfetto"""
    bl_idname = "wbp.depsgraph_export"
    bl_label = "Export Recording"

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    format: EnumProperty(
        name="Format",
        description="File format",
        items=(
            ('CHROME_TRACE', "Chrome Trace", "Trace event format, opens in Perfetto (ui.perfetto.dev)"),
            ('JSON', "JSON", "Plain list of the recorded evaluations"),
        ),
        default='CHROME_TRACE',
    )

    @classmethod
    def poll(cls, _context):
        return bool(recorder.records)

    def execute(self, _context):
        data = recorder.as_chrome_trace() if self.format == 'CHROME_TRACE' else recorder.as_dict()
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        self.report({'INFO'}, "Saved {:d} evaluations to {:s}".format(len(recorder.records), self.filepath))
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Panels

class _SidebarPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Workbench"


def _settings(context):
    return context.window_manager.wbp_depsgraph_inspector


def _draw_updates(layout, updates, show_unchanged):
    col = layout.column(align=True)
    shown = 0
    for update in updates:
        flags = "".join(flag for flag, value in (
            ("T", update.is_updated_transform),
            ("G", update.is_updated_geometry),
            ("S", update.is_updated_shading),
        ) if value)
        if not flags and not show_unchanged:
            continue
        row = col.row(align=True)
        row.label(text=update.name, icon=id_type_icon(update.id_type))
        row.label(text=flags or "-")
        shown += 1
        if shown == UPDATES_SHOWN_MAX:
            break
    hidden = len(updates) - shown
    if hidden > 0 and shown == UPDATES_SHOWN_MAX:
        col.label(text="{:d} more".format(hidden))
    if not updates:
        col.label(text="Nothing updated")


class WBP_PT_depsgraph_inspector(_SidebarPanel, Panel):
    bl_label = "Depsgraph Inspector"

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)

        row = layout.row(align=True)
        recording = is_recording()
        row.operator(WBP_OT_depsgraph_record.bl_idname, text="Stop" if recording else "Record",
                     icon='PAUSE' if recording else 'REC', depress=recording).state = not recording
        row.operator(WBP_OT_depsgraph_clear.bl_idname, text="", icon='TRASH')

        last = recorder.last
        if last is None:
            layout.label(text="No evaluations recorded")
            return
        layout.label(text="{:s}: {:.2f} ms, {:d} updated".format(
            last.label, last.duration * 1000.0, len(last.updates)))
        layout.prop(settings, "show_unchanged")
        _draw_updates(layout.box(), last.updates, settings.show_unchanged)
        layout.label(text="T: transform, G: geometry, S: shading")


class WBP_PT_depsgraph_relations(_SidebarPanel, Panel):
    bl_label = "Relations"
    bl_parent_id = "WBP_PT_depsgraph_inspector"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def draw(self, context):
        layout = self.layout
        ob = context.active_object
        layout.operator(WBP_OT_depsgraph_reevaluate.bl_idname, text="Re-evaluate " + ob.name, icon='FILE_REFRESH')

        uses, used_by = relations.relations(ob)
        for title, ids in (("Uses", uses), ("Used By", used_by)):
            layout.label(text=title)
            col = layout.box().column(align=True)
            if not ids:
                col.label(text="Nothing")
            for id_data in ids:
                row = col.row(align=True)
                row.label(text=id_data.name, icon=id_type_icon(id_data.id_type))
                props = row.operator(WBP_OT_depsgraph_reevaluate.bl_idname, text="", icon='FILE_REFRESH')
                props.id_type = id_data.id_type
                props.id_name = id_data.name


class WBP_PT_depsgraph_profile(_SidebarPanel, Panel):
    bl_label = "Profile"
    bl_parent_id = "WBP_PT_depsgraph_inspector"

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)
        scene = context.scene

        layout.operator(WBP_OT_depsgraph_profile.bl_idname, icon='TIME',
                        text="Profile Frames {:d}-{:d}".format(scene.frame_start, scene.frame_end))
        layout.operator(WBP_OT_depsgraph_export.bl_idname, icon='EXPORT')
        if not recorder.records:
            return

        layout.prop(settings, "sort", expand=True)
        col = layout.box().column(align=True)
        for record in recorder.sorted_records(settings.sort)[:RECORDS_SHOWN_MAX]:
            row = col.row(align=True)
            row.label(text=record.label)
            row.label(text="{:.2f} ms".format(record.duration * 1000.0))
            row.label(text="{:d} IDs".format(len(record.updates)))

        layout.label(text="Most Expensive Data")
        col = layout.box().column(align=True)
        for id_type, name, count, seconds in recorder.id_costs()[:RECORDS_SHOWN_MAX]:
            row = col.row(align=True)
            row.label(text=name, icon=id_type_icon(id_type))
            row.label(text="{:.2f} ms in {:d}".format(seconds * 1000.0, count))


_classes = (
    DepsgraphInspectorSettings,
    WBP_OT_depsgraph_record,
    WBP_OT_depsgraph_clear,
    WBP_OT_depsgraph_reevaluate,
    WBP_OT_depsgraph_profile,
    WBP_OT_depsgraph_export,
    WBP_PT_depsgraph_inspector,
    WBP_PT_depsgraph_relations,
    WBP_PT_depsgraph_profile,
)


def register():
    classes_register(_classes)
    bpy.types.WindowManager.wbp_depsgraph_inspector = bpy.props.PointerProperty(type=DepsgraphInspectorSettings)


def unregister():
    recording_set(False)
    recorder.clear()
    del bpy.types.WindowManager.wbp_depsgraph_inspector
    classes_unregister(_classes)
