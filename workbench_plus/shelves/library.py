# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The shelves and their buttons, stored as JSON. Independent of the interface.

Format (version 1)::

   {"version": 1, "shelves": [
       {"name": "Modeling", "buttons": [
           {"label": "Cube", "icon": "MESH_CUBE", "tooltip": "Add a cube",
            "kind": "OPERATOR", "operator": "mesh.primitive_cube_add", "properties": {"size": 2.0}},
           {"label": "Hello", "icon": "NONE", "tooltip": "", "kind": "SCRIPT", "script": "print('hello')"}
       ]}
   ]}
"""

__all__ = (
    "FORMAT_VERSION",
    "Library",
    "button_new",
    "validate",
)

import copy
import json
import os

FORMAT_VERSION = 1

BUTTON_KINDS = ("OPERATOR", "SCRIPT")


def _fail(path, message):
    raise ValueError("{:s}: {:s}".format(path, message))


def _check_str(value, path, *, allow_empty=True):
    if not isinstance(value, str):
        _fail(path, "expected a string")
    if not allow_empty and not value.strip():
        _fail(path, "must not be empty")


def validate(data):
    """Raise ``ValueError`` (naming the offending entry) unless ``data`` is a valid shelves document."""
    if not isinstance(data, dict):
        _fail("document", "expected an object")
    if data.get("version") != FORMAT_VERSION:
        _fail("version", "unsupported version {!r}, expected {:d}".format(data.get("version"), FORMAT_VERSION))
    shelves = data.get("shelves")
    if not isinstance(shelves, list):
        _fail("shelves", "expected a list")
    for shelf_index, shelf in enumerate(shelves):
        shelf_path = "shelves[{:d}]".format(shelf_index)
        if not isinstance(shelf, dict):
            _fail(shelf_path, "expected an object")
        _check_str(shelf.get("name"), shelf_path + ".name", allow_empty=False)
        buttons = shelf.get("buttons")
        if not isinstance(buttons, list):
            _fail(shelf_path + ".buttons", "expected a list")
        for button_index, button in enumerate(buttons):
            path = "{:s}.buttons[{:d}]".format(shelf_path, button_index)
            if not isinstance(button, dict):
                _fail(path, "expected an object")
            _check_str(button.get("label"), path + ".label", allow_empty=False)
            _check_str(button.get("icon", "NONE"), path + ".icon")
            _check_str(button.get("tooltip", ""), path + ".tooltip")
            kind = button.get("kind")
            if kind not in BUTTON_KINDS:
                _fail(path + ".kind", "expected one of {!r}".format(BUTTON_KINDS))
            if kind == "OPERATOR":
                _check_str(button.get("operator"), path + ".operator", allow_empty=False)
                if "." not in button["operator"]:
                    _fail(path + ".operator", "expected 'module.name', e.g. 'mesh.primitive_cube_add'")
                if not isinstance(button.get("properties", {}), dict):
                    _fail(path + ".properties", "expected an object")
            else:
                _check_str(button.get("script"), path + ".script", allow_empty=False)


def button_new(label, *, kind="OPERATOR", icon="NONE", tooltip="", operator="", properties=None, script=""):
    button = {"label": label, "icon": icon, "tooltip": tooltip, "kind": kind}
    if kind == "OPERATOR":
        button["operator"] = operator
        button["properties"] = dict(properties or {})
    else:
        button["script"] = script
    return button


class Library:
    """The shelves, kept in memory and saved to ``filepath`` after every change made through it."""

    def __init__(self, filepath=None):
        self.filepath = filepath
        self.shelves = []

    # Persistence.

    def as_dict(self):
        return {"version": FORMAT_VERSION, "shelves": copy.deepcopy(self.shelves)}

    def load(self):
        """Load from :attr:`filepath`. A missing file is an empty library; an invalid one raises ``ValueError``."""
        if self.filepath is None or not os.path.exists(self.filepath):
            self.shelves = []
            return
        with open(self.filepath, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as ex:
                raise ValueError("{:s}: invalid JSON: {:s}".format(self.filepath, str(ex)))
        validate(data)
        self.shelves = data["shelves"]

    def save(self):
        if self.filepath is None:
            return
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        # Write next to the file first, so a failure never leaves a truncated library.
        filepath_tmp = self.filepath + ".tmp"
        with open(filepath_tmp, "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, indent=2, ensure_ascii=False)
        os.replace(filepath_tmp, self.filepath)

    # Shelves.

    def _name_unique(self, name, ignore_index=None):
        names = {shelf["name"] for index, shelf in enumerate(self.shelves) if index != ignore_index}
        if name not in names:
            return name
        number = 2
        while "{:s} ({:d})".format(name, number) in names:
            number += 1
        return "{:s} ({:d})".format(name, number)

    def shelf_add(self, name):
        """Add an empty shelf (renamed when the name is taken) and return its index."""
        _check_str(name, "name", allow_empty=False)
        self.shelves.append({"name": self._name_unique(name.strip()), "buttons": []})
        self.save()
        return len(self.shelves) - 1

    def shelf_remove(self, index):
        del self.shelves[index]
        self.save()

    def shelf_rename(self, index, name):
        _check_str(name, "name", allow_empty=False)
        self.shelves[index]["name"] = self._name_unique(name.strip(), ignore_index=index)
        self.save()

    def shelf_move(self, index, offset):
        """Move the shelf by ``offset`` positions (clamped), return its new index."""
        new_index = max(0, min(len(self.shelves) - 1, index + offset))
        self.shelves.insert(new_index, self.shelves.pop(index))
        self.save()
        return new_index

    # Buttons.

    def button_add(self, shelf_index, button):
        validate({"version": FORMAT_VERSION, "shelves": [{"name": "shelf", "buttons": [button]}]})
        self.shelves[shelf_index]["buttons"].append(button)
        self.save()
        return len(self.shelves[shelf_index]["buttons"]) - 1

    def button_remove(self, shelf_index, button_index):
        del self.shelves[shelf_index]["buttons"][button_index]
        self.save()

    def button_move(self, shelf_index, button_index, offset):
        buttons = self.shelves[shelf_index]["buttons"]
        new_index = max(0, min(len(buttons) - 1, button_index + offset))
        buttons.insert(new_index, buttons.pop(button_index))
        self.save()
        return new_index

    # Sharing.

    def export_shelves(self, filepath, indices=None):
        """Write the shelves at ``indices`` (default: all) to ``filepath``."""
        shelves = self.shelves if indices is None else [self.shelves[index] for index in indices]
        with open(filepath, "w", encoding="utf-8") as fh:
            json.dump({"version": FORMAT_VERSION, "shelves": shelves}, fh, indent=2, ensure_ascii=False)

    def import_shelves(self, filepath, *, replace=False):
        """
        Add the shelves of ``filepath``. Shelves named like existing ones are renamed,
        or replace them when ``replace`` is True. Returns the number of shelves imported.
        """
        with open(filepath, encoding="utf-8") as fh:
            try:
                data = json.load(fh)
            except ValueError as ex:
                raise ValueError("invalid JSON: {:s}".format(str(ex)))
        validate(data)
        for shelf in data["shelves"]:
            existing = [index for index, other in enumerate(self.shelves) if other["name"] == shelf["name"]]
            if replace and existing:
                self.shelves[existing[0]] = shelf
            else:
                shelf["name"] = self._name_unique(shelf["name"])
                self.shelves.append(shelf)
        self.save()
        return len(data["shelves"])
