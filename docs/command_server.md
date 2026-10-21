# Command Server protocol

The Command Server lets other programs (IDEs, pipeline tools, test runners, AI agents) drive a running
Blender: run Python, call operators, read and write properties, and receive events.

- **Transport:** TCP on `127.0.0.1` only. The port is chosen in the add-on preferences (`0` picks a free port).
- **Messages:** [JSON-RPC 2.0](https://www.jsonrpc.org/specification), UTF-8 JSON.
- **Framing:** every message is a 4 byte big-endian unsigned length followed by that many bytes of JSON.
  Messages larger than 256 MiB are rejected.
- **Protocol version:** `1`, returned by the `version` method. Additions keep the version; incompatible changes bump it.

A reference client without dependencies lives in
[`workbench_plus/command_server/client.py`](../workbench_plus/command_server/client.py):
it is a single file that can be copied anywhere (Python 3.10+).

## Starting the server

- **With the interface:** *Preferences → Add-ons → Workbench Plus → Command Server → Start*,
  or enable *Start Automatically*.
- **Headless:** `blender -b --python-expr "import bpy; bpy.ops.wbp.command_server_serve()"` serves requests
  until a client calls `shutdown`.

## Discovery and authentication

While the server runs it writes `command_server.json` to the add-on's user directory
(`bpy.utils.extension_path_user(...)`), readable only by the current user where the OS supports it:

```json
{"protocol": 1, "host": "127.0.0.1", "port": 50123, "token": "…", "pid": 4242, "blender": "5.2.3"}
```

The token is random for every start. The first request on a connection must be `auth`;
any other request before it gets an `unauthorized` error and the connection is closed.

## Requests and responses

```json
→ {"jsonrpc": "2.0", "id": 1, "method": "eval", "params": {"expression": "bpy.context.scene.frame_current"}}
← {"jsonrpc": "2.0", "id": 1, "result": 1}
```

Requests run one at a time on Blender's main thread, in the order they arrive.
A request without `id` is a notification: it runs but gets no response.

Only `call_operator` pushes an undo step (like running the operator from the interface).
To make other changes undoable, or to group several changes into one step, use `batch`.

**Latency:** in background mode a round trip takes about 0.1 ms. With the interface, requests are picked up
by a timer, so a round trip takes about as long as one iteration of Blender's event loop (around 5 ms when idle).
Use `batch` for many small changes.

## Methods

| Method | Params | Result |
|---|---|---|
| `auth` | `token` | `true` |
| `ping` | — | `"pong"` |
| `version` | — | `{"protocol", "server", "blender"}` |
| `exec` | `code` | `{"stdout"}`: Python run in a namespace kept between requests, with `bpy` imported |
| `eval` | `expression` | The value of the expression (see *Values*) |
| `call_operator` | `operator` (`"object.select_all"`), `properties` (object, optional), `execution_context` (default `"EXEC_DEFAULT"`) | `{"result": ["FINISHED"]}` |
| `get_property` | `path`, `root` (`"data"` default, or `"context"`) | The value at the RNA path, e.g. `objects["Cube"].location` |
| `set_property` | `path`, `value`, `root` | `null`. Paths may end with an index: `objects["Cube"].location[2]` |
| `batch` | `calls` (list of `{"method", "params"}`), `undo` (message, optional) | List of results. All calls make a single undo step; the first failing call stops the batch |
| `subscribe` | `events` (list) | The events now subscribed |
| `unsubscribe` | `events` (list) | The events still subscribed |
| `shutdown` | — | `null`, then the server stops |

### Values

Results are converted to JSON: numbers, strings, booleans and `null` as is; vectors, colors, Euler
rotations and quaternions as lists; matrices as lists of rows; sets as sorted lists;
data-blocks as `{"$type": "ID", "id_type": "OBJECT", "name": "Cube"}`;
other Blender structs as `{"$type": "struct", "rna_type": "…", "repr": "…"}`;
anything else as `{"$type": "repr", "repr": "…"}`. Non-finite floats become `null`.

## Errors

```json
← {"jsonrpc": "2.0", "id": 3, "error": {"code": -32000, "message": "ZeroDivisionError: division by zero",
                                         "data": {"type": "ZeroDivisionError", "traceback": "Traceback …"}}}
```

| Code | Name | When |
|---|---|---|
| -32700 | Parse error | The message is not valid JSON |
| -32600 | Invalid request | Not a JSON-RPC 2.0 request object |
| -32601 | Method not found | Unknown method |
| -32602 | Invalid params | Missing or wrong parameters, unknown operator, unresolvable path |
| -32603 | Internal error | A bug in the server |
| -32000 | Python exception | The code, expression or property access raised; `data` has `type` and `traceback` |
| -32001 | Unauthorized | Missing or wrong `auth` |
| -32002 | Operator failed | The operator could not run (`poll()` failed) or reported an error |
| -32003 | Batch failed | A call in a batch failed; `data` has the `index` of the call and its `error` |

## Events

After `subscribe`, the server sends notifications (no `id`) to that connection:

```json
← {"jsonrpc": "2.0", "method": "event", "params": {"event": "frame_change", "frame": 12}}
```

| Event | Params | When |
|---|---|---|
| `depsgraph_update` | `ids`: `[{"id_type", "name"}]` of the updated data-blocks | After the dependency graph evaluates changes |
| `frame_change` | `frame` | After the current frame changes |
| `load_post` | `filepath` | After a file is loaded |
| `save_post` | `filepath` | After the file is saved |
| `undo_post`, `redo_post` | — | After undo or redo |
| `progress` | `value` (0–1), `message` | Sent by Python run with `exec`, through `notify_progress(value, message)` |
