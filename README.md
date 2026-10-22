# Blender Workbench Plus

[![Tests](https://github.com/aeflorezm/blender-workbench-plus/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/aeflorezm/blender-workbench-plus/actions/workflows/tests.yml)

Workflow and pipeline tools for Blender, built to the same quality bar as Blender itself.

> **Status:** early development. Build the package with `python tools/build_extension.py` (see *Development*).

## Features

- **Command Server:** a built-in remote control server for Blender (JSON-RPC over localhost, token auth, structured errors, batching, notifications) for IDEs, pipelines and AI agents. See [Command Server](#command-server).
- **Depsgraph Inspector:** see what gets re-evaluated, what the active object depends on, and how long each frame takes; export traces to Perfetto. See [Depsgraph Inspector](#depsgraph-inspector).
- **Quick Attributes:** fast multi-object channel editing with virtual sliders. See [Quick Attributes](#quick-attributes).
- **Shelves:** shareable button bars for your own scripts and operators. See [Shelves](#shelves).
- **Script Editor with command echo:** see the Python behind every UI action. See [Script Editor](#script-editor).
- **Driven Keys:** create driver relationships by posing, without writing expressions. See [Driven Keys](#driven-keys).

Most features ship as a regular **Blender extension** (works on stock Blender 5.2 LTS). Features that need core changes live in a separate Blender branch, and generally useful improvements are proposed upstream to Blender.

## Command Server

Lets other programs control a running Blender: run Python, call operators, read and write properties
(also in batches that undo as one step) and receive events such as frame changes or file loads.

- Start it from *Preferences → Add-ons → Workbench Plus → Command Server* (or enable *Start Automatically*).
  It only listens on `127.0.0.1`.
- Headless: `blender -b --python-expr "import bpy; bpy.ops.wbp.command_server_serve()"` serves until a client
  sends `shutdown`.
- Clients read the port and a per-session token from `command_server.json` in the add-on's user directory.

```python
from client import Client  # workbench_plus/command_server/client.py, a single file without dependencies.

with Client.from_discovery_file(path_to_command_server_json) as blender:
    blender.call("call_operator", operator="mesh.primitive_cube_add", properties={"location": [0, 0, 1]})
    print(blender.call("get_property", path="active_object.location", root="context"))
```

The protocol (methods, values, error codes, events) is described in [`docs/command_server.md`](docs/command_server.md).

## Depsgraph Inspector

*3D Viewport → Sidebar → Workbench → Depsgraph Inspector*

- **Record** lists the data-blocks each evaluation updated, with what changed (transform, geometry, shading)
  and how long it took. Recording only installs its handlers while it is on.
- **Relations** shows the data-blocks the active object uses (parent, constraint and modifier targets, drivers,
  action...) and the ones that use it, with a button to re-evaluate any of them and time it.
- **Profile** steps through the scene's frame range, then lists the slowest frames and the data-blocks updated
  in the most expensive evaluations. *Export Recording* saves it as JSON or as a Chrome trace that opens in
  [Perfetto](https://ui.perfetto.dev).

Times are measured per evaluation: Python has no hooks inside the evaluation of each object, so the ranking of
data-blocks points at suspects rather than splitting the time between them.

## Quick Attributes

*3D Viewport → Sidebar → Workbench → Quick Attributes*

- Shows the location, rotation and scale channels of the active object; editing a value sets it on **every
  selected object**. Locked channels are skipped.
- **Click a channel name** (X, Y, Z) and move the mouse to offset that channel on every selected object, each
  keeping its own value; click to confirm, right-click or Esc to cancel. Hold Shift for finer changes, Ctrl for steps.
- The ▾ button of each channel keys it, keys a breakdown, locks/unlocks it or mutes/unmutes its animation,
  on every selected object.
- Numeric custom properties of the active object are listed too, with a button to copy each one to the selection.

## Shelves

*3D Viewport → Sidebar → Workbench → Shelves*

- Tabs of buttons; each button runs an **operator** (with properties given as JSON) or a **Python script**,
  with its own icon and tooltip. The gear icon shows the controls to rename, reorder and remove.
- In the Text Editor, *right-click → Add to Shelf* turns the selected text (or the whole text) into a button.
- Shelves are saved to `shelves.json` in the add-on's user directory after every change.
  *Export* writes the shown shelf (or all of them) to a file that others can *Import*;
  shelves with a name that already exists are renamed, or replaced with the *Replace* option.

## Script Editor

*Text Editor → Sidebar → Workbench → Script Editor*

- **Run Selection** (Ctrl Shift Enter) runs the selected lines, or the whole text, in a namespace kept between
  runs (with `bpy`, `C` for the context and `D` for `bpy.data`). The code and its output or traceback are
  written to the *Script Log* text, which can be edited and run again like any other text.
- **Echo Operators** writes every operator run from the interface to the log as the Python call that repeats it.
- **Echo Property Changes** writes changes of object location, rotation, scale and visibility, and of the
  current frame, as Python assignments.

The log is a regular text data-block, so it is saved with the file; clear it with the trash button.

## Driven Keys

*3D Viewport → Sidebar → Workbench → Driven Keys*

1. Choose the driver channel (e.g. *Rotation X*) and *Load Driver*: it uses the active object, or the active
   bone in Pose Mode.
2. Choose the driven channels and *Load Driven*: they use the other selected objects, or bones.
3. Turn on *Posing*, pose the driver and the driven channels, and press *Key*. Repeat for each pose,
   then turn *Posing* off: the driver now moves the driven channels, interpolating between the keyed poses.

Each driven channel gets a regular Blender driver whose F-curve maps driver values to driven values, so it can
also be refined in the Drivers editor. *Relationships* lists them, to load one again for more keys or remove it.

## Development

The extension lives in [`workbench_plus/`](workbench_plus). Each feature is a sub-module with its own
`register()`/`unregister()` and can be turned on or off under
*Preferences → Add-ons → Workbench Plus → Features*.

Run the test suite (Blender 5.2 LTS or newer, Python 3.10 or newer to launch it):

```
python tools/test.py                              # The whole suite.
python tools/test.py --validate                   # Validate the extension manifest first.
python tools/test.py -k toggle                    # Only tests matching "toggle".
python tools/test.py --pattern "test_reg*.py"     # Only some test files.
python tools/test.py --blender PATH/TO/blender    # A specific Blender binary.
```

The Blender binary is taken from `--blender`, then the `BLENDER_BIN` environment variable,
then a local build, then `blender` in the `PATH`.
Tests run with the same strict settings as Blender's own Python tests
(`--factory-startup --debug-memory --debug-exit-on-error --python-exit-code 1`), so memory leaks
and Python errors fail the run. See `python tools/test.py --help` for all options.

The runner loads the extension straight from this source tree (as a temporary local extension repository),
starts every test from an empty file, writes a JUnit report to `build/test-results/junit.xml`
and exits with a non-zero code when a test fails.

Inside Blender, without the launcher:

```
blender -b --factory-startup --python tools/run_tests.py -- -k toggle
blender --command extension validate workbench_plus
```

Build the installable package (`dist/workbench_plus-<version>.zip`, install it from
*Preferences → Get Extensions → Install from Disk*):

```
python tools/build_extension.py                   # Into dist/.
python tools/build_extension.py --output-dir DIR  # Into DIR (created when missing).
```

Every push to `main` and every pull request runs the same suite in the latest official Blender 5.2 LTS
release on Linux ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)); the JUnit report is attached
to each run.

Python code follows Blender's formatting rules (`autopep8` with the settings in [`pyproject.toml`](pyproject.toml)).

## Español

Herramientas de flujo de trabajo y pipeline para Blender. La mayoría se instala como extensión normal; lo que necesita cambios en el núcleo vive en una rama aparte de Blender, y las mejoras de interés general se proponen a Blender oficial.

## License

GPL-2.0-or-later, same as Blender. See [`LICENSE`](LICENSE).
