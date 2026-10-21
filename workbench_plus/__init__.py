# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Workflow and pipeline tools for Blender.

Each feature lives in its own sub-module with its own ``register()``/``unregister()``,
so it can be enabled or disabled independently from the add-on preferences.
"""

import importlib

import bpy
from bpy.props import BoolProperty
from bpy.types import AddonPreferences

# Sub-module name, label and description of every feature, in display order.
# Each one has a matching `use_<name>` toggle in the add-on preferences.
FEATURES = (
    ("command_server", "Command Server",
     "Remote control server for scripts, pipelines and external tools"),
    ("depsgraph_inspector", "Depsgraph Inspector",
     "Inspect what the dependency graph re-evaluates, why and how long it takes"),
    ("quick_attributes", "Quick Attributes",
     "Edit the channels of many selected objects at once"),
    ("shelves", "Shelves",
     "Shareable button bars for scripts and operators"),
    ("script_editor", "Script Editor",
     "Show the Python commands behind UI actions"),
    ("driven_keys", "Driven Keys",
     "Create driver relationships by posing instead of writing expressions"),
)

FEATURE_NAMES = tuple(name for name, _label, _description in FEATURES)

# Names of the features whose sub-module is currently registered.
_features_registered = set()


def _feature_module(name):
    return importlib.import_module("." + name, __package__)


def feature_is_registered(name):
    """Return True when the feature sub-module ``name`` is registered."""
    return name in _features_registered


def feature_register(name):
    """Register the feature sub-module ``name``, doing nothing when it is already registered."""
    if name in _features_registered:
        return
    _feature_module(name).register()
    _features_registered.add(name)


def feature_unregister(name):
    """Unregister the feature sub-module ``name``, doing nothing when it is not registered."""
    if name not in _features_registered:
        return
    _feature_module(name).unregister()
    _features_registered.discard(name)


def _feature_toggle(name, label, description):
    def update(self, _context):
        if getattr(self, "use_" + name):
            feature_register(name)
        else:
            feature_unregister(name)

    return BoolProperty(
        name=label,
        description=description,
        default=True,
        update=update,
    )


class WorkbenchPlusPreferences(AddonPreferences):
    bl_idname = __package__

    use_command_server: _feature_toggle(*FEATURES[0])
    use_depsgraph_inspector: _feature_toggle(*FEATURES[1])
    use_quick_attributes: _feature_toggle(*FEATURES[2])
    use_shelves: _feature_toggle(*FEATURES[3])
    use_script_editor: _feature_toggle(*FEATURES[4])
    use_driven_keys: _feature_toggle(*FEATURES[5])

    def draw(self, _context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(heading="Features", align=True)
        for name in FEATURE_NAMES:
            col.prop(self, "use_" + name)


def _preferences():
    # The add-on is only listed in the preferences when enabled with `default_set`,
    # e.g. not when enabled from a script for the current session only.
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def register():
    bpy.utils.register_class(WorkbenchPlusPreferences)

    prefs = _preferences()
    for name in FEATURE_NAMES:
        if prefs is None or getattr(prefs, "use_" + name):
            feature_register(name)


def unregister():
    for name in reversed(FEATURE_NAMES):
        feature_unregister(name)

    bpy.utils.unregister_class(WorkbenchPlusPreferences)
