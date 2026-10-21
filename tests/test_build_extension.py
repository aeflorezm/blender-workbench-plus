# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_build_extension.py
"""

import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
import zipfile

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_EXTENSION_FILEPATH = os.path.join(SOURCE_DIR, "tools", "build_extension.py")
MANIFEST_FILEPATH = os.path.join(SOURCE_DIR, "workbench_plus", "blender_manifest.toml")


def build_extension(*args):
    """Run ``tools/build_extension.py`` with this Blender, return the completed process."""
    return subprocess.run(
        [sys.executable, BUILD_EXTENSION_FILEPATH, "--blender", bpy.app.binary_path, *args],
        capture_output=True,
        text=True,
    )


class BuildExtensionTest(unittest.TestCase):
    """``tools/build_extension.py`` produces an installable package."""

    def test_build_into_missing_directory(self):
        with open(MANIFEST_FILEPATH, "rb") as fh:
            manifest = tomllib.load(fh)
        with tempfile.TemporaryDirectory() as tempdir:
            output_dir = os.path.join(tempdir, "does", "not", "exist")
            proc = build_extension("--output-dir", output_dir)
            self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

            zip_filepath = os.path.join(output_dir, "{:s}-{:s}.zip".format(manifest["id"], manifest["version"]))
            self.assertTrue(os.path.isfile(zip_filepath), msg=os.listdir(output_dir))
            with zipfile.ZipFile(zip_filepath) as zf:
                names = zf.namelist()
            self.assertIn("blender_manifest.toml", names)
            self.assertIn("__init__.py", names)
            self.assertFalse([name for name in names if "__pycache__" in name])

    def test_missing_blender_fails(self):
        with tempfile.TemporaryDirectory() as tempdir:
            proc = subprocess.run(
                [
                    sys.executable, BUILD_EXTENSION_FILEPATH,
                    "--blender", os.path.join(tempdir, "no-blender-here"),
                    "--output-dir", tempdir,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertIn("not found", proc.stderr)
            self.assertEqual(os.listdir(tempdir), [])


if __name__ == "__main__":
    harness.main()
