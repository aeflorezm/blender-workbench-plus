# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_registration.py
"""

import os
import sys
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness


class RegistrationTest(unittest.TestCase):
    """The add-on and every feature register and unregister cleanly."""

    def tearDown(self):
        if harness.addon_is_enabled():
            harness.addon_disable()

    def test_enable_disable_twice(self):
        for _ in range(2):
            harness.addon_enable()
            self.assertTrue(harness.addon_is_enabled())
            self.assertIn(harness.ADDON_MODULE, bpy.context.preferences.addons)

            harness.addon_disable()
            self.assertFalse(harness.addon_is_enabled())
            self.assertNotIn(harness.ADDON_MODULE, bpy.context.preferences.addons)

    def test_all_features_registered_by_default(self):
        harness.addon_enable()
        addon = harness.addon_module()
        for name in addon.FEATURE_NAMES:
            with self.subTest(feature=name):
                self.assertTrue(addon.feature_is_registered(name))

    def test_disable_unregisters_features(self):
        harness.addon_enable()
        addon = harness.addon_module()
        harness.addon_disable()
        for name in addon.FEATURE_NAMES:
            with self.subTest(feature=name):
                self.assertFalse(addon.feature_is_registered(name))

    def test_preferences_class_unregistered(self):
        harness.addon_enable()
        cls = harness.addon_module().WorkbenchPlusPreferences
        self.assertTrue(cls.is_registered)
        harness.addon_disable()
        self.assertFalse(cls.is_registered)

    def test_survives_file_load(self):
        harness.addon_enable()
        harness.scene_reset()
        self.assertTrue(harness.addon_is_enabled())


class FailedRegistrationTest(unittest.TestCase):
    """A feature failing to register leaves nothing behind, so the add-on can be enabled again."""

    def test_rollback(self):
        harness.addon_enable()
        addon = harness.addon_module()
        feature = sys.modules[addon.__name__ + ".shelves"]
        harness.addon_disable()

        register = feature.register

        def register_failing():
            raise RuntimeError("Simulated failure")

        feature.register = register_failing
        try:
            with self.assertRaises(RuntimeError):
                addon.register()
        finally:
            feature.register = register

        # Features registered before the failing one were unregistered, and so were the preferences.
        self.assertFalse(addon.feature_is_registered("command_server"))
        self.assertFalse(addon.WorkbenchPlusPreferences.is_registered)
        self.assertFalse(hasattr(bpy.types, "WBP_OT_command_server_start"))

        harness.addon_enable()
        self.assertTrue(harness.addon_module().feature_is_registered("shelves"))
        harness.addon_disable()

    def test_classes_register_rolls_back(self):
        harness.addon_enable()
        utils = sys.modules[harness.ADDON_MODULE + ".utils"]

        class WBP_OT_test_ok(bpy.types.Operator):
            bl_idname = "wbp_test.ok"
            bl_label = "OK"

        class WBP_PT_test_broken(bpy.types.Panel):
            bl_label = "Broken"
            bl_space_type = 'NOT_A_SPACE'

        with self.assertRaises(Exception):
            utils.classes_register((WBP_OT_test_ok, WBP_PT_test_broken))
        self.assertFalse(WBP_OT_test_ok.is_registered)
        harness.addon_disable()


class FeatureToggleTest(unittest.TestCase):
    """Each feature can be turned off and on from the preferences, independently of the others."""

    def setUp(self):
        harness.addon_enable()
        self.addon = harness.addon_module()
        self.prefs = harness.addon_preferences()

    def tearDown(self):
        harness.addon_disable()

    def test_every_feature_has_toggle(self):
        for name in self.addon.FEATURE_NAMES:
            with self.subTest(feature=name):
                self.assertIn("use_" + name, self.prefs.bl_rna.properties)
                self.assertTrue(self.prefs.bl_rna.properties["use_" + name].description)

    def test_feature_submodules_exist(self):
        for name in self.addon.FEATURE_NAMES:
            with self.subTest(feature=name):
                module = sys.modules[self.addon.__name__ + "." + name]
                self.assertTrue(callable(module.register))
                self.assertTrue(callable(module.unregister))

    def test_toggle_independent(self):
        for name in self.addon.FEATURE_NAMES:
            with self.subTest(feature=name):
                setattr(self.prefs, "use_" + name, False)
                self.assertFalse(self.addon.feature_is_registered(name))
                others = [other for other in self.addon.FEATURE_NAMES if other != name]
                self.assertTrue(all(self.addon.feature_is_registered(other) for other in others))

                setattr(self.prefs, "use_" + name, True)
                self.assertTrue(self.addon.feature_is_registered(name))

    def test_disabled_feature_stays_off_after_file_load(self):
        self.prefs.use_shelves = False
        harness.scene_reset()
        self.prefs = harness.addon_preferences()

        self.assertFalse(self.prefs.use_shelves)
        self.assertFalse(self.addon.feature_is_registered("shelves"))
        self.assertTrue(self.addon.feature_is_registered("command_server"))

    def test_disable_with_feature_off(self):
        # Unregistering must not try to unregister a feature that is already off.
        self.prefs.use_driven_keys = False
        harness.addon_disable()
        self.assertFalse(self.addon.feature_is_registered("driven_keys"))
        harness.addon_enable()


if __name__ == "__main__":
    harness.main()
