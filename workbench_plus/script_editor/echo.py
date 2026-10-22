# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Turns what happens in Blender into lines of Python: operators run from the interface (from the reports
Blender keeps for the Info editor) and changes of watched properties (by comparing snapshots).
"""

__all__ = (
    "PropertyWatcher",
    "ReportReader",
    "WATCHED_PROPERTIES",
    "value_to_python",
)

import math

import bpy

# `(type, property, collection in bpy.data)`: the properties whose changes are echoed.
WATCHED_PROPERTIES = (
    (bpy.types.Object, "location", "objects"),
    (bpy.types.Object, "rotation_euler", "objects"),
    (bpy.types.Object, "scale", "objects"),
    (bpy.types.Object, "hide_viewport", "objects"),
    (bpy.types.Object, "hide_render", "objects"),
    (bpy.types.Scene, "frame_current", "scenes"),
)


def _number_to_python(value):
    if isinstance(value, float):
        if not math.isfinite(value):
            return "float({!r})".format(str(value))
        # Enough digits for any practical value, without float noise such as 0.30000000000000004.
        value = round(value, 6)
        return repr(int(value)) + ".0" if value.is_integer() else repr(value)
    return repr(value)


def value_to_python(value):
    """Python source for ``value`` (numbers, strings, booleans and sequences of them)."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return _number_to_python(value) if isinstance(value, float) else repr(value)
    items = [value_to_python(item) for item in value]
    return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"


def _value_snapshot(value):
    """A comparable copy of a property value (``mathutils`` and arrays become tuples)."""
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return tuple(_value_snapshot(item) for item in value)


class ReportReader:
    """
    Reads the reports Blender keeps for the Info editor and returns the new ones as Python lines.

    Operators run from the interface are reported (type ``'OPERATOR'``) as the Python call that repeats them;
    other reports become comments. Reports are identified by their ``session_uid``, which only grows.
    """

    def __init__(self):
        self._uid_last = 0

    def skip_existing(self, reports):
        """Ignore the reports that already exist (when echoing starts)."""
        self._uid_last = max([self._uid_last] + [report.session_uid for report in reports])

    def read(self, reports):
        lines = []
        for report in reports:
            if report.session_uid <= self._uid_last:
                continue
            self._uid_last = report.session_uid
            if report.type == 'OPERATOR':
                lines.append(report.message)
            else:
                lines.append("# {:s}: {:s}".format(report.type.title(), report.message))
        return lines


class PropertyWatcher:
    """
    Remembers the values of :data:`WATCHED_PROPERTIES` and returns assignments for the ones that changed.

    Change notifications (``bpy.msgbus``) do not say which data-block changed, so every data-block
    of the notified type is compared with its last known value.
    """

    def __init__(self):
        self._snapshot = {}

    def _values(self, collection_name, attr):
        for id_data in getattr(bpy.data, collection_name):
            if id_data.library is None:
                yield id_data.name, _value_snapshot(getattr(id_data, attr))

    def reset(self):
        """Remember the current values without reporting them."""
        self._snapshot = {
            (collection_name, attr, name): value
            for _type, attr, collection_name in WATCHED_PROPERTIES
            for name, value in self._values(collection_name, attr)
        }

    def track_new(self):
        """Remember the values of data-blocks created since the last reset, without reporting them."""
        for _type, attr, collection_name in WATCHED_PROPERTIES:
            for name, value in self._values(collection_name, attr):
                self._snapshot.setdefault((collection_name, attr, name), value)

    def check(self, attr, collection_name):
        """Return assignments (Python lines) for the values of ``attr`` that changed since the last check."""
        lines = []
        for name, value in self._values(collection_name, attr):
            key = (collection_name, attr, name)
            if self._snapshot.get(key, value) != value:
                lines.append("bpy.data.{:s}[{:s}].{:s} = {:s}".format(
                    collection_name, '"' + bpy.utils.escape_identifier(name) + '"', attr, value_to_python(value)))
            self._snapshot[key] = value
        return lines

    def check_all(self):
        lines = []
        for _type, attr, collection_name in WATCHED_PROPERTIES:
            lines.extend(self.check(attr, collection_name))
        return lines
