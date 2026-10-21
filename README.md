# Blender Workbench Plus

[![Tests](https://github.com/aeflorezm/blender-workbench-plus/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/aeflorezm/blender-workbench-plus/actions/workflows/tests.yml)

Workflow and pipeline tools for Blender, built to the same quality bar as Blender itself.

> **Status:** early development. Build the package with `python tools/build_extension.py` (see *Development*).

## Features

- **Command Server:** a built-in remote control server for Blender (JSON-RPC over localhost, token auth, structured errors, batching, notifications) for IDEs, pipelines and AI agents. See [Command Server](#command-server).

## Planned features

- **Depsgraph Inspector:** see what gets re-evaluated, why, and how long it takes; export traces to Perfetto.
- **Quick Attributes:** fast multi-object channel editing with virtual sliders.
- **Shelves:** shareable button bars for your own scripts and operators.
- **Script Editor with command echo:** see the Python behind every UI action.
- **Driven Keys UI:** create driver relationships by posing, without writing expressions.

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
