# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Test harness: loads the extension from this source tree and runs ``unittest`` suites inside Blender.

The source tree is registered as a temporary local extension repository, so the add-on is loaded
exactly as an installed extension would be (``bl_ext.<repo>.workbench_plus``) without copying files.
Every test starts from an empty factory-startup file.
"""

import os
import sys
import time
import unittest
import xml.etree.ElementTree as ET

import addon_utils
import bpy

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REPO_MODULE = "wbp_tests"
ADDON_MODULE = "bl_ext.{:s}.workbench_plus".format(REPO_MODULE)


def _repo_find():
    for repo in bpy.context.preferences.extensions.repos:
        if repo.module == REPO_MODULE:
            return repo
    return None


def extension_repo_ensure():
    """Register the source tree as a local extension repository (once per session)."""
    if _repo_find() is None:
        bpy.context.preferences.extensions.repos.new(
            name="Workbench Plus (tests)",
            module=REPO_MODULE,
            custom_directory=SOURCE_DIR,
            source='USER',
        )


def extension_repo_remove():
    if (repo := _repo_find()) is not None:
        bpy.context.preferences.extensions.repos.remove(repo)


def _raise(ex):
    raise ex


def addon_enable():
    """Enable the add-on, raising on any error in its ``register()``."""
    extension_repo_ensure()
    return addon_utils.enable(ADDON_MODULE, default_set=True, handle_error=_raise)


def addon_disable():
    """Disable the add-on, raising on any error in its ``unregister()``."""
    addon_utils.disable(ADDON_MODULE, default_set=True, handle_error=_raise)


def addon_is_enabled():
    return addon_utils.check(ADDON_MODULE) == (True, True)


def addon_module():
    """The add-on package, only valid while it is enabled."""
    return sys.modules[ADDON_MODULE]


def addon_preferences():
    return bpy.context.preferences.addons[ADDON_MODULE].preferences


def scene_reset():
    """
    Load an empty factory-startup file.

    ``wm.read_factory_settings`` is not used because it also resets the preferences,
    which removes the test repository and disables the add-on.
    """
    bpy.ops.wm.read_homefile(use_factory_startup=True, use_empty=True)


class BlenderTestResult(unittest.TextTestResult):
    """Text result that resets the scene before each test and records timings for the JUnit report."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tuples of: (test, outcome, details, seconds).
        self.records = []
        self._time_start = 0.0

    def startTest(self, test):
        scene_reset()
        self._time_start = time.perf_counter()
        super().startTest(test)

    def _record(self, test, outcome, details=""):
        self.records.append((test, outcome, details, time.perf_counter() - self._time_start))

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, "success")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, "failure", self.failures[-1][1])

    def addError(self, test, err):
        super().addError(test, err)
        # Also called for errors in class and module fixtures, which are reported as their own entries.
        self._record(test, "error", self.errors[-1][1])

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._record(test, "skipped", reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._record(test, "success")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._record(test, "failure", "Unexpected success")


def junit_write(result, filepath, suite_name="workbench_plus"):
    """Write ``result`` as a JUnit XML file (the report format CI systems understand)."""
    count = {"failure": 0, "error": 0, "skipped": 0}
    for _test, outcome, _details, _seconds in result.records:
        if outcome in count:
            count[outcome] += 1

    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", {
        "name": suite_name,
        "tests": str(len(result.records)),
        "failures": str(count["failure"]),
        "errors": str(count["error"]),
        "skipped": str(count["skipped"]),
        "time": "{:.3f}".format(sum(record[3] for record in result.records)),
    })
    for test, outcome, details, seconds in result.records:
        test_id = test.id()
        classname, _, name = test_id.rpartition(".")
        case = ET.SubElement(suite, "testcase", {
            "classname": classname or test_id,
            "name": name,
            "time": "{:.3f}".format(seconds),
        })
        if outcome == "success":
            continue
        message = details.strip().splitlines()[-1] if details.strip() else ""
        ET.SubElement(case, outcome, {"message": message}).text = details

    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    ET.ElementTree(root).write(filepath, encoding="utf-8", xml_declaration=True)


def run(suite, *, junit_filepath=None, verbosity=2):
    """Run ``suite`` and return True when every test passed."""
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=verbosity, resultclass=BlenderTestResult)
    try:
        result = runner.run(suite)
    finally:
        if addon_is_enabled():
            addon_disable()
        extension_repo_remove()

    if junit_filepath:
        junit_write(result, junit_filepath)
        print("JUnit report:", junit_filepath)
    return result.wasSuccessful()


def main(module_name="__main__"):
    """Run a single test file: ``blender -b --factory-startup --python tests/test_<name>.py``."""
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[module_name])
    sys.exit(0 if run(suite) else 1)
