# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Quick Attributes: fast channel editing across many selected objects.

A compact panel (3D Viewport sidebar, *Workbench* tab) with the transform channels of the active object.
Editing a value sets it on every selected object; clicking a channel name starts a virtual slider that
offsets the channel of every selected object with the mouse. Locked channels are left untouched.
"""

__all__ = (
    "CHANNELS",
    "channel_get",
    "channel_is_locked",
    "channels_offset",
    "channels_set",
    "custom_properties",
    "register",
    "slider_delta",
    "unregister",
)

import math
from typing import NamedTuple

import bpy
from bpy.props import EnumProperty, FloatProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from bpy_extras import anim_utils


class Channel(NamedTuple):
    id: str
    """``"<data_path>.<index>"``, also the name of its virtual property (with ``_`` instead of ``.``)."""
    data_path: str
    index: int
    lock_path: str
    group: str
    label: str
    kind: str
    """``'LOCATION'``, ``'ROTATION'`` or ``'SCALE'``: units and slider speed."""


def _channels():
    channels = []
    for data_path, lock_path, group, kind in (
            ("location", "lock_location", "Location", 'LOCATION'),
            ("rotation_euler", "lock_rotation", "Rotation", 'ROTATION'),
            ("scale", "lock_scale", "Scale", 'SCALE'),
    ):
        for index, axis in enumerate("XYZ"):
            channels.append(Channel(
                "{:s}.{:d}".format(data_path, index), data_path, index, lock_path, group, axis, kind))
    return tuple(channels)


CHANNELS = _channels()
_CHANNELS_BY_ID = {channel.id: channel for channel in CHANNELS}

# Slider change per pixel of mouse motion, and the step used while holding Ctrl.
_SLIDER_SPEED = {'LOCATION': 0.01, 'ROTATION': math.radians(0.5), 'SCALE': 0.01}
_SLIDER_STEP = {'LOCATION': 0.1, 'ROTATION': math.radians(5.0), 'SCALE': 0.1}


def slider_delta(kind, pixels, *, fine=False, steps=False):
    """The change of a channel of ``kind`` after moving the slider by ``pixels``."""
    delta = pixels * _SLIDER_SPEED[kind]
    if fine:
        delta *= 0.1
    if steps:
        step = _SLIDER_STEP[kind]
        delta = round(delta / step) * step
    return delta


def channel_from_id(channel_id):
    channel = _CHANNELS_BY_ID.get(channel_id)
    if channel is None:
        raise KeyError("Unknown channel {!r}".format(channel_id))
    return channel


# -----------------------------------------------------------------------------
# Channel access

def channel_get(ob, channel):
    return getattr(ob, channel.data_path)[channel.index]


def channel_is_locked(ob, channel):
    return getattr(ob, channel.lock_path)[channel.index]


def _targets(context):
    """The selected objects, plus the active object even when it is not selected."""
    objects = list(context.selected_objects)
    active = context.active_object
    if active is not None and active not in objects:
        objects.append(active)
    return objects


def channels_set(context, channel, value):
    """Set ``channel`` of every target object (selected + active) to ``value``, skipping locked ones."""
    for ob in _targets(context):
        if not channel_is_locked(ob, channel):
            getattr(ob, channel.data_path)[channel.index] = value


def channels_offset(context, channel, delta, initial=None):
    """
    Add ``delta`` to ``channel`` of every target object, skipping locked ones.

    :arg initial: ``{object_name: value}`` to offset from (for sliders), instead of the current values.
    """
    for ob in _targets(context):
        if channel_is_locked(ob, channel):
            continue
        base = initial[ob.name] if initial is not None else channel_get(ob, channel)
        getattr(ob, channel.data_path)[channel.index] = base + delta


# -----------------------------------------------------------------------------
# Virtual properties: show the active object's value, set every selected object.

def _virtual_property(channel):
    def get(_self):
        ob = bpy.context.active_object
        return channel_get(ob, channel) if ob is not None else 0.0

    def set(_self, value):
        channels_set(bpy.context, channel, value)

    subtype, unit = {
        'LOCATION': ('DISTANCE', 'LENGTH'),
        'ROTATION': ('ANGLE', 'ROTATION'),
        'SCALE': ('NONE', 'NONE'),
    }[channel.kind]
    return FloatProperty(
        name="{:s} {:s}".format(channel.group, channel.label),
        description="{:s} {:s} of the active object. Editing it sets every selected object "
                    "(locked channels are skipped)".format(channel.group, channel.label),
        get=get,
        set=set,
        subtype=subtype,
        unit=unit,
        step=10 if channel.kind == 'ROTATION' else 1,
        precision=3,
    )


def _virtual_property_name(channel):
    return channel.id.replace(".", "_")


class QuickAttributesProperties(PropertyGroup):
    # Stored on the scene (not the window manager) so edits from the interface push undo steps.
    __annotations__ = {_virtual_property_name(channel): _virtual_property(channel) for channel in CHANNELS}


# -----------------------------------------------------------------------------
# Operators

def _channel_enum_items():
    return [(channel.id, "{:s} {:s}".format(channel.group, channel.label), "") for channel in CHANNELS]


class WBP_OT_quick_attributes_slide(Operator):
    """Click, then move the mouse to change this channel on every selected object (Shift: finer, Ctrl: steps)"""
    bl_idname = "wbp.quick_attributes_slide"
    bl_label = "Channel Slider"
    bl_options = {'REGISTER', 'UNDO', 'GRAB_CURSOR_X', 'BLOCKING'}

    channel: EnumProperty(name="Channel", description="Channel to change", items=_channel_enum_items())
    delta: FloatProperty(name="Delta", description="Amount added to the channel of every selected object")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        channels_offset(context, channel_from_id(self.channel), self.delta)
        return {'FINISHED'}

    def invoke(self, context, event):
        channel = channel_from_id(self.channel)
        self._initial = {ob.name: channel_get(ob, channel) for ob in _targets(context)}
        self._mouse_x = event.mouse_x
        context.window_manager.modal_handler_add(self)
        self._header_update(context)
        return {'RUNNING_MODAL'}

    def _delta_from_event(self, event):
        channel = channel_from_id(self.channel)
        return slider_delta(channel.kind, event.mouse_x - self._mouse_x, fine=event.shift, steps=event.ctrl)

    def _header_update(self, context):
        channel = channel_from_id(self.channel)
        delta = math.degrees(self.delta) if channel.kind == 'ROTATION' else self.delta
        context.workspace.status_text_set("{:s} {:s}: {:+.3f}   Click: confirm   Esc/Right-click: cancel".format(
            channel.group, channel.label, delta))

    def _restore(self, context):
        channel = channel_from_id(self.channel)
        for ob in _targets(context):
            if ob.name in self._initial:
                getattr(ob, channel.data_path)[channel.index] = self._initial[ob.name]

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self.delta = self._delta_from_event(event)
            channels_offset(context, channel_from_id(self.channel), self.delta, self._initial)
            self._header_update(context)
            return {'RUNNING_MODAL'}
        if event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            context.workspace.status_text_set(None)
            return {'FINISHED'}
        if event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            self._restore(context)
            context.workspace.status_text_set(None)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


def _fcurve_find(ob, channel):
    animation_data = ob.animation_data
    if animation_data is None or animation_data.action is None:
        return None
    channelbag = anim_utils.action_get_channelbag_for_slot(animation_data.action, animation_data.action_slot)
    if channelbag is None:
        return None
    return channelbag.fcurves.find(channel.data_path, index=channel.index)


class WBP_OT_quick_attributes_channel(Operator):
    """Key, lock or mute this channel on every selected object"""
    bl_idname = "wbp.quick_attributes_channel"
    bl_label = "Channel Action"
    bl_options = {'REGISTER', 'UNDO'}

    channel: EnumProperty(name="Channel", description="Channel to act on", items=_channel_enum_items())
    action: EnumProperty(
        name="Action",
        description="What to do with the channel",
        items=(
            ('KEY', "Key", "Insert a keyframe on the current frame"),
            ('BREAKDOWN', "Breakdown", "Insert a breakdown keyframe on the current frame"),
            ('LOCK', "Lock", "Lock the channel, so it cannot be changed"),
            ('UNLOCK', "Unlock", "Unlock the channel"),
            ('MUTE', "Mute", "Mute the animation of the channel"),
            ('UNMUTE', "Unmute", "Use the animation of the channel again"),
        ),
    )

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        channel = channel_from_id(self.channel)
        count = 0
        for ob in _targets(context):
            if self.action in {'KEY', 'BREAKDOWN'}:
                keytype = 'BREAKDOWN' if self.action == 'BREAKDOWN' else 'KEYFRAME'
                count += ob.keyframe_insert(channel.data_path, index=channel.index, keytype=keytype)
            elif self.action in {'LOCK', 'UNLOCK'}:
                getattr(ob, channel.lock_path)[channel.index] = self.action == 'LOCK'
                count += 1
            else:
                fcurve = _fcurve_find(ob, channel)
                if fcurve is not None:
                    fcurve.mute = self.action == 'MUTE'
                    count += 1
        if count == 0:
            self.report({'WARNING'}, "No channel changed")
            return {'CANCELLED'}
        return {'FINISHED'}


class WBP_OT_quick_attributes_channel_menu(Operator):
    """Key, lock or mute this channel"""
    bl_idname = "wbp.quick_attributes_channel_menu"
    bl_label = "Channel Menu"
    bl_options = {'INTERNAL'}

    channel: EnumProperty(name="Channel", description="Channel of the menu", items=_channel_enum_items())

    def execute(self, context):
        channel = channel_from_id(self.channel)

        def draw(menu, _context):
            layout = menu.layout
            for action, text, icon in (
                    ('KEY', "Key", 'KEYFRAME_HLT'),
                    ('BREAKDOWN', "Key Breakdown", 'KEYTYPE_BREAKDOWN_VEC'),
                    (None, None, None),
                    ('LOCK', "Lock", 'LOCKED'),
                    ('UNLOCK', "Unlock", 'UNLOCKED'),
                    (None, None, None),
                    ('MUTE', "Mute", 'CHECKBOX_DEHLT'),
                    ('UNMUTE', "Unmute", 'CHECKBOX_HLT'),
            ):
                if action is None:
                    layout.separator()
                    continue
                props = layout.operator(WBP_OT_quick_attributes_channel.bl_idname, text=text, icon=icon)
                props.channel = channel.id
                props.action = action

        context.window_manager.popup_menu(draw, title="{:s} {:s}".format(channel.group, channel.label))
        return {'FINISHED'}


class WBP_OT_quick_attributes_copy_property(Operator):
    """Copy this custom property of the active object to every selected object"""
    bl_idname = "wbp.quick_attributes_copy_property"
    bl_label = "Copy to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    name: StringProperty(name="Name", description="Name of the custom property")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        active = context.active_object
        if self.name not in active.keys():
            self.report({'ERROR'}, "The active object has no property {!r}".format(self.name))
            return {'CANCELLED'}
        value = active[self.name]
        for ob in context.selected_objects:
            if ob != active:
                ob[self.name] = value
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Panel

def custom_properties(ob):
    """Names of the numeric custom properties of ``ob`` (the ones that can be animated), sorted."""
    return sorted(
        name for name in ob.keys()
        if isinstance(ob[name], (int, float)) and not isinstance(ob[name], bool) and not name.startswith("_")
    )


class WBP_PT_quick_attributes(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Workbench"
    bl_label = "Quick Attributes"

    def draw(self, context):
        layout = self.layout
        ob = context.active_object
        if ob is None:
            layout.label(text="No active object")
            return

        selected = len(_targets(context))
        layout.label(text=ob.name if selected == 1 else "{:s} (+{:d} selected)".format(ob.name, selected - 1),
                     icon='OBJECT_DATA')
        props = context.scene.wbp_quick_attributes

        col = layout.column(align=True)
        group = None
        for channel in CHANNELS:
            if channel.group != group:
                group = channel.group
                if channel.kind == 'ROTATION' and ob.rotation_mode in {'QUATERNION', 'AXIS_ANGLE'}:
                    col.label(text=group + " (Euler, not used by this object)")
                else:
                    col.label(text=group)
            row = col.row(align=True)
            slider = row.operator(WBP_OT_quick_attributes_slide.bl_idname, text=channel.label, emboss=False)
            slider.channel = channel.id
            row.prop(props, _virtual_property_name(channel), text="")
            locked = channel_is_locked(ob, channel)
            lock = row.operator(WBP_OT_quick_attributes_channel.bl_idname, text="",
                                icon='LOCKED' if locked else 'UNLOCKED', emboss=False)
            lock.channel = channel.id
            lock.action = 'UNLOCK' if locked else 'LOCK'
            row.operator(WBP_OT_quick_attributes_channel_menu.bl_idname, text="",
                         icon='DOWNARROW_HLT', emboss=False).channel = channel.id

        names = custom_properties(ob)
        if names:
            col = layout.column(align=True)
            col.label(text="Custom Properties")
            for name in names:
                row = col.row(align=True)
                row.prop(ob, '["{:s}"]'.format(bpy.utils.escape_identifier(name)), text=name)
                row.operator(WBP_OT_quick_attributes_copy_property.bl_idname, text="",
                             icon='PASTEDOWN', emboss=False).name = name


_classes = (
    QuickAttributesProperties,
    WBP_OT_quick_attributes_slide,
    WBP_OT_quick_attributes_channel,
    WBP_OT_quick_attributes_channel_menu,
    WBP_OT_quick_attributes_copy_property,
    WBP_PT_quick_attributes,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.wbp_quick_attributes = PointerProperty(type=QuickAttributesProperties)


def unregister():
    del bpy.types.Scene.wbp_quick_attributes
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
