# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_shelves.py
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


class ShelvesTestCase(unittest.TestCase):
    """Uses a temporary library file, never the user's one."""

    def setUp(self):
        harness.addon_enable()
        self.shelves = sys.modules[harness.ADDON_MODULE + ".shelves"]
        self.library_module = sys.modules[harness.ADDON_MODULE + ".shelves.library"]
        # On Windows another process (e.g. a virus scanner) may briefly keep a just written file open,
        # which makes the cleanup fail with "directory not empty": that must not fail the test.
        self.tempdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.filepath = os.path.join(self.tempdir.name, "config", "shelves.json")
        self.shelves.library_reload(self.filepath)
        self.library = self.shelves.library

    def tearDown(self):
        harness.addon_disable()
        self.tempdir.cleanup()

    def saved(self):
        with open(self.filepath, encoding="utf-8") as fh:
            return json.load(fh)


class LibraryTest(ShelvesTestCase):

    def test_starts_empty_without_file(self):
        self.assertEqual(self.library.shelves, [])
        self.assertFalse(os.path.exists(self.filepath))

    def test_add_shelf_saves(self):
        self.assertEqual(bpy.ops.wbp.shelf_add(name="Modeling"), {'FINISHED'})
        self.assertEqual(self.saved(), {"version": 1, "shelves": [{"name": "Modeling", "buttons": []}]})
        self.assertEqual(bpy.context.window_manager.wbp_shelves.active_shelf, 0)

    def test_unique_names(self):
        bpy.ops.wbp.shelf_add(name="Tools")
        bpy.ops.wbp.shelf_add(name="Tools")
        bpy.ops.wbp.shelf_add(name="Tools")
        self.assertEqual([shelf["name"] for shelf in self.library.shelves], ["Tools", "Tools (2)", "Tools (3)"])

    def test_rename_and_remove(self):
        bpy.ops.wbp.shelf_add(name="A")
        bpy.ops.wbp.shelf_add(name="B")
        bpy.ops.wbp.shelf_rename(index=0, name="Renamed")
        bpy.ops.wbp.shelf_remove(index=1)
        self.assertEqual([shelf["name"] for shelf in self.saved()["shelves"]], ["Renamed"])
        self.assertEqual(bpy.context.window_manager.wbp_shelves.active_shelf, 0)

    def test_empty_name_rejected(self):
        result, reports = helpers.operator_call(bpy.ops.wbp.shelf_add, name="  ")
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[0].type, 'ERROR')

    def test_persists_across_restart(self):
        bpy.ops.wbp.shelf_add(name="Kept")
        bpy.ops.wbp.shelf_button_add(label="Cube", operator="mesh.primitive_cube_add")
        self.shelves.library_reload(self.filepath)
        self.assertEqual(self.shelves.library.shelves[0]["buttons"][0]["label"], "Cube")

    def test_invalid_file_not_overwritten(self):
        os.makedirs(os.path.dirname(self.filepath))
        with open(self.filepath, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        self.shelves.library_reload(self.filepath)
        self.assertIn("invalid JSON", self.shelves.library_error)
        bpy.ops.wbp.shelf_add(name="New")
        with open(self.filepath, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{broken")

    def test_validate(self):
        validate = self.library_module.validate
        good = {"version": 1, "shelves": [{"name": "S", "buttons": [
            {"label": "A", "kind": "OPERATOR", "operator": "object.select_all", "properties": {"action": "SELECT"}},
            {"label": "B", "kind": "SCRIPT", "script": "pass"},
        ]}]}
        validate(good)
        for data, message in (
                ([], "document"),
                ({"version": 2, "shelves": []}, "version"),
                ({"version": 1, "shelves": {}}, "shelves"),
                ({"version": 1, "shelves": [{"name": "", "buttons": []}]}, "shelves[0].name"),
                ({"version": 1, "shelves": [{"name": "S", "buttons": [{"label": "A", "kind": "X"}]}]},
                 "shelves[0].buttons[0].kind"),
                ({"version": 1, "shelves": [{"name": "S", "buttons": [
                    {"label": "A", "kind": "OPERATOR", "operator": "nodot"}]}]}, "shelves[0].buttons[0].operator"),
                ({"version": 1, "shelves": [{"name": "S", "buttons": [
                    {"label": "A", "kind": "SCRIPT", "script": ""}]}]}, "shelves[0].buttons[0].script"),
        ):
            with self.subTest(message=message):
                with self.assertRaises(ValueError) as cm:
                    validate(data)
                self.assertTrue(str(cm.exception).startswith(message), str(cm.exception))


class ButtonsTest(ShelvesTestCase):

    def setUp(self):
        super().setUp()
        bpy.ops.wbp.shelf_add(name="Main")

    def test_add_operator_button(self):
        result = bpy.ops.wbp.shelf_button_add(label="Deselect", icon="restrict_select_on", tooltip="Deselect all",
                                              operator="object.select_all", properties='{"action": "DESELECT"}')
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(self.saved()["shelves"][0]["buttons"], [{
            "label": "Deselect", "icon": "RESTRICT_SELECT_ON", "tooltip": "Deselect all", "kind": "OPERATOR",
            "operator": "object.select_all", "properties": {"action": "DESELECT"},
        }])

    def test_unknown_icon_replaced(self):
        result, reports = helpers.operator_call(bpy.ops.wbp.shelf_button_add, label="A", icon="NOT_AN_ICON",
                                                operator="object.select_all")
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(reports[0].type, 'WARNING')
        self.assertEqual(self.library.shelves[0]["buttons"][0]["icon"], "NONE")

    def test_invalid_buttons_rejected(self):
        for kwargs in (
                {"label": "", "operator": "object.select_all"},
                {"label": "A", "operator": "nodot"},
                {"label": "A", "operator": "object.select_all", "properties": "{not json"},
                {"label": "A", "operator": "object.select_all", "properties": "[1]"},
                {"label": "A", "kind": 'SCRIPT', "script": ""},
        ):
            with self.subTest(**kwargs):
                result, reports = helpers.operator_call(bpy.ops.wbp.shelf_button_add, **kwargs)
                self.assertEqual(result, {'CANCELLED'})
                self.assertEqual(reports[-1].type, 'ERROR')
        self.assertEqual(self.library.shelves[0]["buttons"], [])

    def test_run_operator_button(self):
        a, b = helpers.objects_new("A", "B")
        helpers.select_objects(a, b)
        bpy.ops.wbp.shelf_button_add(label="Deselect", operator="object.select_all",
                                     properties='{"action": "DESELECT"}')
        self.assertEqual(bpy.ops.wbp.shelf_run(shelf=0, button=0), {'FINISHED'})
        self.assertEqual(bpy.context.selected_objects, [])

    def test_run_script_button(self):
        bpy.ops.wbp.shelf_button_add(label="Make", kind='SCRIPT', script=(
            "ob = D.objects.new('FromShelf', None)\n"
            "C.scene.collection.objects.link(ob)"
        ))
        self.assertEqual(bpy.ops.wbp.shelf_run(shelf=0, button=0), {'FINISHED'})
        self.assertIn("FromShelf", bpy.context.scene.objects)

    def test_script_error_reported(self):
        bpy.ops.wbp.shelf_button_add(label="Broken", kind='SCRIPT', script="1 / 0")
        result, reports = helpers.operator_call(bpy.ops.wbp.shelf_run, shelf=0, button=0)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[-1], helpers.Report('ERROR', "Broken failed: ZeroDivisionError: division by zero"))

    def test_operator_error_reported(self):
        self.library.button_add(0, self.library_module.button_new("Gone", operator="object.not_an_operator"))
        result, reports = helpers.operator_call(bpy.ops.wbp.shelf_run, shelf=0, button=0)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual(reports[-1], helpers.Report('ERROR', "Unknown operator 'object.not_an_operator'"))

    def test_missing_button(self):
        result, _reports = helpers.operator_call(bpy.ops.wbp.shelf_run, shelf=0, button=5)
        self.assertEqual(result, {'CANCELLED'})

    def test_tooltip(self):
        bpy.ops.wbp.shelf_button_add(label="A", tooltip="Custom tip", operator="object.select_all")
        bpy.ops.wbp.shelf_button_add(label="B", operator="object.select_all")
        description = bpy.types.WBP_OT_shelf_run.description

        class Properties:
            shelf = 0
            button = 0
        self.assertEqual(description(bpy.context, Properties), "Custom tip")
        Properties.button = 1
        self.assertEqual(description(bpy.context, Properties), "Run object.select_all")
        Properties.button = 9
        self.assertEqual(description(bpy.context, Properties), "Missing shelf button")

    def test_move_and_remove_buttons(self):
        for label in ("A", "B", "C"):
            bpy.ops.wbp.shelf_button_add(label=label, operator="object.select_all")
        bpy.ops.wbp.shelf_button_move(shelf=0, button=0, offset=1)
        self.assertEqual([button["label"] for button in self.library.shelves[0]["buttons"]], ["B", "A", "C"])
        bpy.ops.wbp.shelf_button_move(shelf=0, button=2, offset=-5)
        self.assertEqual([button["label"] for button in self.library.shelves[0]["buttons"]], ["C", "B", "A"])
        bpy.ops.wbp.shelf_button_remove(shelf=0, button=1)
        self.assertEqual([button["label"] for button in self.saved()["shelves"][0]["buttons"]], ["C", "A"])

    def test_add_to_shown_shelf(self):
        bpy.ops.wbp.shelf_add(name="Second")
        bpy.ops.wbp.shelf_activate(index=1)
        bpy.ops.wbp.shelf_button_add(label="A", operator="object.select_all")
        self.assertEqual(len(self.library.shelves[1]["buttons"]), 1)
        self.assertEqual(len(self.library.shelves[0]["buttons"]), 0)


class SharingTest(ShelvesTestCase):

    def setUp(self):
        super().setUp()
        bpy.ops.wbp.shelf_add(name="Rigging")
        bpy.ops.wbp.shelf_button_add(label="Bone", operator="armature.bone_primitive_add")
        bpy.ops.wbp.shelf_add(name="Other")
        self.export_filepath = os.path.join(self.tempdir.name, "shared.json")

    def test_export_shown_shelf(self):
        bpy.ops.wbp.shelf_activate(index=0)
        self.assertEqual(bpy.ops.wbp.shelf_export(filepath=self.export_filepath), {'FINISHED'})
        with open(self.export_filepath, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual([shelf["name"] for shelf in data["shelves"]], ["Rigging"])

    def test_round_trip_renames_duplicates(self):
        bpy.ops.wbp.shelf_export(filepath=self.export_filepath, use_all=True)
        result, reports = helpers.operator_call(bpy.ops.wbp.shelf_import, filepath=self.export_filepath)
        self.assertEqual(result, {'FINISHED'})
        self.assertEqual(reports, [helpers.Report('INFO', "Imported 2 shelves")])
        self.assertEqual([shelf["name"] for shelf in self.library.shelves],
                         ["Rigging", "Other", "Rigging (2)", "Other (2)"])
        self.assertEqual(self.library.shelves[2]["buttons"], self.library.shelves[0]["buttons"])

    def test_import_replace(self):
        bpy.ops.wbp.shelf_export(filepath=self.export_filepath, use_all=True)
        self.library.shelves[0]["buttons"].clear()
        bpy.ops.wbp.shelf_import(filepath=self.export_filepath, use_replace=True)
        self.assertEqual([shelf["name"] for shelf in self.library.shelves], ["Rigging", "Other"])
        self.assertEqual(len(self.library.shelves[0]["buttons"]), 1)

    def test_import_invalid(self):
        with open(self.export_filepath, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "shelves": [{"name": "S", "buttons": [{"label": "A"}]}]}, fh)
        result, reports = helpers.operator_call(bpy.ops.wbp.shelf_import, filepath=self.export_filepath)
        self.assertEqual(result, {'CANCELLED'})
        self.assertIn("shelves[0].buttons[0].kind", reports[-1].message)
        self.assertEqual(len(self.library.shelves), 2)


class TextSelectionTest(ShelvesTestCase):

    def setUp(self):
        super().setUp()
        self.text = bpy.data.texts.new("tool.py")
        self.text.write("line one\nline two\nline three")

    def select(self, line_start, char_start, line_end, char_end):
        self.text.current_line_index = line_start
        self.text.current_character = char_start
        self.text.select_end_line_index = line_end
        self.text.select_end_character = char_end

    def test_no_selection_is_whole_text(self):
        self.select(1, 2, 1, 2)
        self.assertEqual(self.shelves.selected_text(self.text), "line one\nline two\nline three")

    def test_single_line(self):
        self.select(1, 5, 1, 8)
        self.assertEqual(self.shelves.selected_text(self.text), "two")

    def test_multi_line(self):
        self.select(0, 5, 2, 4)
        self.assertEqual(self.shelves.selected_text(self.text), "one\nline two\nline")

    def test_backwards_selection(self):
        self.select(2, 4, 0, 5)
        self.assertEqual(self.shelves.selected_text(self.text), "one\nline two\nline")

    def test_operator_needs_text_editor(self):
        self.assertFalse(bpy.ops.wbp.shelf_button_from_text.poll())

    def test_context_menu_entry(self):
        draw_funcs = bpy.types.TEXT_MT_context_menu._dyn_ui_initialize()
        self.assertIn(self.shelves._text_context_menu_draw, draw_funcs)


if __name__ == "__main__":
    harness.main()
