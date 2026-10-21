# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Data-block level relations: which data-blocks an ID uses, and which ones use it.

These references (parent, constraint and modifier targets, driver variables, actions, materials...)
are what the dependency graph builds its relations from. Python has no access to the finer,
per-component relations of the graph itself.
"""

__all__ = (
    "id_find",
    "relations",
)

import bpy


def relations(id_data):
    """
    Return ``(uses, used_by)``: sorted lists of the IDs that ``id_data`` references, and of the IDs
    that reference it.
    """
    user_map = bpy.data.user_map()
    used_by = user_map.get(id_data, set())
    uses = [other for other, users in user_map.items() if id_data in users and other != id_data]

    def key(other):
        return (other.id_type, other.name)

    return sorted(uses, key=key), sorted((other for other in used_by if other != id_data), key=key)


def id_find(id_type, name):
    """Return the local data-block of type ``id_type`` (e.g. ``'OBJECT'``) named ``name``, or None."""
    for attr in dir(bpy.data):
        collection = getattr(bpy.data, attr, None)
        if not isinstance(collection, bpy.types.bpy_prop_collection):
            continue
        id_data = collection.get(name)
        if isinstance(id_data, bpy.types.ID) and id_data.id_type == id_type and id_data.library is None:
            return id_data
    return None
