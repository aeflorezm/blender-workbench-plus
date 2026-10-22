# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Helpers shared by several features.
"""

__all__ = (
    "classes_register",
    "classes_unregister",
    "selected_text",
)

import bpy


def classes_register(classes):
    """
    Register ``classes`` in order. When one fails, the ones already registered are unregistered
    before the error is raised, so a failed registration leaves nothing behind.
    """
    registered = []
    try:
        for cls in classes:
            bpy.utils.register_class(cls)
            registered.append(cls)
    except BaseException:
        for cls in reversed(registered):
            bpy.utils.unregister_class(cls)
        raise


def classes_unregister(classes):
    """Unregister ``classes`` in reverse order."""
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


def selected_text(text):
    """The selected text of a Text data-block, or all of it when nothing is selected."""
    start = (text.current_line_index, text.current_character)
    end = (text.select_end_line_index, text.select_end_character)
    if start == end:
        return text.as_string()
    if end < start:
        start, end = end, start
    lines = [line.body for line in text.lines[start[0]:end[0] + 1]]
    if len(lines) == 1:
        return lines[0][start[1]:end[1]]
    lines[0] = lines[0][start[1]:]
    lines[-1] = lines[-1][:end[1]]
    return "\n".join(lines)
