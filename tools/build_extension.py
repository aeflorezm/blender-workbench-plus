# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Build the Workbench Plus extension package (a ``.zip`` installable from Blender's Preferences).

Runs ``blender --command extension build`` on ``workbench_plus/``, which also validates the manifest.
The Blender binary is looked up like ``tools/test.py`` does.

Usage::

   python tools/build_extension.py [--blender PATH] [--output-dir DIR]
"""

__all__ = (
    "main",
)

import argparse
import os
import sys

# `tools/` is the script directory, so the launcher's helpers can be imported directly.
from test import (
    EXTENSION_DIR,
    SOURCE_DIR,
    blender_bin_find,
    run,
)

OUTPUT_DIR_DEFAULT = os.path.join(SOURCE_DIR, "dist")


def argparse_create():
    parser = argparse.ArgumentParser(
        prog="python tools/build_extension.py",
        description="Build the Workbench Plus extension package with Blender.",
    )
    parser.add_argument(
        "--blender",
        default=None,
        metavar="PATH",
        help="Blender binary to use (default: $BLENDER_BIN, a local build, or 'blender' in the PATH)",
    )
    parser.add_argument(
        "--output-dir",
        default=OUTPUT_DIR_DEFAULT,
        metavar="DIR",
        help="Directory to write the package to, created when missing (default: %(default)s)",
    )
    return parser


def main():
    args = argparse_create().parse_args()

    blender_bin = blender_bin_find(args.blender)
    if blender_bin is None:
        return 1

    # `extension build` fails when the output directory does not exist.
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    returncode = run([
        blender_bin,
        "--factory-startup",
        "--command", "extension", "build",
        "--source-dir", EXTENSION_DIR,
        "--output-dir", output_dir,
    ])
    if returncode != 0:
        print("Error: extension build failed (exit code {:d})".format(returncode), file=sys.stderr)
    return returncode


if __name__ == "__main__":
    sys.exit(main())
