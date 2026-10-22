# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Driven key relationships: a driven channel follows a driver channel through an F-curve whose keyframes map
driver values (X) to driven values (Y). Independent of the interface.

A relationship is a regular Blender driver with a single variable named :data:`VARIABLE_NAME`.
"""

__all__ = (
    "Channel",
    "VARIABLE_NAME",
    "key_insert",
    "relationship_mute",
    "relationship_remove",
    "relationships",
)

from typing import NamedTuple

import bpy

VARIABLE_NAME = "driven_key"


class Channel(NamedTuple):
    """One component of an animatable property, e.g. ``(armature, 'pose.bones["Arm"].rotation_euler', 2)``."""
    id_data: bpy.types.ID
    data_path: str
    index: int

    @property
    def full_path(self):
        return "{:s}[{:d}]".format(self.data_path, self.index)

    @property
    def label(self):
        return "{:s}.{:s}".format(self.id_data.name, self.full_path)

    def value(self):
        return self.id_data.path_resolve(self.data_path)[self.index]


def _driver_fcurve_find(channel):
    animation_data = channel.id_data.animation_data
    if animation_data is None:
        return None
    return animation_data.drivers.find(channel.data_path, index=channel.index)


def _is_driven_key(fcurve):
    variables = fcurve.driver.variables
    return len(variables) == 1 and variables[0].name == VARIABLE_NAME


def _driver_fcurve_ensure(driver, driven):
    """The F-curve of the driven key relationship ``driver`` → ``driven``, created or re-targeted."""
    fcurve = _driver_fcurve_find(driven)
    if fcurve is not None and not _is_driven_key(fcurve):
        raise ValueError("{:s} already has another driver".format(driven.label))
    if fcurve is None:
        fcurve = driven.id_data.driver_add(driven.data_path, driven.index)
        # New driver F-curves come with default keyframes (or a generator modifier in older versions).
        fcurve.keyframe_points.clear()
        for modifier in list(fcurve.modifiers):
            fcurve.modifiers.remove(modifier)
        fcurve.extrapolation = 'CONSTANT'
        variable = fcurve.driver.variables.new()
        variable.name = VARIABLE_NAME
        variable.type = 'SINGLE_PROP'
        fcurve.driver.type = 'AVERAGE'

    target = fcurve.driver.variables[0].targets[0]
    target.id_type = driver.id_data.id_type
    target.id = driver.id_data
    target.data_path = driver.full_path
    return fcurve


def key_insert(driver, driven_channels):
    """
    Key the current pose: for every driven channel, add a keyframe at X = the driver value with
    Y = the driven value (replacing a keyframe at the same X). Returns the driver value.
    """
    x = driver.value()
    for driven in driven_channels:
        if driven.id_data == driver.id_data and driven.full_path == driver.full_path:
            raise ValueError("{:s} cannot drive itself".format(driven.label))
    for driven in driven_channels:
        y = driven.value()
        fcurve = _driver_fcurve_ensure(driver, driven)
        # Replaces the keyframe at the same X, if any (the 'REPLACE' option would only replace, never add).
        fcurve.keyframe_points.insert(x, y)
        fcurve.update()
    return x


def relationships():
    """
    Every driven key relationship in the file, as ``(driven Channel, driver ID, driver path, fcurve)``,
    sorted by driven label.
    """
    found = []
    for collection in (bpy.data.objects, bpy.data.armatures, bpy.data.meshes, bpy.data.shape_keys):
        for id_data in collection:
            if id_data.library is not None or id_data.animation_data is None:
                continue
            for fcurve in id_data.animation_data.drivers:
                if not _is_driven_key(fcurve):
                    continue
                target = fcurve.driver.variables[0].targets[0]
                found.append((Channel(id_data, fcurve.data_path, fcurve.array_index), target.id,
                              target.data_path, fcurve))
    return sorted(found, key=lambda item: item[0].label)


def relationship_remove(driven):
    """Remove the driven key relationship of ``driven`` (the driven channel keeps its current value)."""
    fcurve = _driver_fcurve_find(driven)
    if fcurve is None or not _is_driven_key(fcurve):
        return False
    value = driven.value()
    driven.id_data.driver_remove(driven.data_path, driven.index)
    driven.id_data.path_resolve(driven.data_path)[driven.index] = value
    return True


def relationship_mute(driven, mute):
    fcurve = _driver_fcurve_find(driven)
    if fcurve is not None and _is_driven_key(fcurve):
        fcurve.mute = mute
