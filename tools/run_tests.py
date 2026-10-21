# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Run the Workbench Plus test suite inside Blender.

Usage::

   blender -b --factory-startup --python tools/run_tests.py -- [--pattern "test_*.py"] [--junit PATH] [-k NAME]

Exits with a non-zero code when any test fails, so it can be used from CI.
"""

import argparse
import os
import sys
import unittest

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_DIR = os.path.join(SOURCE_DIR, "tests")


def argparse_create():
    parser = argparse.ArgumentParser(
        prog="blender -b --factory-startup --python tools/run_tests.py --",
        description="Run the Workbench Plus test suite inside Blender.",
    )
    parser.add_argument(
        "--pattern",
        default="test_*.py",
        help="File name pattern of the test modules to run (default: %(default)s)",
    )
    parser.add_argument(
        "--junit",
        default=os.path.join(SOURCE_DIR, "build", "test-results", "junit.xml"),
        metavar="PATH",
        help="Where to write the JUnit XML report, an empty string disables it (default: %(default)s)",
    )
    parser.add_argument(
        "-k",
        dest="name_patterns",
        action="append",
        default=[],
        metavar="NAME",
        help="Only run tests whose name matches this substring or wildcard pattern (can be repeated)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Only print a summary instead of one line per test",
    )
    return parser


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = argparse_create().parse_args(argv)

    if TESTS_DIR not in sys.path:
        sys.path.insert(0, TESTS_DIR)
    import harness

    loader = unittest.TestLoader()
    if args.name_patterns:
        loader.testNamePatterns = [
            pattern if "*" in pattern else "*{:s}*".format(pattern) for pattern in args.name_patterns
        ]
    suite = loader.discover(TESTS_DIR, pattern=args.pattern, top_level_dir=TESTS_DIR)

    if suite.countTestCases() == 0:
        print("No tests found in", repr(TESTS_DIR), "matching", repr(args.pattern))
        sys.exit(1)

    ok = harness.run(suite, junit_filepath=args.junit or None, verbosity=1 if args.quiet else 2)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
