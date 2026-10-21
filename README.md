# Blender Workbench Plus

Workflow and pipeline tools for Blender, built to the same quality bar as Blender itself.

> **Status:** early development. Nothing to install yet.

## Planned features

- **Command Server:** a built-in remote control server for Blender (JSON-RPC over localhost, token auth, structured errors, batching, notifications) for IDEs, pipelines and AI agents.
- **Depsgraph Inspector:** see what gets re-evaluated, why, and how long it takes; export traces to Perfetto.
- **Quick Attributes:** fast multi-object channel editing with virtual sliders.
- **Shelves:** shareable button bars for your own scripts and operators.
- **Script Editor with command echo:** see the Python behind every UI action.
- **Driven Keys UI:** create driver relationships by posing, without writing expressions.

Most features ship as a regular **Blender extension** (works on stock Blender 5.2 LTS). Features that need core changes live in a separate Blender branch, and generally useful improvements are proposed upstream to Blender.

## Development

The extension lives in [`workbench_plus/`](workbench_plus). Each feature is a sub-module with its own
`register()`/`unregister()` and can be turned on or off under
*Preferences → Add-ons → Workbench Plus → Features*.

Validate the extension manifest:

```
blender --command extension validate workbench_plus
```

Run the test suite (Blender 5.2 LTS or newer):

```
blender -b --factory-startup --python tools/run_tests.py
blender -b --factory-startup --python tools/run_tests.py -- -k toggle   # Only tests matching "toggle".
blender -b --factory-startup --python tests/test_registration.py         # A single test file.
```

The runner loads the extension straight from this source tree (as a temporary local extension repository),
starts every test from an empty file, writes a JUnit report to `build/test-results/junit.xml`
and exits with a non-zero code when a test fails.

Python code follows Blender's formatting rules (`autopep8` with the settings in [`pyproject.toml`](pyproject.toml)).

## Español

Herramientas de flujo de trabajo y pipeline para Blender. La mayoría se instala como extensión normal; lo que necesita cambios en el núcleo vive en una rama aparte de Blender, y las mejoras de interés general se proponen a Blender oficial.

## License

GPL-2.0-or-later, same as Blender. See [`LICENSE`](LICENSE).
