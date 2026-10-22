# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Helpers shared by several features.
"""

__all__ = (
    "selected_text",
)


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
