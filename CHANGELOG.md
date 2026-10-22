# Changelog

## Unreleased

- **Command Server:** MCP server (`mcp_server.py`, stdio, no dependencies) so AI assistants that support the
  Model Context Protocol can drive Blender; *Copy Command* / *Copy JSON* in the preferences.

## 0.1.0 (2026-10-21)

First release, for Blender 5.2 LTS.

- **Command Server:** remote control over JSON-RPC 2.0 on `127.0.0.1`, with a per-session token and a discovery
  file. Methods: `exec`, `eval`, `call_operator`, `get_property`, `set_property`, `batch` (one undo step),
  event subscriptions (frame changes, depsgraph updates, file load/save, undo/redo, progress) and `shutdown`.
  Also runs headless (`wbp.command_server_serve`). Includes a dependency-free Python client.
- **Depsgraph Inspector:** records what each evaluation updated and how long it took, shows what the active
  object uses and is used by, re-evaluates and times data-blocks, profiles frame ranges and exports JSON or
  Chrome traces (Perfetto).
- **Quick Attributes:** transform channels of the active object, edited on every selected object; a relative
  slider on the channel names; key, breakdown, lock and mute per channel; numeric custom properties.
- **Shelves:** tabs of operator and script buttons, saved as JSON, shareable through export/import;
  buttons from the selected text of the Text Editor.
- **Script Editor:** runs the selection of the Text Editor and logs the code and its output, together with the
  operators run from the interface and changes of transforms, visibility and the current frame, as Python.
- **Driven Keys:** driver relationships made by posing, for objects and bones.

Every feature can be turned off in the add-on preferences.
