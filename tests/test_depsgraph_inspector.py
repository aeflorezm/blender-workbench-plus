# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_depsgraph_inspector.py
"""

import json
import os
import sys
import tempfile
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import helpers


def _animated_scene():
    """An object animated on frames 1-10, constrained to a target and parented to another object."""
    anim, target, parent = helpers.objects_new("Anim", "Target", "Parent")
    constraint = anim.constraints.new('COPY_ROTATION')
    constraint.target = target
    anim.parent = parent
    anim.keyframe_insert("location", frame=1)
    anim.location.x = 5.0
    anim.keyframe_insert("location", frame=10)
    bpy.context.scene.frame_start = 1
    bpy.context.scene.frame_end = 10
    bpy.context.view_layer.update()
    return anim, target, parent


class DepsgraphInspectorTestCase(unittest.TestCase):

    def setUp(self):
        harness.addon_enable()
        self.di = sys.modules[harness.ADDON_MODULE + ".depsgraph_inspector"]
        self.di.recorder.clear()

    def tearDown(self):
        harness.addon_disable()


class RecordingTest(DepsgraphInspectorTestCase):

    def test_off_by_default(self):
        self.assertFalse(self.di.is_recording())
        helpers.objects_new("A")
        bpy.context.view_layer.update()
        self.assertEqual(len(self.di.recorder.records), 0)

    def test_toggle_operator(self):
        bpy.ops.wbp.depsgraph_record(state=True)
        self.assertTrue(self.di.is_recording())
        bpy.ops.wbp.depsgraph_record(state=False)
        self.assertFalse(self.di.is_recording())

    def test_update_recorded(self):
        (ob,) = helpers.objects_new("Moving")
        bpy.context.view_layer.update()
        self.di.recording_set(True)
        ob.location.x = 2.0
        bpy.context.view_layer.update()

        record = self.di.recorder.last
        self.assertEqual(record.kind, 'UPDATE')
        self.assertGreaterEqual(record.duration, 0.0)
        updates = {(update.id_type, update.name): update for update in record.updates}
        self.assertIn(('OBJECT', "Moving"), updates)
        self.assertTrue(updates[('OBJECT', "Moving")].is_updated_transform)

    def test_dependent_object_updated(self):
        anim, target, _parent = _animated_scene()
        self.di.recording_set(True)
        target.rotation_euler.z = 1.0
        bpy.context.view_layer.update()
        names = {update.name for update in self.di.recorder.last.updates}
        # The constrained object is re-evaluated because its target changed.
        self.assertIn(anim.name, names)

    def test_frame_change_recorded(self):
        anim, _target, _parent = _animated_scene()
        self.di.recording_set(True)
        bpy.context.scene.frame_set(5)
        record = self.di.recorder.last
        self.assertEqual(record.kind, 'FRAME')
        self.assertEqual(record.frame, 5)
        self.assertIn(anim.name, {update.name for update in record.updates})
        self.assertEqual(record.label, "Frame 5")

    def test_clear(self):
        self.di.recording_set(True)
        bpy.context.scene.frame_set(2)
        bpy.ops.wbp.depsgraph_clear()
        self.assertIsNone(self.di.recorder.last)

    def test_limit(self):
        recorder = sys.modules[harness.ADDON_MODULE + ".depsgraph_inspector.recorder"].Recorder(limit=3)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        for frame in range(5):
            recorder.begin('FRAME')
            recorder.end('FRAME', frame, depsgraph)
        self.assertEqual([record.frame for record in recorder.records], [2, 3, 4])

    def test_end_without_begin_ignored(self):
        self.assertIsNone(self.di.recorder.end('UPDATE', 1, bpy.context.evaluated_depsgraph_get()))
        self.assertEqual(len(self.di.recorder.records), 0)

    def test_disable_addon_stops_recording(self):
        self.di.recording_set(True)
        harness.addon_disable()
        handlers = bpy.app.handlers
        self.assertFalse([h for h in handlers.depsgraph_update_post if h.__module__.startswith(harness.ADDON_MODULE)])
        self.assertFalse([h for h in handlers.frame_change_post if h.__module__.startswith(harness.ADDON_MODULE)])
        harness.addon_enable()


class RelationsTest(DepsgraphInspectorTestCase):

    def test_uses_and_used_by(self):
        anim, target, parent = _animated_scene()
        relations = sys.modules[harness.ADDON_MODULE + ".depsgraph_inspector.relations"]

        uses, used_by = relations.relations(anim)
        uses_names = {(id_data.id_type, id_data.name) for id_data in uses}
        self.assertIn(('OBJECT', "Target"), uses_names)
        self.assertIn(('OBJECT', "Parent"), uses_names)
        self.assertIn(('ACTION', anim.animation_data.action.name), uses_names)
        self.assertNotIn(('OBJECT', "Anim"), uses_names)

        _uses, target_used_by = relations.relations(target)
        self.assertIn(anim, target_used_by)
        _uses, parent_used_by = relations.relations(parent)
        self.assertIn(anim, parent_used_by)

    def test_id_find(self):
        (ob,) = helpers.objects_new("Find Me", data='MESH')
        relations = sys.modules[harness.ADDON_MODULE + ".depsgraph_inspector.relations"]
        self.assertEqual(relations.id_find('OBJECT', "Find Me"), ob)
        self.assertEqual(relations.id_find('MESH', "Find Me"), ob.data)
        self.assertIsNone(relations.id_find('OBJECT', "Missing"))
        self.assertIsNone(relations.id_find('MATERIAL', "Find Me"))


class ReevaluateTest(DepsgraphInspectorTestCase):

    def test_active_object(self):
        (ob,) = helpers.objects_new("A")
        helpers.select_objects(ob)
        bpy.context.view_layer.update()
        self.di.recording_set(True)
        result, reports = helpers.operator_call(bpy.ops.wbp.depsgraph_reevaluate)
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(reports[0].type, 'INFO')
        self.assertRegex(reports[0].message, r"^Re-evaluated A in \d+\.\d\d ms$")
        self.assertIn("A", {update.name for update in self.di.recorder.last.updates})

    def test_by_name(self):
        _anim, target, _parent = _animated_scene()
        self.di.recording_set(True)
        result, _reports = helpers.operator_call(bpy.ops.wbp.depsgraph_reevaluate, id_type='OBJECT', id_name="Target")
        self.assertEqual(result, {'FINISHED'})
        self.assertIn(target.name, {update.name for update in self.di.recorder.last.updates})

    def test_not_found(self):
        result, reports = helpers.operator_call(bpy.ops.wbp.depsgraph_reevaluate, id_type='OBJECT', id_name="Nope")
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports, [helpers.Report('ERROR', "Data-block not found")])


class ProfileTest(DepsgraphInspectorTestCase):

    def test_profile_scene_range(self):
        _animated_scene()
        bpy.context.scene.frame_set(4)
        result, reports = helpers.operator_call(bpy.ops.wbp.depsgraph_profile)
        self.assertEqual(result, {'FINISHED'})
        self.assertRegex(reports[0].message, r"^Profiled 10 frames: ")

        frames = [record.frame for record in self.di.recorder.records]
        self.assertEqual(frames, list(range(1, 11)))
        self.assertTrue(all(record.kind == 'FRAME' for record in self.di.recorder.records))
        # The frame and the recording state are restored.
        self.assertEqual(bpy.context.scene.frame_current, 4)
        self.assertFalse(self.di.is_recording())

    def test_profile_custom_range_keeps_recording(self):
        _animated_scene()
        self.di.recording_set(True)
        bpy.ops.wbp.depsgraph_profile(frame_start=3, frame_end=5)
        self.assertEqual([record.frame for record in self.di.recorder.records], [3, 4, 5])
        self.assertTrue(self.di.is_recording())

    def test_profile_without_clear(self):
        _animated_scene()
        bpy.ops.wbp.depsgraph_profile(frame_start=1, frame_end=2)
        bpy.ops.wbp.depsgraph_profile(frame_start=3, frame_end=4, use_clear=False)
        self.assertEqual([record.frame for record in self.di.recorder.records], [1, 2, 3, 4])

    def test_profile_invalid_range(self):
        result, reports = helpers.operator_call(bpy.ops.wbp.depsgraph_profile, frame_start=5, frame_end=2)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[0].type, 'ERROR')

    def test_sorting_and_costs(self):
        _animated_scene()
        bpy.ops.wbp.depsgraph_profile()
        recorder = self.di.recorder
        durations = [record.duration for record in recorder.sorted_records('DURATION')]
        self.assertEqual(durations, sorted(durations, reverse=True))
        self.assertEqual([record.frame for record in recorder.sorted_records('FRAME')], list(range(1, 11)))
        self.assertEqual(recorder.sorted_records('ORDER'), list(recorder.records))

        costs = recorder.id_costs()
        self.assertEqual(costs, sorted(costs, key=lambda item: -item[3]))
        anim_cost = [item for item in costs if item[:2] == ('OBJECT', "Anim")]
        self.assertEqual(len(anim_cost), 1)
        self.assertEqual(anim_cost[0][2], 10)
        self.assertAlmostEqual(anim_cost[0][3], sum(record.duration for record in recorder.records
                                                    if "Anim" in {u.name for u in record.updates}))


class ExportTest(DepsgraphInspectorTestCase):

    def test_export_needs_records(self):
        self.assertFalse(bpy.ops.wbp.depsgraph_export.poll())

    def test_export_chrome_trace(self):
        _animated_scene()
        bpy.ops.wbp.depsgraph_profile(frame_start=1, frame_end=3)
        with tempfile.TemporaryDirectory() as tempdir:
            filepath = os.path.join(tempdir, "trace.json")
            self.assertEqual(bpy.ops.wbp.depsgraph_export(filepath=filepath), {'FINISHED'})
            with open(filepath, encoding="utf-8") as fh:
                trace = json.load(fh)
        slices = [event for event in trace["traceEvents"] if event["ph"] == "X"]
        self.assertEqual([event["name"] for event in slices], ["Frame 1", "Frame 2", "Frame 3"])
        for event in slices:
            self.assertGreaterEqual(event["dur"], 0.0)
            self.assertIn("OBJECT:Anim", event["args"]["updated"])
        # Slices are in time order and do not overlap.
        for previous, event in zip(slices, slices[1:]):
            self.assertGreaterEqual(event["ts"], previous["ts"] + previous["dur"])

    def test_export_json(self):
        _animated_scene()
        bpy.ops.wbp.depsgraph_profile(frame_start=1, frame_end=2)
        with tempfile.TemporaryDirectory() as tempdir:
            filepath = os.path.join(tempdir, "records.json")
            bpy.ops.wbp.depsgraph_export(filepath=filepath, format='JSON')
            with open(filepath, encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(data["version"], 1)
        self.assertEqual([record["frame"] for record in data["records"]], [1, 2])
        update = next(u for u in data["records"][0]["updates"] if u["name"] == "Anim")
        self.assertEqual(update["id_type"], "OBJECT")
        self.assertIn("is_updated_transform", update)


class PanelsTest(DepsgraphInspectorTestCase):

    def test_registered(self):
        for name in ("WBP_PT_depsgraph_inspector", "WBP_PT_depsgraph_relations", "WBP_PT_depsgraph_profile"):
            with self.subTest(panel=name):
                cls = getattr(bpy.types, name)
                self.assertEqual(cls.bl_space_type, 'VIEW_3D')
                self.assertEqual(cls.bl_category, "Workbench")
        self.assertIn("wbp_depsgraph_inspector", bpy.types.WindowManager.bl_rna.properties)


if __name__ == "__main__":
    harness.main()
