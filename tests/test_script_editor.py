# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_script_editor.py
"""

import math
import os
import sys
import unittest
from typing import NamedTuple

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import helpers


class FakeReport(NamedTuple):
    session_uid: int
    type: str
    message: str


class ScriptEditorTestCase(unittest.TestCase):

    def setUp(self):
        harness.addon_enable()
        self.se = sys.modules[harness.ADDON_MODULE + ".script_editor"]
        self.echo = sys.modules[harness.ADDON_MODULE + ".script_editor.echo"]

    def tearDown(self):
        harness.addon_disable()

    def log_lines(self):
        text = self.se.log_text(create=False)
        return text.as_string().split("\n") if text is not None else []


class RunTest(ScriptEditorTestCase):

    def test_run_logs_code_and_output(self):
        self.assertIsNone(self.se.run_code("print('hello')\nprint(1 + 1)", "tool.py"))
        self.assertEqual(self.log_lines(), ["# >>> tool.py", "print('hello')", "print(1 + 1)", "# hello", "# 2"])

    def test_namespace_kept_between_runs(self):
        self.se.run_code("value = 21", "a.py")
        self.se.run_code("print(value * 2)", "b.py")
        self.assertEqual(self.log_lines()[-1], "# 42")

    def test_namespace_has_shortcuts(self):
        self.se.run_code("print(D is bpy.data, C.scene.name)", "c.py")
        self.assertEqual(self.log_lines()[-1], "# True Scene")

    def test_error_logged_with_traceback(self):
        error = self.se.run_code("x = 1\nx / 0", "broken.py")
        self.assertIsInstance(error, ZeroDivisionError)
        lines = self.log_lines()
        self.assertIn('#   File "<broken.py>", line 2, in <module>', lines)
        self.assertEqual(lines[-1], "# ZeroDivisionError: division by zero")

    def test_system_exit_does_not_quit(self):
        error = self.se.run_code("import sys\nsys.exit(1)", "exit.py")
        self.assertIsInstance(error, SystemExit)

    def test_stderr_captured(self):
        self.se.run_code("import sys\nsys.stderr.write('warn\\n')", "err.py")
        self.assertEqual(self.log_lines()[-1], "# warn")

    def test_log_limit(self):
        limit = self.se.LOG_LINES_MAX
        self.se.log_append(["line {:d}".format(i) for i in range(limit + 10)])
        lines = self.log_lines()
        self.assertEqual(len(lines), limit)
        self.assertEqual(lines[0], "line 10")

    def test_log_clear(self):
        self.se.log_append(["something"])
        bpy.ops.wbp.script_log_clear()
        self.assertEqual(self.se.log_text().as_string(), "")

    def test_run_operator_needs_text_editor(self):
        self.assertFalse(bpy.ops.wbp.script_run.poll())
        self.assertFalse(bpy.ops.wbp.script_log_show.poll())


class ReportEchoTest(ScriptEditorTestCase):

    def test_operator_reports_become_lines(self):
        reader = self.echo.ReportReader()
        reports = [
            FakeReport(1, 'OPERATOR', "bpy.ops.mesh.primitive_cube_add(size=3)"),
            FakeReport(2, 'INFO', "Saved \"file.blend\""),
            FakeReport(3, 'WARNING', "Careful"),
        ]
        self.assertEqual(reader.read(reports), [
            "bpy.ops.mesh.primitive_cube_add(size=3)",
            "# Info: Saved \"file.blend\"",
            "# Warning: Careful",
        ])
        # Only new reports the next time.
        reports.append(FakeReport(4, 'OPERATOR', "bpy.ops.object.delete()"))
        self.assertEqual(reader.read(reports), ["bpy.ops.object.delete()"])
        self.assertEqual(reader.read(reports), [])

    def test_skip_existing(self):
        reader = self.echo.ReportReader()
        reader.skip_existing([FakeReport(5, 'OPERATOR', "old()")])
        self.assertEqual(reader.read([FakeReport(5, 'OPERATOR', "old()"), FakeReport(6, 'OPERATOR', "new()")]),
                         ["new()"])

    def test_echo_operators_toggle_timer(self):
        settings = bpy.context.window_manager.wbp_script_editor
        settings.use_echo_operators = True
        self.assertTrue(bpy.app.timers.is_registered(self.se._echo_operators_timer))
        settings.use_echo_operators = False
        self.assertFalse(bpy.app.timers.is_registered(self.se._echo_operators_timer))


class PropertyEchoTest(ScriptEditorTestCase, helpers.TransformAssertions):

    def setUp(self):
        super().setUp()
        (self.ob,) = helpers.objects_new('Cube "1"')
        self.watcher = self.echo.PropertyWatcher()
        self.watcher.reset()

    def test_no_change_no_line(self):
        self.assertEqual(self.watcher.check_all(), [])

    def test_changes_become_assignments(self):
        self.ob.location = (1.0, 2.5, -3.0)
        self.ob.hide_render = True
        bpy.context.scene.frame_current = 12
        self.assertEqual(self.watcher.check_all(), [
            'bpy.data.objects["Cube \\"1\\""].location = (1.0, 2.5, -3.0)',
            'bpy.data.objects["Cube \\"1\\""].hide_render = True',
            'bpy.data.scenes["Scene"].frame_current = 12',
        ])
        self.assertEqual(self.watcher.check_all(), [])

    def test_assignment_reproduces_change(self):
        self.ob.rotation_euler = (0.1, math.pi / 3.0, 0.0)
        self.ob.scale = (2.0, 2.0, 2.0)
        lines = self.watcher.check_all()
        self.ob.rotation_euler = (0.0, 0.0, 0.0)
        self.ob.scale = (1.0, 1.0, 1.0)
        exec("\n".join(lines), {"bpy": bpy})
        self.assertVectorAlmostEqual(self.ob.rotation_euler, (0.1, math.pi / 3.0, 0.0), delta=1e-6)
        self.assertVectorAlmostEqual(self.ob.scale, (2.0, 2.0, 2.0))

    def test_check_single_property(self):
        self.ob.location.x = 4.0
        self.ob.scale.x = 3.0
        self.assertEqual(self.watcher.check("location", "objects"),
                         ['bpy.data.objects["Cube \\"1\\""].location = (4.0, 0.0, 0.0)'])

    def test_object_created_after_reset(self):
        # Seen by the depsgraph after creation, then changed: the change is echoed.
        (new,) = helpers.objects_new("Later")
        self.watcher.track_new()
        new.location.x = 2.0
        self.assertEqual(self.watcher.check("location", "objects"),
                         ['bpy.data.objects["Later"].location = (2.0, 0.0, 0.0)'])

    def test_new_objects_tracked_on_depsgraph_update(self):
        settings = bpy.context.window_manager.wbp_script_editor
        settings.use_echo_properties = True
        (new,) = helpers.objects_new("Later")
        bpy.context.view_layer.update()
        new.location.y = 1.0
        self.se._on_property_changed("location", "objects")
        self.assertEqual(self.log_lines()[-1], 'bpy.data.objects["Later"].location = (0.0, 1.0, 0.0)')
        settings.use_echo_properties = False
        self.assertNotIn(self.se._on_depsgraph_update_post, bpy.app.handlers.depsgraph_update_post)

    def test_new_objects_are_not_changes(self):
        helpers.objects_new("New")
        self.assertEqual(self.watcher.check_all(), [])

    def test_echo_properties_toggle(self):
        settings = bpy.context.window_manager.wbp_script_editor
        settings.use_echo_properties = True
        # Notifications are not sent in background mode: call the handler like `bpy.msgbus` would.
        self.ob.location.z = 5.0
        self.se._on_property_changed("location", "objects")
        self.assertEqual(self.log_lines()[-1], 'bpy.data.objects["Cube \\"1\\""].location = (0.0, 0.0, 5.0)')
        settings.use_echo_properties = False

    def test_value_to_python(self):
        value_to_python = self.echo.value_to_python
        self.assertEqual(value_to_python(0.30000000000000004), "0.3")
        self.assertEqual(value_to_python(2.0), "2.0")
        self.assertEqual(value_to_python(7), "7")
        self.assertEqual(value_to_python(True), "True")
        self.assertEqual(value_to_python((1.5,)), "(1.5,)")
        self.assertEqual(value_to_python(((1.0, 0.0), (0.0, 1.0))), "((1.0, 0.0), (0.0, 1.0))")
        self.assertEqual(value_to_python(float("inf")), "float('inf')")


class CleanupTest(ScriptEditorTestCase):

    def test_disable_stops_echo(self):
        settings = bpy.context.window_manager.wbp_script_editor
        settings.use_echo_operators = True
        timer = self.se._echo_operators_timer
        harness.addon_disable()
        self.assertFalse(bpy.app.timers.is_registered(timer))
        harness.addon_enable()


if __name__ == "__main__":
    harness.main()
