# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_helpers.py
"""

import math
import os
import sys
import unittest

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import helpers


class _ReportOperator(bpy.types.Operator):
    """Test operator emitting one report of the requested type"""
    bl_idname = "wbp_test.report"
    bl_label = "Report"

    report_type: bpy.props.StringProperty(default='INFO')
    message: bpy.props.StringProperty(default="")

    def execute(self, _context):
        self.report({self.report_type}, self.message)
        return {'CANCELLED'} if self.report_type == 'ERROR' else {'FINISHED'}


class _PollFailsOperator(bpy.types.Operator):
    """Test operator that can never run"""
    bl_idname = "wbp_test.poll_fails"
    bl_label = "Poll Fails"

    @classmethod
    def poll(cls, _context):
        return False

    def execute(self, _context):
        return {'FINISHED'}


class SceneHelpersTest(unittest.TestCase):

    def test_objects_new_empty(self):
        a, b = helpers.objects_new("A", "B")
        self.assertEqual((a.name, b.name), ("A", "B"))
        self.assertIsNone(a.data)
        self.assertIn(a.name, bpy.context.scene.collection.objects)

    def test_objects_new_mesh(self):
        (ob,) = helpers.objects_new("Cube", data='MESH')
        self.assertEqual(ob.type, 'MESH')
        self.assertEqual(len(ob.data.vertices), 8)
        self.assertEqual(len(ob.data.polygons), 6)

    def test_objects_new_into_collection(self):
        collection = bpy.data.collections.new("Sub")
        bpy.context.scene.collection.children.link(collection)
        (ob,) = helpers.objects_new("A", collection=collection)
        self.assertIn(ob.name, collection.objects)
        self.assertNotIn(ob.name, bpy.context.scene.collection.objects)

    def test_objects_new_invalid_data(self):
        with self.assertRaises(ValueError):
            helpers.objects_new("A", data='CAMERA')

    def test_select_objects(self):
        a, b, c = helpers.objects_new("A", "B", "C")
        helpers.select_objects(a, b)
        self.assertEqual({ob.name for ob in bpy.context.selected_objects}, {"A", "B"})
        self.assertEqual(bpy.context.view_layer.objects.active, b)

        helpers.select_objects(a, c, active=a)
        self.assertEqual({ob.name for ob in bpy.context.selected_objects}, {"A", "C"})
        self.assertEqual(bpy.context.view_layer.objects.active, a)

        helpers.select_objects()
        self.assertEqual(bpy.context.selected_objects, [])
        self.assertIsNone(bpy.context.view_layer.objects.active)


class TransformAssertionsTest(unittest.TestCase, helpers.TransformAssertions):

    def test_vector_within_tolerance(self):
        self.assertVectorAlmostEqual(Vector((1.0, 2.0, 3.0)), (1.0, 2.0, 3.0 + 1e-7))
        self.assertVectorAlmostEqual(Euler((0.0, 0.0, math.pi)), Euler((0.0, 0.0, math.pi + 1e-7)))
        self.assertVectorAlmostEqual(Quaternion(), (1.0, 0.0, 0.0, 0.0))

    def test_vector_out_of_tolerance(self):
        with self.assertRaises(AssertionError) as cm:
            self.assertVectorAlmostEqual(Vector((1.0, 2.0, 3.0)), Vector((1.0, 2.5, 3.0)))
        self.assertIn("component 1", str(cm.exception))
        self.assertIn("0.5", str(cm.exception))

    def test_vector_custom_delta(self):
        self.assertVectorAlmostEqual((0.0, 0.0), (0.0, 0.05), delta=0.1)
        with self.assertRaises(AssertionError):
            self.assertVectorAlmostEqual((0.0, 0.0), (0.0, 0.05), delta=0.01)

    def test_vector_size_mismatch(self):
        with self.assertRaises(AssertionError) as cm:
            self.assertVectorAlmostEqual((0.0, 0.0), (0.0, 0.0, 0.0))
        self.assertIn("sizes differ", str(cm.exception))

    def test_matrix_within_tolerance(self):
        a = Matrix.Translation((1.0, 2.0, 3.0)) @ Matrix.Rotation(math.pi / 2.0, 4, 'Z')
        b = a.copy()
        b[0][3] += 1e-7
        self.assertMatrixAlmostEqual(a, b)

    def test_matrix_out_of_tolerance(self):
        a = Matrix.Identity(4)
        b = Matrix.Identity(4)
        b[2][3] = 0.001
        with self.assertRaises(AssertionError) as cm:
            self.assertMatrixAlmostEqual(a, b)
        # Row 2, column 3 of a 4x4 matrix.
        self.assertIn("component 11", str(cm.exception))

    def test_object_world_matrix(self):
        (ob,) = helpers.objects_new("A")
        ob.location = (1.0, 2.0, 3.0)
        bpy.context.view_layer.update()
        self.assertMatrixAlmostEqual(ob.matrix_world, Matrix.Translation((1.0, 2.0, 3.0)))


class ReportCaptureTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        bpy.utils.register_class(_ReportOperator)
        bpy.utils.register_class(_PollFailsOperator)

    @classmethod
    def tearDownClass(cls):
        bpy.utils.unregister_class(_PollFailsOperator)
        bpy.utils.unregister_class(_ReportOperator)

    def test_info_and_warning(self):
        with helpers.capture_reports() as reports:
            bpy.ops.wbp_test.report(report_type='INFO', message="first")
            bpy.ops.wbp_test.report(report_type='WARNING', message="second")
        self.assertEqual(reports, [
            helpers.Report('INFO', "first"),
            helpers.Report('WARNING', "second"),
        ])

    def test_no_reports(self):
        with helpers.capture_reports() as reports:
            print("Plain output is not a report")
        self.assertEqual(reports, [])

    def test_operator_call_error(self):
        result, reports = helpers.operator_call(bpy.ops.wbp_test.report, report_type='ERROR', message="broken")
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports, [helpers.Report('ERROR', "broken")])

    def test_operator_call_success(self):
        result, reports = helpers.operator_call(bpy.ops.wbp_test.report, message="done")
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(reports, [helpers.Report('INFO', "done")])

    def test_operator_call_poll_fails(self):
        result, reports = helpers.operator_call(bpy.ops.wbp_test.poll_fails)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].type, 'ERROR')
        self.assertIn("poll", reports[0].message)

    def test_operator_call_property_named_operator(self):
        # Operators may have a property called "operator" (or "args"): it must reach the operator.
        result, reports = helpers.operator_call(lambda **kwargs: {kwargs["operator"]}, operator='FINISHED')
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(reports, [])

    def test_capture_restores_stdout(self):
        with helpers.capture_reports():
            pass
        # Writing to the real standard output still works after the capture.
        os.write(sys.stdout.fileno(), b"")
        print("stdout restored")


if __name__ == "__main__":
    harness.main()
