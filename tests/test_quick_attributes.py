# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_quick_attributes.py
"""

import math
import os
import sys
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import helpers


class QuickAttributesTestCase(unittest.TestCase, helpers.TransformAssertions):

    def setUp(self):
        harness.addon_enable()
        self.qa = sys.modules[harness.ADDON_MODULE + ".quick_attributes"]
        self.a, self.b, self.c = helpers.objects_new("A", "B", "C")
        self.a.location = (1.0, 0.0, 0.0)
        self.b.location = (2.0, 0.0, 0.0)
        self.c.location = (3.0, 0.0, 0.0)
        helpers.select_objects(self.a, self.b, active=self.a)
        self.props = bpy.context.scene.wbp_quick_attributes

    def tearDown(self):
        harness.addon_disable()


class ChannelsTest(QuickAttributesTestCase):

    def test_channels(self):
        self.assertEqual(len(self.qa.CHANNELS), 9)
        self.assertEqual([channel.id for channel in self.qa.CHANNELS[:3]], ["location.0", "location.1", "location.2"])
        self.assertEqual({channel.kind for channel in self.qa.CHANNELS}, {'LOCATION', 'ROTATION', 'SCALE'})

    def test_virtual_property_reads_active(self):
        self.assertEqual(self.props.location_0, 1.0)
        bpy.context.view_layer.objects.active = self.b
        self.assertEqual(self.props.location_0, 2.0)

    def test_virtual_property_without_active(self):
        helpers.select_objects()
        self.assertEqual(self.props.location_0, 0.0)
        self.props.location_0 = 5.0  # No targets: nothing happens.
        self.assertEqual(self.c.location.x, 3.0)

    def test_set_all_selected(self):
        self.props.location_1 = 4.0
        self.assertEqual(self.a.location.y, 4.0)
        self.assertEqual(self.b.location.y, 4.0)
        # Not selected.
        self.assertEqual(self.c.location.y, 0.0)

    def test_set_rotation_and_scale(self):
        self.props.rotation_euler_2 = math.pi / 2.0
        self.props.scale_0 = 2.0
        for ob in (self.a, self.b):
            self.assertAlmostEqual(ob.rotation_euler.z, math.pi / 2.0, places=6)
            self.assertEqual(ob.scale.x, 2.0)

    def test_active_not_selected_is_included(self):
        helpers.select_objects(self.b, active=self.c)
        self.c.select_set(False)
        self.props.location_2 = 7.0
        self.assertEqual(self.b.location.z, 7.0)
        self.assertEqual(self.c.location.z, 7.0)
        self.assertEqual(self.a.location.z, 0.0)

    def test_locked_channel_skipped(self):
        self.b.lock_location[0] = True
        self.props.location_0 = 9.0
        self.assertEqual(self.a.location.x, 9.0)
        self.assertEqual(self.b.location.x, 2.0)

    def test_registered_on_scene(self):
        # On the scene, so that edits from the interface are undoable.
        self.assertIn("wbp_quick_attributes", bpy.types.Scene.bl_rna.properties)
        prop = self.props.bl_rna.properties["rotation_euler_0"]
        self.assertEqual(prop.subtype, 'ANGLE')
        self.assertTrue(prop.description)


class SliderTest(QuickAttributesTestCase):

    def test_offset_keeps_relative_values(self):
        bpy.ops.wbp.quick_attributes_slide(channel="location.0", delta=0.5)
        self.assertAlmostEqual(self.a.location.x, 1.5)
        self.assertAlmostEqual(self.b.location.x, 2.5)
        self.assertAlmostEqual(self.c.location.x, 3.0)

    def test_offset_from_initial_values(self):
        channel = self.qa.channel_from_id("location.0")
        initial = {"A": 1.0, "B": 2.0}
        # A slider applies every new delta to the values from when it started, not cumulatively.
        self.qa.channels_offset(bpy.context, channel, 0.3, initial)
        self.qa.channels_offset(bpy.context, channel, 0.5, initial)
        self.assertAlmostEqual(self.a.location.x, 1.5)
        self.assertAlmostEqual(self.b.location.x, 2.5)

    def test_offset_skips_locked(self):
        self.a.lock_scale[1] = True
        bpy.ops.wbp.quick_attributes_slide(channel="scale.1", delta=1.0)
        self.assertEqual(self.a.scale.y, 1.0)
        self.assertEqual(self.b.scale.y, 2.0)

    def test_slider_delta(self):
        self.assertAlmostEqual(self.qa.slider_delta('LOCATION', 100), 1.0)
        self.assertAlmostEqual(self.qa.slider_delta('LOCATION', 100, fine=True), 0.1)
        self.assertAlmostEqual(self.qa.slider_delta('LOCATION', 37, steps=True), 0.4)
        self.assertAlmostEqual(self.qa.slider_delta('ROTATION', 10), math.radians(5.0))
        self.assertAlmostEqual(self.qa.slider_delta('ROTATION', 13, steps=True), math.radians(5.0))
        self.assertAlmostEqual(self.qa.slider_delta('SCALE', -50), -0.5)

    def test_unknown_channel(self):
        with self.assertRaises(KeyError):
            self.qa.channel_from_id("location.3")
        with self.assertRaises(TypeError):
            bpy.ops.wbp.quick_attributes_slide(channel="nope", delta=1.0)


class ChannelActionsTest(QuickAttributesTestCase):

    def _fcurve(self, ob, channel_id):
        return self.qa._fcurve_find(ob, self.qa.channel_from_id(channel_id))

    def test_key(self):
        bpy.context.scene.frame_set(5)
        self.assertEqual(bpy.ops.wbp.quick_attributes_channel(channel="location.0", action='KEY'), {'FINISHED'})
        for ob in (self.a, self.b):
            fcurve = self._fcurve(ob, "location.0")
            self.assertIsNotNone(fcurve)
            self.assertEqual([tuple(point.co) for point in fcurve.keyframe_points], [(5.0, ob.location.x)])
            self.assertEqual(fcurve.keyframe_points[0].type, 'KEYFRAME')
            # Only that channel was keyed.
            self.assertIsNone(self._fcurve(ob, "location.1"))
        self.assertIsNone(self.c.animation_data)

    def test_breakdown(self):
        bpy.ops.wbp.quick_attributes_channel(channel="rotation_euler.1", action='BREAKDOWN')
        self.assertEqual(self._fcurve(self.a, "rotation_euler.1").keyframe_points[0].type, 'BREAKDOWN')

    def test_lock_unlock(self):
        bpy.ops.wbp.quick_attributes_channel(channel="scale.2", action='LOCK')
        self.assertTrue(self.a.lock_scale[2])
        self.assertTrue(self.b.lock_scale[2])
        self.assertFalse(self.c.lock_scale[2])
        self.assertFalse(self.a.lock_scale[1])
        bpy.ops.wbp.quick_attributes_channel(channel="scale.2", action='UNLOCK')
        self.assertFalse(self.a.lock_scale[2])

    def test_mute_unmute(self):
        bpy.ops.wbp.quick_attributes_channel(channel="location.0", action='KEY')
        bpy.ops.wbp.quick_attributes_channel(channel="location.0", action='MUTE')
        self.assertTrue(self._fcurve(self.a, "location.0").mute)
        self.assertTrue(self._fcurve(self.b, "location.0").mute)
        bpy.ops.wbp.quick_attributes_channel(channel="location.0", action='UNMUTE')
        self.assertFalse(self._fcurve(self.a, "location.0").mute)

    def test_mute_without_animation(self):
        result, reports = helpers.operator_call(bpy.ops.wbp.quick_attributes_channel,
                                                channel="location.0", action='MUTE')
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports, [helpers.Report('WARNING', "No channel changed")])

    def test_needs_active_object(self):
        helpers.select_objects()
        self.assertFalse(bpy.ops.wbp.quick_attributes_channel.poll())
        self.assertFalse(bpy.ops.wbp.quick_attributes_slide.poll())


class CustomPropertiesTest(QuickAttributesTestCase):

    def test_numeric_properties_listed(self):
        self.a["weight"] = 0.5
        self.a["count"] = 3
        self.a["label"] = "text"
        self.a["flag"] = True
        self.a["_hidden"] = 1.0
        self.assertEqual(self.qa.custom_properties(self.a), ["count", "weight"])

    def test_copy_to_selected(self):
        self.a["weight"] = 0.75
        self.assertEqual(bpy.ops.wbp.quick_attributes_copy_property(name="weight"), {'FINISHED'})
        self.assertEqual(self.b["weight"], 0.75)
        self.assertNotIn("weight", self.c.keys())

    def test_copy_missing(self):
        result, reports = helpers.operator_call(bpy.ops.wbp.quick_attributes_copy_property, name="missing")
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[0].type, 'ERROR')


class UndoTest(QuickAttributesTestCase):

    def test_slide_undo(self):
        bpy.ops.ed.undo_push(message="Before")
        bpy.ops.wbp.quick_attributes_slide('EXEC_DEFAULT', True, channel="location.0", delta=1.0)
        self.assertAlmostEqual(bpy.data.objects["B"].location.x, 3.0)
        bpy.ops.ed.undo()
        self.assertAlmostEqual(bpy.data.objects["A"].location.x, 1.0)
        self.assertAlmostEqual(bpy.data.objects["B"].location.x, 2.0)


if __name__ == "__main__":
    harness.main()
