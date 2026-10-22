# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_driven_keys.py
"""

import os
import sys
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import helpers


def _evaluated(ob):
    bpy.context.view_layer.update()
    return ob.evaluated_get(bpy.context.evaluated_depsgraph_get())


class DrivenKeysTestCase(unittest.TestCase):

    def setUp(self):
        harness.addon_enable()
        self.relations = sys.modules[harness.ADDON_MODULE + ".driven_keys.relations"]
        self.Channel = self.relations.Channel
        self.settings = bpy.context.scene.wbp_driven_keys

    def tearDown(self):
        harness.addon_disable()


class RelationsTest(DrivenKeysTestCase):

    def setUp(self):
        super().setUp()
        self.ctrl, self.door = helpers.objects_new("Ctrl", "Door")
        self.driver = self.Channel(self.ctrl, "location", 0)
        self.driven = self.Channel(self.door, "location", 2)

    def key(self, driver_value, driven_value):
        self.relations.relationship_mute(self.driven, True)
        self.ctrl.location.x = driver_value
        self.door.location.z = driven_value
        self.relations.key_insert(self.driver, [self.driven])
        self.relations.relationship_mute(self.driven, False)

    def test_interpolates_between_keys(self):
        self.key(0.0, 0.0)
        self.key(2.0, 5.0)
        for driver_value, expected in ((0.0, 0.0), (1.0, 2.5), (2.0, 5.0), (-1.0, 0.0), (3.0, 5.0)):
            with self.subTest(driver=driver_value):
                self.ctrl.location.x = driver_value
                self.assertAlmostEqual(_evaluated(self.door).location.z, expected, places=4)

    def test_driver_setup(self):
        self.key(0.0, 0.0)
        fcurve = self.door.animation_data.drivers.find("location", index=2)
        self.assertEqual(len(fcurve.driver.variables), 1)
        variable = fcurve.driver.variables[0]
        self.assertEqual(variable.name, self.relations.VARIABLE_NAME)
        self.assertEqual(variable.targets[0].id, self.ctrl)
        self.assertEqual(variable.targets[0].data_path, "location[0]")
        self.assertEqual(fcurve.extrapolation, 'CONSTANT')
        self.assertEqual(len(fcurve.modifiers), 0)
        # The default keyframes of new driver F-curves are removed.
        self.assertEqual([tuple(point.co) for point in fcurve.keyframe_points], [(0.0, 0.0)])

    def test_key_replaces_same_driver_value(self):
        self.key(1.0, 2.0)
        self.key(1.0, 4.0)
        fcurve = self.door.animation_data.drivers.find("location", index=2)
        self.assertEqual([tuple(point.co) for point in fcurve.keyframe_points], [(1.0, 4.0)])

    def test_cannot_drive_itself(self):
        with self.assertRaises(ValueError):
            self.relations.key_insert(self.driver, [self.driver])

    def test_other_driver_kept(self):
        fcurve = self.door.driver_add("location", 2)
        fcurve.driver.expression = "frame"
        with self.assertRaises(ValueError) as cm:
            self.relations.key_insert(self.driver, [self.driven])
        self.assertIn("already has another driver", str(cm.exception))
        self.assertEqual(fcurve.driver.expression, "frame")

    def test_relationships_and_remove(self):
        self.key(0.0, 0.0)
        self.key(2.0, 6.0)
        self.door.driver_add("location", 1).driver.expression = "1"  # Not a driven key.
        found = self.relations.relationships()
        self.assertEqual(len(found), 1)
        driven, driver_id, driver_path, fcurve = found[0]
        self.assertEqual(driven.label, "Door.location[2]")
        self.assertEqual((driver_id, driver_path, len(fcurve.keyframe_points)), (self.ctrl, "location[0]", 2))

        self.ctrl.location.x = 1.0
        value = _evaluated(self.door).location.z
        self.door.location.z = value
        self.assertTrue(self.relations.relationship_remove(driven))
        self.assertIsNone(self.door.animation_data.drivers.find("location", index=2))
        self.assertAlmostEqual(self.door.location.z, value)
        self.assertFalse(self.relations.relationship_remove(driven))

    def test_rotation_driver(self):
        driver = self.Channel(self.ctrl, "rotation_euler", 2)
        self.relations.key_insert(driver, [self.driven])
        self.ctrl.rotation_euler.z = 1.0
        self.door.location.z = 3.0
        self.relations.relationship_mute(self.driven, True)
        self.relations.key_insert(driver, [self.driven])
        self.relations.relationship_mute(self.driven, False)
        self.ctrl.rotation_euler.z = 0.5
        self.assertAlmostEqual(_evaluated(self.door).location.z, 1.5, places=4)


class OperatorsTest(DrivenKeysTestCase):

    def setUp(self):
        super().setUp()
        self.ctrl, self.door, self.lid = helpers.objects_new("Ctrl", "Door", "Lid")
        helpers.select_objects(self.door, self.lid, self.ctrl, active=self.ctrl)
        self.settings.driver_channel = "rotation_euler.0"
        self.settings.driven_channels = {"location.2", "scale.0"}

    def load(self):
        self.assertEqual(bpy.ops.wbp.driven_keys_load_driver(), {'FINISHED'})
        self.assertEqual(bpy.ops.wbp.driven_keys_load_driven(), {'FINISHED'})

    def test_load(self):
        self.load()
        self.assertEqual((self.settings.driver_target, self.settings.driver_path, self.settings.driver_index),
                         (self.ctrl, "rotation_euler", 0))
        self.assertEqual(
            sorted((item.target.name, item.data_path, item.index) for item in self.settings.driven),
            [("Door", "location", 2), ("Door", "scale", 0), ("Lid", "location", 2), ("Lid", "scale", 0)],
        )

    def test_load_driven_needs_selection(self):
        helpers.select_objects(self.ctrl)
        result, reports = helpers.operator_call(bpy.ops.wbp.driven_keys_load_driven)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[0].type, 'ERROR')

    def test_key_workflow(self):
        self.load()
        self.assertEqual(bpy.ops.wbp.driven_keys_key(), {'FINISHED'})

        self.settings.use_posing = True
        self.ctrl.rotation_euler.x = 1.0
        self.door.location.z = 2.0
        self.lid.scale.x = 3.0
        bpy.context.view_layer.update()
        # While posing, the relationships are muted: the pose is kept.
        self.assertAlmostEqual(_evaluated(self.door).location.z, 2.0)
        result, reports = helpers.operator_call(bpy.ops.wbp.driven_keys_key)
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(reports, [helpers.Report('INFO', "Keyed 4 channels at Ctrl.rotation_euler[0] = 1.000")])
        self.settings.use_posing = False

        self.ctrl.rotation_euler.x = 0.5
        self.assertAlmostEqual(_evaluated(self.door).location.z, 1.0, places=4)
        self.assertAlmostEqual(_evaluated(self.lid).scale.x, 2.0, places=4)
        self.assertAlmostEqual(_evaluated(self.lid).location.z, 0.0, places=4)

    def test_key_needs_driver_and_driven(self):
        self.assertFalse(bpy.ops.wbp.driven_keys_key.poll())

    def test_relationship_operators(self):
        self.load()
        bpy.ops.wbp.driven_keys_key()
        self.assertEqual(len(self.relations.relationships()), 4)

        self.assertEqual(bpy.ops.wbp.driven_keys_relationship_load(label="Lid.scale[0]"), {'FINISHED'})
        self.assertEqual([(item.target, item.data_path, item.index) for item in self.settings.driven],
                         [(self.lid, "scale", 0)])
        self.assertEqual((self.settings.driver_target, self.settings.driver_path, self.settings.driver_index),
                         (self.ctrl, "rotation_euler", 0))

        self.assertEqual(bpy.ops.wbp.driven_keys_relationship_remove(label="Lid.scale[0]"), {'FINISHED'})
        self.assertEqual(len(self.relations.relationships()), 3)
        result, _reports = helpers.operator_call(bpy.ops.wbp.driven_keys_relationship_remove, label="Lid.scale[0]")
        self.assertEqual(result, {'CANCELLED'})

    def test_undo_key(self):
        self.load()
        bpy.ops.ed.undo_push(message="Before")
        bpy.ops.wbp.driven_keys_key('EXEC_DEFAULT', True)
        self.assertEqual(len(self.relations.relationships()), 4)
        bpy.ops.ed.undo()
        self.assertEqual(len(self.relations.relationships()), 0)


class PoseBonesTest(DrivenKeysTestCase):

    def setUp(self):
        super().setUp()
        armature = bpy.data.armatures.new("Rig")
        self.rig = bpy.data.objects.new("Rig", armature)
        bpy.context.scene.collection.objects.link(self.rig)
        helpers.select_objects(self.rig)
        bpy.ops.object.mode_set(mode='EDIT')
        for name, head in (("Ctrl", (0.0, 0.0, 0.0)), ("Finger", (1.0, 0.0, 0.0))):
            bone = armature.edit_bones.new(name)
            bone.head = head
            bone.tail = (head[0], 0.0, 1.0)
        bpy.ops.object.mode_set(mode='POSE')
        bones = self.rig.pose.bones
        for bone in bones:
            bone.rotation_mode = 'XYZ'
            bone.select = True
        armature.bones.active = armature.bones["Ctrl"]

    def tearDown(self):
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        super().tearDown()

    def test_bone_driver_and_driven(self):
        self.settings.driver_channel = "location.1"
        self.settings.driven_channels = {"rotation_euler.0"}
        self.assertEqual(bpy.ops.wbp.driven_keys_load_driver(), {'FINISHED'})
        self.assertEqual(self.settings.driver_path, 'pose.bones["Ctrl"].location')
        self.assertEqual(bpy.ops.wbp.driven_keys_load_driven(), {'FINISHED'})
        self.assertEqual([(item.target, item.data_path, item.index) for item in self.settings.driven],
                         [(self.rig, 'pose.bones["Finger"].rotation_euler', 0)])

        bpy.ops.wbp.driven_keys_key()
        self.settings.use_posing = True
        self.rig.pose.bones["Ctrl"].location.y = 1.0
        self.rig.pose.bones["Finger"].rotation_euler.x = 1.2
        bpy.ops.wbp.driven_keys_key()
        self.settings.use_posing = False

        self.rig.pose.bones["Ctrl"].location.y = 0.5
        evaluated = _evaluated(self.rig)
        self.assertAlmostEqual(evaluated.pose.bones["Finger"].rotation_euler.x, 0.6, places=4)


class PanelsTest(DrivenKeysTestCase):

    def test_registered(self):
        self.assertEqual(bpy.types.WBP_PT_driven_keys.bl_category, "Workbench")
        self.assertEqual(bpy.types.WBP_PT_driven_keys_relationships.bl_parent_id, "WBP_PT_driven_keys")
        self.assertIn("wbp_driven_keys", bpy.types.Scene.bl_rna.properties)


if __name__ == "__main__":
    harness.main()
