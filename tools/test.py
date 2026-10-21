# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Run the Workbench Plus test suite from a regular Python interpreter.

Finds a Blender binary and runs ``tools/run_tests.py`` inside it, using the same strict
command line arguments as Blender's own Python CTests. The exit code of Blender is propagated.

Usage::

   python tools/test.py [--blender PATH] [--validate] [-k NAME] [--pattern GLOB] [--junit PATH] [-q]

The Blender binary is looked up in this order:

- The ``--blender`` argument.
- The ``BLENDER_BIN`` environment variable.
- A local Blender build next to this repository (see ``LOCAL_BUILD_BLENDER_BIN``).
- ``blender`` in the ``PATH``.
"""

__all__ = (
    "main",
)

import argparse
import os
import shutil
import subprocess
import sys

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_DIR = os.path.join(SOURCE_DIR, "workbench_plus")
RUN_TESTS_FILEPATH = os.path.join(SOURCE_DIR, "tools", "run_tests.py")

LOCAL_BUILD_BLENDER_BIN = os.path.join("C:\\", "dev", "build_windows_x64_vc17_Release", "bin", "Release", "blender.exe")

# Keep in sync with `TEST_BLENDER_EXE_PARAMS` in Blender's `tests/CMakeLists.txt`.
TEST_BLENDER_EXE_PARAMS = (
    "--background",
    "--console-crash-handler",
    "--factory-startup",
    "--debug-memory",
    "--debug-exit-on-error",
    "--python-exit-code", "1",
)


def argparse_create():
    parser = argparse.ArgumentParser(
        prog="python tools/test.py",
        description="Run the Workbench Plus test suite in Blender, with the same strict settings as Blender's CTests.",
    )
    parser.add_argument(
        "--blender",
        default=None,
        metavar="PATH",
        help="Blender binary to use (default: $BLENDER_BIN, a local build, or 'blender' in the PATH)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the extension manifest before running the tests",
    )

    # Forwarded as-is to `tools/run_tests.py`.
    group = parser.add_argument_group("test selection (forwarded to tools/run_tests.py)")
    group.add_argument(
        "--pattern",
        default=None,
        help="File name pattern of the test modules to run (default: test_*.py)",
    )
    group.add_argument(
        "--junit",
        default=None,
        metavar="PATH",
        help="Where to write the JUnit XML report, an empty string disables it (default: build/test-results/junit.xml)",
    )
    group.add_argument(
        "-k",
        dest="name_patterns",
        action="append",
        default=[],
        metavar="NAME",
        help="Only run tests whose name matches this substring or wildcard pattern (can be repeated)",
    )
    group.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only print a summary instead of one line per test",
    )
    return parser


def blender_bin_find(blender_bin_arg):
    """
    Return the path of the Blender binary to use, or None (after printing why) when there is none.
    """
    if blender_bin_arg is not None:
        source, blender_bin = "--blender", blender_bin_arg
    elif os.environ.get("BLENDER_BIN"):
        source, blender_bin = "BLENDER_BIN", os.environ["BLENDER_BIN"]
    elif os.path.isfile(LOCAL_BUILD_BLENDER_BIN):
        return LOCAL_BUILD_BLENDER_BIN
    else:
        blender_bin = shutil.which("blender")
        if blender_bin is None:
            print(
                "Error: Blender not found. Pass --blender PATH, set BLENDER_BIN, "
                "or add 'blender' to the PATH.",
                file=sys.stderr,
            )
        return blender_bin

    # An explicit choice must not silently fall back to another Blender.
    if not os.path.isfile(blender_bin):
        blender_bin_found = shutil.which(blender_bin)
        if blender_bin_found is None:
            print("Error: Blender binary from {:s} not found: {!r}".format(source, blender_bin), file=sys.stderr)
            return None
        blender_bin = blender_bin_found
    return blender_bin


def run_tests_args(args):
    """
    Return the arguments to forward to `tools/run_tests.py`.
    """
    argv = []
    if args.pattern is not None:
        argv.extend(("--pattern", args.pattern))
    if args.junit is not None:
        argv.extend(("--junit", args.junit))
    for name_pattern in args.name_patterns:
        argv.extend(("-k", name_pattern))
    if args.quiet:
        argv.append("-q")
    return argv


def run(command):
    print("Running:", subprocess.list2cmdline(command), flush=True)
    return subprocess.call(command, cwd=SOURCE_DIR)


def main():
    args = argparse_create().parse_args()

    blender_bin = blender_bin_find(args.blender)
    if blender_bin is None:
        return 1

    if args.validate:
        returncode = run([blender_bin, "--factory-startup", "--command", "extension", "validate", EXTENSION_DIR])
        if returncode != 0:
            print("Error: extension validation failed (exit code {:d})".format(returncode), file=sys.stderr)
            return returncode

    return run([
        blender_bin,
        *TEST_BLENDER_EXE_PARAMS,
        "--python", RUN_TESTS_FILEPATH,
        "--",
        *run_tests_args(args),
    ])


if __name__ == "__main__":
    sys.exit(main())
