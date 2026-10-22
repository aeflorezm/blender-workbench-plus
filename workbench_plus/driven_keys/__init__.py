# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Driven Keys: create driver relationships by posing.

Pick a driver channel (on the active object, or the active bone in Pose Mode) and driven channels
(on the other selected objects or bones), then pose and *Key*: every key maps the driver value to the
driven values. Panel in the 3D Viewport sidebar, *Workbench* tab.
"""

__all__ = (
    "register",
    "unregister",
)

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup

from ..utils import classes_register, classes_unregister
from . import relations
from .relations import Channel

_TRANSFORM_PROPERTIES = (("location", "Location"), ("rotation_euler", "Rotation"), ("scale", "Scale"))

# `(identifier, label, description)` of the channels that can be loaded, e.g. `("location.0", "Location X")`.
CHANNEL_ITEMS = tuple(
    ("{:s}.{:d}".format(attr, index), "{:s} {:s}".format(label, axis), "{:s} {:s}".format(label, axis))
    for attr, label in _TRANSFORM_PROPERTIES
    for index, axis in enumerate("XYZ")
)


def _channel_split(identifier):
    attr, _, index = identifier.partition(".")
    return attr, int(index)


def _sources(context, *, driver):
    """
    ``[(object, path prefix, name)]``: the active (``driver=True``) or the other selected (``driver=False``)
    objects, or bones in Pose Mode.
    """
    if context.mode == 'POSE':
        bones = [context.active_pose_bone] if driver else [
            bone for bone in context.selected_pose_bones or () if bone != context.active_pose_bone]
        return [(bone.id_data, 'pose.bones["{:s}"].'.format(bpy.utils.escape_identifier(bone.name)), bone.name)
                for bone in bones if bone is not None]
    if driver:
        return [(context.active_object, "", context.active_object.name)] if context.active_object else []
    return [(ob, "", ob.name) for ob in context.selected_objects if ob != context.active_object]


class DrivenChannel(PropertyGroup):
    target: PointerProperty(name="Object", type=bpy.types.Object, description="Object owning the driven channel")
    data_path: StringProperty(name="Path", description="Path of the driven property, from the object")
    index: IntProperty(name="Index", description="Component of the driven property")


def _driven_channels(settings):
    return [Channel(item.target, item.data_path, item.index) for item in settings.driven if item.target]


def _driver_channel(settings):
    return Channel(settings.driver_target, settings.driver_path, settings.driver_index) \
        if settings.driver_target is not None else None


def _use_posing_update(self, _context):
    for channel in _driven_channels(self):
        relations.relationship_mute(channel, self.use_posing)


class DrivenKeysSettings(PropertyGroup):
    driver_channel: EnumProperty(name="Driver Channel", description="Channel of the driver to load",
                                 items=CHANNEL_ITEMS)
    driven_channels: EnumProperty(name="Driven Channels", description="Channels of the driven objects to load",
                                  items=CHANNEL_ITEMS, options={'ENUM_FLAG'})
    driver_target: PointerProperty(name="Driver", type=bpy.types.Object, description="Object owning the driver")
    driver_path: StringProperty(name="Driver Path", description="Path of the driver property, from the object")
    driver_index: IntProperty(name="Driver Index", description="Component of the driver property")
    driven: CollectionProperty(type=DrivenChannel)
    use_posing: BoolProperty(
        name="Posing",
        description="Mute the loaded relationships, so driven channels can be posed before keying. "
                    "Turn off to see the driver move them",
        update=_use_posing_update,
    )


def _settings(context):
    return context.scene.wbp_driven_keys


# -----------------------------------------------------------------------------
# Operators

class WBP_OT_driven_keys_load_driver(Operator):
    """Use the chosen channel of the active object (or active bone in Pose Mode) as the driver"""
    bl_idname = "wbp.driven_keys_load_driver"
    bl_label = "Load Driver"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context)
        sources = _sources(context, driver=True)
        if not sources:
            self.report({'ERROR'}, "No active object or bone")
            return {'CANCELLED'}
        target, prefix, _name = sources[0]
        attr, index = _channel_split(settings.driver_channel)
        settings.driver_target = target
        settings.driver_path = prefix + attr
        settings.driver_index = index
        return {'FINISHED'}


class WBP_OT_driven_keys_load_driven(Operator):
    """Use the chosen channels of the other selected objects (or bones in Pose Mode) as the driven channels"""
    bl_idname = "wbp.driven_keys_load_driven"
    bl_label = "Load Driven"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context)
        sources = _sources(context, driver=False)
        if not sources or not settings.driven_channels:
            self.report({'ERROR'}, "Select the driven objects (besides the active one) and at least one channel")
            return {'CANCELLED'}
        settings.use_posing = False
        settings.driven.clear()
        for target, prefix, _name in sources:
            for identifier, _label, _description in CHANNEL_ITEMS:
                if identifier in settings.driven_channels:
                    attr, index = _channel_split(identifier)
                    item = settings.driven.add()
                    item.target, item.data_path, item.index = target, prefix + attr, index
        return {'FINISHED'}


class WBP_OT_driven_keys_key(Operator):
    """Key the current pose: map the current driver value to the current values of the driven channels"""
    bl_idname = "wbp.driven_keys_key"
    bl_label = "Key"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return settings.driver_target is not None and len(settings.driven) > 0

    def execute(self, context):
        settings = _settings(context)
        driver = _driver_channel(settings)
        driven = _driven_channels(settings)
        try:
            value = relations.key_insert(driver, driven)
        except (ValueError, KeyError, IndexError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        for channel in driven:
            relations.relationship_mute(channel, settings.use_posing)
        self.report({'INFO'}, "Keyed {:d} channels at {:s} = {:.3f}".format(len(driven), driver.label, value))
        return {'FINISHED'}


class _RelationshipOperator:
    label: StringProperty(name="Driven", description="Label of the driven channel of the relationship")

    def _find(self):
        for driven, driver_id, driver_path, fcurve in relations.relationships():
            if driven.label == self.label:
                return driven, driver_id, driver_path, fcurve
        return None


class WBP_OT_driven_keys_relationship_load(_RelationshipOperator, Operator):
    """Load this relationship to add or change its keys"""
    bl_idname = "wbp.driven_keys_relationship_load"
    bl_label = "Load Relationship"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, context):
        found = self._find()
        if found is None or not isinstance(found[0].id_data, bpy.types.Object) or not isinstance(
                found[1], bpy.types.Object):
            self.report({'ERROR'}, "Relationship not found, or not between objects")
            return {'CANCELLED'}
        driven, driver_id, driver_path, _fcurve = found
        settings = _settings(context)
        settings.use_posing = False
        data_path, _, index = driver_path.rpartition("[")
        settings.driver_target = driver_id
        settings.driver_path = data_path
        settings.driver_index = int(index.rstrip("]"))
        settings.driven.clear()
        item = settings.driven.add()
        item.target, item.data_path, item.index = driven
        return {'FINISHED'}


class WBP_OT_driven_keys_relationship_remove(_RelationshipOperator, Operator):
    """Remove this relationship (the driven channel keeps its current value)"""
    bl_idname = "wbp.driven_keys_relationship_remove"
    bl_label = "Remove Relationship"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    def execute(self, _context):
        found = self._find()
        if found is None:
            self.report({'ERROR'}, "Relationship not found")
            return {'CANCELLED'}
        relations.relationship_remove(found[0])
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Panels

class _SidebarPanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Workbench"


class WBP_PT_driven_keys(_SidebarPanel, Panel):
    bl_label = "Driven Keys"

    def draw(self, context):
        layout = self.layout
        settings = _settings(context)

        box = layout.box()
        row = box.row(align=True)
        row.prop(settings, "driver_channel", text="")
        row.operator(WBP_OT_driven_keys_load_driver.bl_idname, text="Load Driver")
        driver = _driver_channel(settings)
        if driver is not None:
            try:
                box.label(text="{:s} = {:.3f}".format(driver.label, driver.value()), icon='DRIVER')
            except (ValueError, IndexError):
                box.label(text="{:s} (missing)".format(driver.label), icon='ERROR')

        box = layout.box()
        grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        grid.prop(settings, "driven_channels")
        box.operator(WBP_OT_driven_keys_load_driven.bl_idname)
        col = box.column(align=True)
        for channel in _driven_channels(settings):
            col.label(text=channel.label, icon='DRIVER_TRANSFORM')

        row = layout.row(align=True)
        row.prop(settings, "use_posing", toggle=True, icon='POSE_HLT')
        row.operator(WBP_OT_driven_keys_key.bl_idname, icon='KEYFRAME_HLT')


class WBP_PT_driven_keys_relationships(_SidebarPanel, Panel):
    bl_label = "Relationships"
    bl_parent_id = "WBP_PT_driven_keys"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, _context):
        layout = self.layout
        found = relations.relationships()
        if not found:
            layout.label(text="No driven keys in this file")
        for driven, driver_id, driver_path, fcurve in found:
            # Two lines per relationship: the sidebar is usually too narrow for both channels.
            col = layout.box().column(align=True)
            row = col.row(align=True)
            row.label(text=driven.label, icon='DRIVER_TRANSFORM')
            row.operator(WBP_OT_driven_keys_relationship_load.bl_idname, text="", icon='IMPORT').label = driven.label
            row.operator(WBP_OT_driven_keys_relationship_remove.bl_idname, text="", icon='X').label = driven.label
            col.label(text="← {:s}.{:s} ({:d} keys)".format(
                driver_id.name if driver_id else "?", driver_path, len(fcurve.keyframe_points)))


_classes = (
    DrivenChannel,
    DrivenKeysSettings,
    WBP_OT_driven_keys_load_driver,
    WBP_OT_driven_keys_load_driven,
    WBP_OT_driven_keys_key,
    WBP_OT_driven_keys_relationship_load,
    WBP_OT_driven_keys_relationship_remove,
    WBP_PT_driven_keys,
    WBP_PT_driven_keys_relationships,
)


def register():
    classes_register(_classes)
    bpy.types.Scene.wbp_driven_keys = PointerProperty(type=DrivenKeysSettings)


def unregister():
    del bpy.types.Scene.wbp_driven_keys
    classes_unregister(_classes)
