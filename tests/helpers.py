# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Utilities shared by the tests: minimal scenes, approximate comparison of transforms
and capture of the reports emitted by operators.
"""

__all__ = (
    "Report",
    "TransformAssertions",
    "capture_reports",
    "objects_new",
    "operator_call",
    "select_objects",
)

import contextlib
import ctypes
import os
import sys
import tempfile
from typing import NamedTuple

import bpy

# A unit cube centered at the origin (vertices, faces), small enough to keep tests fast.
_CUBE_VERTS = tuple((x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5))
_CUBE_FACES = (
    (0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
    (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3),
)


# -----------------------------------------------------------------------------
# Scenes

def objects_new(*names, data='EMPTY', collection=None):
    """
    Create one object per name and link it to ``collection`` (default: the scene collection).

    :arg data: ``'EMPTY'`` for empties or ``'MESH'`` for a unit cube mesh per object.
    :return: The new objects, in the order of ``names``.
    """
    if data not in {'EMPTY', 'MESH'}:
        raise ValueError("data must be 'EMPTY' or 'MESH', not {!r}".format(data))
    if collection is None:
        collection = bpy.context.scene.collection

    objects = []
    for name in names:
        if data == 'MESH':
            mesh = bpy.data.meshes.new(name)
            mesh.from_pydata(_CUBE_VERTS, (), _CUBE_FACES)
            mesh.validate()
        else:
            mesh = None
        ob = bpy.data.objects.new(name, mesh)
        collection.objects.link(ob)
        objects.append(ob)
    return objects


def select_objects(*objects, active=None):
    """
    Select exactly ``objects`` in the current view layer and make ``active`` the active object
    (default: the last of ``objects``, or none when ``objects`` is empty).
    """
    view_layer = bpy.context.view_layer
    for ob in view_layer.objects:
        ob.select_set(False)
    for ob in objects:
        ob.select_set(True)
    if active is None and objects:
        active = objects[-1]
    view_layer.objects.active = active


# -----------------------------------------------------------------------------
# Transforms

def _flatten(value):
    """Return the components of a vector, matrix, quaternion, Euler or plain sequence as a flat tuple."""
    if hasattr(value, "row") or (len(value) and hasattr(value[0], "__len__")):
        return tuple(component for row in value for component in row)
    return tuple(value)


class TransformAssertions:
    """
    Mix-in for ``unittest.TestCase`` comparing ``mathutils`` values (or plain sequences) with a tolerance.

    Failure messages name the component with the largest difference.
    """

    def _assert_components_almost_equal(self, first, second, delta, kind, msg):
        a = _flatten(first)
        b = _flatten(second)
        if len(a) != len(b):
            self.fail(self._formatMessage(msg, "{:s} sizes differ: {:d} != {:d}".format(kind, len(a), len(b))))
        diffs = [abs(x - y) for x, y in zip(a, b)]
        index = max(range(len(diffs)), key=diffs.__getitem__) if diffs else 0
        if diffs and diffs[index] > delta:
            self.fail(self._formatMessage(msg, (
                "{:s}s differ by {:g} at component {:d} ({!r} != {!r}, delta {:g}):\n{!r}\n{!r}"
            ).format(kind, diffs[index], index, a[index], b[index], delta, first, second)))

    def assertVectorAlmostEqual(self, first, second, delta=1e-5, msg=None):
        """Vectors, quaternions, Euler rotations or plain sequences of numbers."""
        self._assert_components_almost_equal(first, second, delta, "Vector", msg)

    def assertMatrixAlmostEqual(self, first, second, delta=1e-5, msg=None):
        """Matrices or nested sequences of numbers, compared component by component."""
        self._assert_components_almost_equal(first, second, delta, "Matrix", msg)


# -----------------------------------------------------------------------------
# Operator reports

class Report(NamedTuple):
    type: str
    """One of ``'DEBUG'``, ``'INFO'``, ``'OPERATOR'``, ``'PROPERTY'``, ``'WARNING'``, ``'ERROR'`` ..."""
    message: str


# Prefixes Blender prints for each report type (see `BKE_report_type_str`).
_REPORT_PREFIXES = (
    ("Debug: ", 'DEBUG'),
    ("Info: ", 'INFO'),
    ("Operator: ", 'OPERATOR'),
    ("Property: ", 'PROPERTY'),
    ("Warning: ", 'WARNING'),
    ("Error: ", 'ERROR'),
    ("Invalid Input Error: ", 'ERROR_INVALID_INPUT'),
    ("Invalid Context Error: ", 'ERROR_INVALID_CONTEXT'),
    ("Out Of Memory Error: ", 'ERROR_OUT_OF_MEMORY'),
)


def _reports_parse(text):
    reports = []
    for line in text.splitlines():
        for prefix, report_type in _REPORT_PREFIXES:
            if line.startswith(prefix):
                reports.append(Report(report_type, line[len(prefix):]))
                break
    return reports


def _c_stdout_flush():
    # Blender prints reports with C `printf`, which is fully buffered when stdout is a file.
    if sys.platform != "win32":
        ctypes.CDLL(None).fflush(None)


@contextlib.contextmanager
def capture_reports():
    """
    Collect the reports emitted while the block runs, as a list of :class:`Report`.

    Operators called from Python in background mode do not add their reports to
    ``WindowManager.reports``: Blender prints them instead. So the process standard output
    is redirected at the file descriptor level and parsed when the block exits;
    the captured output is echoed afterwards so nothing is hidden from the test log.

    Error reports also make ``bpy.ops`` raise ``RuntimeError``; that exception is not caught here
    (see :func:`operator_call`).
    """
    reports = []
    sys.stdout.flush()
    _c_stdout_flush()
    fd = sys.stdout.fileno()
    fd_saved = os.dup(fd)
    with tempfile.TemporaryFile() as tmp:
        os.dup2(tmp.fileno(), fd)
        try:
            yield reports
        finally:
            sys.stdout.flush()
            _c_stdout_flush()
            os.dup2(fd_saved, fd)
            os.close(fd_saved)
            tmp.seek(0)
            text = tmp.read().decode("utf-8", errors="replace")
            sys.stdout.write(text)
            reports.extend(_reports_parse(text))


def operator_call(operator, /, *args, **kwargs):
    """
    Call ``operator`` (e.g. ``bpy.ops.object.select_all``) and return ``(result, reports)``.

    ``result`` is the set returned by the operator, or ``{'CANCELLED'}`` when it failed with an error
    report (which ``bpy.ops`` raises as ``RuntimeError``). ``reports`` is a list of :class:`Report`;
    a failure that printed no error report (e.g. a failing ``poll()``) is added as an ``'ERROR'`` report.
    """
    error = None
    with capture_reports() as reports:
        try:
            result = operator(*args, **kwargs)
        except RuntimeError as ex:
            result = {'CANCELLED'}
            error = str(ex).strip()
    if error is not None and not any(report.type.startswith('ERROR') for report in reports):
        reports.append(Report('ERROR', error))
    return result, reports
