# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
blender -b --factory-startup --python tests/test_command_server.py
"""

import hashlib
import importlib.util
import json
import os
import secrets
import socket
import statistics
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import helpers

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_FILEPATH = os.path.join(SOURCE_DIR, "workbench_plus", "command_server", "client.py")


def _client_module():
    # Loaded from its file, like a user would after copying it: it must not need the add-on package.
    spec = importlib.util.spec_from_file_location("wbp_command_server_client", CLIENT_FILEPATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


client_module = _client_module()
Client = client_module.Client
ServerError = client_module.ServerError

# Error codes, from the protocol documentation.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
PYTHON_EXCEPTION = -32000
UNAUTHORIZED = -32001
OPERATOR_FAILED = -32002
BATCH_FAILED = -32003


def _raw_send(sock, data):
    sock.sendall(struct.pack(">I", len(data)) + data)


def _raw_recv(sock):
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            return None
        header += chunk
    (size,) = struct.unpack(">I", header)
    data = b""
    while len(data) < size:
        data += sock.recv(size - len(data))
    return json.loads(data)


class CommandServerTestCase(unittest.TestCase):
    """Starts the server with a temporary discovery file; clients run in threads while the main thread pumps."""

    def setUp(self):
        harness.addon_enable()
        self.cs = sys.modules[harness.ADDON_MODULE + ".command_server"]
        self.tempdir = tempfile.TemporaryDirectory()
        self.discovery_filepath = os.path.join(self.tempdir.name, "command_server.json")
        self.port = self.cs.server_start(discovery_filepath=self.discovery_filepath)
        with open(self.discovery_filepath, encoding="utf-8") as fh:
            self.token = json.load(fh)["token"]

    def tearDown(self):
        self.cs.server_stop()
        harness.addon_disable()
        self.tempdir.cleanup()

    def connect(self, **kwargs):
        return Client.from_discovery_file(self.discovery_filepath, **kwargs)

    def run_client(self, function, timeout=60.0):
        """Run ``function`` in a thread while the server executes requests here, return its result."""
        outcome = {}

        def target():
            try:
                outcome["value"] = function()
            except BaseException as ex:
                outcome["error"] = ex

        thread = threading.Thread(target=target)
        thread.start()
        time_end = time.monotonic() + timeout
        while thread.is_alive():
            if not self.cs.is_running():
                time.sleep(0.001)
            self.cs.server_pump(0.01)
            if time.monotonic() > time_end:
                self.fail("Client did not finish in {:g} seconds".format(timeout))
        thread.join()
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("value")

    def call(self, method, **params):
        """Make one call on a new connection."""
        def function():
            with self.connect() as client:
                return client.call(method, **params)
        return self.run_client(function)

    def assertServerError(self, error_code, method, /, **params):
        with self.assertRaises(ServerError) as cm:
            self.call(method, **params)
        self.assertEqual(cm.exception.code, error_code, msg=str(cm.exception))
        return cm.exception


class LifecycleTest(CommandServerTestCase):

    def test_discovery_file(self):
        with open(self.discovery_filepath, encoding="utf-8") as fh:
            info = json.load(fh)
        self.assertEqual(info["host"], "127.0.0.1")
        self.assertEqual(info["port"], self.port)
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["protocol"], 1)
        self.assertGreaterEqual(len(info["token"]), 32)
        if sys.platform != "win32":
            self.assertEqual(os.stat(self.discovery_filepath).st_mode & 0o777, 0o600)

    def test_stop_removes_discovery_and_closes_port(self):
        self.cs.server_stop()
        self.assertFalse(os.path.exists(self.discovery_filepath))
        self.assertFalse(self.cs.is_running())
        with self.assertRaises(OSError):
            socket.create_connection(("127.0.0.1", self.port), timeout=1.0).close()

    def test_start_twice_fails(self):
        with self.assertRaises(RuntimeError):
            self.cs.server_start(discovery_filepath=self.discovery_filepath)

    def test_new_token_every_start(self):
        self.cs.server_stop()
        self.cs.server_start(discovery_filepath=self.discovery_filepath)
        with open(self.discovery_filepath, encoding="utf-8") as fh:
            self.assertNotEqual(json.load(fh)["token"], self.token)

    def test_disable_addon_stops_server(self):
        harness.addon_disable()
        self.assertFalse(os.path.exists(self.discovery_filepath))
        harness.addon_enable()
        self.cs = sys.modules[harness.ADDON_MODULE + ".command_server"]
        self.assertFalse(self.cs.is_running())

    def test_operators_registered(self):
        for idname in ("command_server_start", "command_server_stop", "command_server_serve"):
            with self.subTest(operator=idname):
                self.assertTrue(hasattr(bpy.types, "WBP_OT_" + idname))
        # Only runs in background mode, which is the case here.
        self.assertTrue(bpy.ops.wbp.command_server_serve.poll())
        self.assertFalse(bpy.ops.wbp.command_server_start.poll())
        self.assertTrue(bpy.ops.wbp.command_server_stop.poll())

    def test_shutdown(self):
        self.assertIsNone(self.call("shutdown"))
        self.assertFalse(self.cs.is_running())
        self.assertFalse(os.path.exists(self.discovery_filepath))

    def test_client_disconnect(self):
        def function():
            client = self.connect()
            client.call("ping")
            client.close()
            # The server notices the closed connection.
            time_end = time.monotonic() + 5.0
            while self.cs._state.server.connections and time.monotonic() < time_end:
                time.sleep(0.01)
            return len(self.cs._state.server.connections)
        self.assertEqual(self.run_client(function), 0)


class AuthenticationTest(CommandServerTestCase):

    def test_invalid_token_rejected(self):
        def function():
            with self.assertRaises(ServerError) as cm:
                Client("127.0.0.1", self.port, "not-the-token")
            return cm.exception.code
        self.assertEqual(self.run_client(function), UNAUTHORIZED)

    def test_request_before_auth_rejected_and_closed(self):
        def function():
            with socket.create_connection(("127.0.0.1", self.port), timeout=5.0) as sock:
                _raw_send(sock, b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}')
                response = _raw_recv(sock)
                return response, _raw_recv(sock)
        response, after = self.run_client(function)
        self.assertEqual(response["error"]["code"], UNAUTHORIZED)
        self.assertIsNone(after, "the connection must be closed")

    def test_non_string_token_rejected(self):
        def function():
            with self.assertRaises(ServerError) as cm:
                Client("127.0.0.1", self.port, None)
            return cm.exception.code
        self.assertEqual(self.run_client(function), UNAUTHORIZED)


class MethodsTest(CommandServerTestCase, helpers.TransformAssertions):

    def test_ping(self):
        self.assertEqual(self.call("ping"), "pong")

    def test_version(self):
        version = self.call("version")
        self.assertEqual(version["protocol"], 1)
        self.assertEqual(version["blender"], bpy.app.version_string)
        self.assertRegex(version["server"], r"^\d+\.\d+\.\d+")

    def test_exec_stdout_and_namespace(self):
        def function():
            with self.connect() as client:
                first = client.call("exec", code="value = 21\nprint('hello')")
                second = client.call("eval", expression="value * 2")
                return first, second
        first, second = self.run_client(function)
        self.assertEqual(first, {"stdout": "hello\n"})
        self.assertEqual(second, 42)

    def test_exec_changes_scene(self):
        self.call("exec", code="bpy.data.objects.new('Remote', None)")
        self.assertIn("Remote", bpy.data.objects)

    def test_eval_values(self):
        helpers.objects_new("Empty")
        self.assertEqual(self.call("eval", expression="(1, 2.5, 'a', None, True)"), [1, 2.5, "a", None, True])
        self.assertEqual(self.call("eval", expression="{3, 1, 2}"), [1, 2, 3])
        self.assertEqual(self.call("eval", expression="float('nan')"), None)
        self.assertEqual(self.call("eval", expression="__import__('mathutils').Vector((1, 2, 3))"), [1.0, 2.0, 3.0])
        self.assertMatrixAlmostEqual(
            self.call("eval", expression="__import__('mathutils').Matrix.Identity(3)"),
            [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        )
        self.assertEqual(
            self.call("eval", expression="bpy.data.objects['Empty']"),
            {"$type": "ID", "id_type": "OBJECT", "name": "Empty"},
        )
        struct_value = self.call("eval", expression="bpy.context.preferences.view")
        self.assertEqual(struct_value["$type"], "struct")
        self.assertEqual(struct_value["rna_type"], "PreferencesView")
        self.assertEqual(self.call("eval", expression="object()")["$type"], "repr")

    def test_call_operator(self):
        a, b = helpers.objects_new("A", "B")
        helpers.select_objects(a, b)
        result = self.call("call_operator", operator="object.select_all", properties={"action": "DESELECT"})
        self.assertEqual(result, {"result": ["FINISHED"]})
        self.assertEqual(bpy.context.selected_objects, [])

    def test_call_operator_unknown(self):
        self.assertServerError(INVALID_PARAMS, "call_operator", operator="object.does_not_exist")
        self.assertServerError(INVALID_PARAMS, "call_operator", operator="nomodule")

    def test_call_operator_bad_property(self):
        self.assertServerError(INVALID_PARAMS, "call_operator", operator="object.select_all",
                               properties={"action": "NOT_AN_ACTION"})

    def test_call_operator_poll_fails(self):
        # No active object: changing the mode is not possible.
        error = self.assertServerError(OPERATOR_FAILED, "call_operator", operator="object.mode_set",
                                       properties={"mode": "EDIT"})
        self.assertIn("poll", error.message)

    def test_get_property(self):
        (ob,) = helpers.objects_new("Cube")
        ob.location = (1.0, 2.0, 3.0)
        self.assertEqual(self.call("get_property", path='objects["Cube"].location'), [1.0, 2.0, 3.0])
        self.assertEqual(self.call("get_property", path='objects["Cube"].location[1]'), 2.0)
        self.assertEqual(self.call("get_property", path="scene.frame_current", root="context"),
                         bpy.context.scene.frame_current)

    def test_set_property(self):
        (ob,) = helpers.objects_new("Cube")
        self.assertIsNone(self.call("set_property", path='objects["Cube"].location', value=[4, 5, 6]))
        self.assertVectorAlmostEqual(ob.location, (4.0, 5.0, 6.0))
        self.call("set_property", path='objects["Cube"].location[2]', value=-1.5)
        self.assertVectorAlmostEqual(ob.location, (4.0, 5.0, -1.5))
        self.call("set_property", path='objects["Cube"]["custom"]', value="text")
        self.assertEqual(ob["custom"], "text")
        self.call("set_property", path="scene.frame_current", value=7, root="context")
        self.assertEqual(bpy.context.scene.frame_current, 7)

    def test_set_property_names_with_dots_and_brackets(self):
        (ob,) = helpers.objects_new("A.001 [x]")
        self.call("set_property", path='objects["A.001 [x]"].location', value=[1, 1, 1])
        self.assertVectorAlmostEqual(ob.location, (1.0, 1.0, 1.0))

    def test_property_errors(self):
        helpers.objects_new("Cube")
        self.assertServerError(INVALID_PARAMS, "get_property", path='objects["Missing"].location')
        self.assertServerError(INVALID_PARAMS, "set_property", path='objects["Cube"].not_a_property', value=1)
        self.assertServerError(INVALID_PARAMS, "set_property", path='objects["Cube"].location')
        self.assertServerError(INVALID_PARAMS, "get_property", path="scene", root="nowhere")
        error = self.assertServerError(PYTHON_EXCEPTION, "set_property", path='objects["Cube"].location',
                                       value="not a vector")
        self.assertEqual(error.data["type"], "ValueError")

    def test_method_not_found(self):
        self.assertServerError(METHOD_NOT_FOUND, "no_such_method")

    def test_missing_params(self):
        self.assertServerError(INVALID_PARAMS, "exec")
        self.assertServerError(INVALID_PARAMS, "eval", expression=42)

    def test_notification_runs_without_response(self):
        def function():
            with self.connect() as client:
                client.notify("exec", code="from_notification = True")
                return client.call("eval", expression="from_notification")
        self.assertIs(self.run_client(function), True)

    def test_invalid_messages(self):
        def function():
            with socket.create_connection(("127.0.0.1", self.port), timeout=5.0) as sock:
                _raw_send(sock, json.dumps({"jsonrpc": "2.0", "id": 0, "method": "auth",
                                            "params": {"token": self.token}}).encode())
                responses = [_raw_recv(sock)]
                for data in (b"{not json", b'[1, 2]', b'{"jsonrpc": "2.0", "id": 1, "method": 5}',
                             b'{"jsonrpc": "2.0", "id": 2, "method": "ping", "params": [1]}',
                             b"\xff\xfe"):
                    _raw_send(sock, data)
                    responses.append(_raw_recv(sock))
                # The connection still works.
                _raw_send(sock, b'{"jsonrpc": "2.0", "id": 3, "method": "ping"}')
                responses.append(_raw_recv(sock))
                return responses
        responses = self.run_client(function)
        self.assertIs(responses[0]["result"], True)
        self.assertEqual([response["error"]["code"] for response in responses[1:-1]],
                         [PARSE_ERROR, INVALID_REQUEST, INVALID_REQUEST, INVALID_PARAMS, PARSE_ERROR])
        self.assertEqual(responses[-1]["result"], "pong")


class ErrorsTest(CommandServerTestCase):

    def test_exec_exception_traceback(self):
        error = self.assertServerError(PYTHON_EXCEPTION, "exec", code="x = 1\ny = x / 0\n")
        self.assertEqual(error.message, "ZeroDivisionError: division by zero")
        self.assertEqual(error.data["type"], "ZeroDivisionError")
        self.assertIn('File "<command_server>", line 2', error.traceback)
        self.assertIn("ZeroDivisionError", error.traceback)

    def test_syntax_error(self):
        error = self.assertServerError(PYTHON_EXCEPTION, "exec", code="def (")
        self.assertEqual(error.data["type"], "SyntaxError")

    def test_eval_exception(self):
        error = self.assertServerError(PYTHON_EXCEPTION, "eval", expression="undefined_name")
        self.assertEqual(error.data["type"], "NameError")

    def test_server_survives_errors(self):
        def function():
            with self.connect() as client:
                with self.assertRaises(ServerError):
                    client.call("exec", code="raise ValueError('boom')")
                return client.call("ping")
        self.assertEqual(self.run_client(function), "pong")


class BatchTest(CommandServerTestCase, helpers.TransformAssertions):

    def test_batch_results(self):
        a, b = helpers.objects_new("A", "B")
        helpers.select_objects(a, b)
        results = self.call("batch", calls=[
            {"method": "set_property", "params": {"path": 'objects["A"].location', "value": [1, 0, 0]}},
            {"method": "call_operator", "params": {"operator": "object.select_all",
                                                   "properties": {"action": "DESELECT"}}},
            {"method": "eval", "params": {"expression": "len(bpy.context.selected_objects)"}},
        ])
        self.assertEqual(results, [None, {"result": ["FINISHED"]}, 0])
        self.assertVectorAlmostEqual(a.location, (1.0, 0.0, 0.0))

    def test_batch_single_undo_step(self):
        (ob,) = helpers.objects_new("A")
        bpy.ops.ed.undo_push(message="Before batch")
        self.call("batch", undo="Move Twice", calls=[
            {"method": "set_property", "params": {"path": 'objects["A"].location', "value": [1, 0, 0]}},
            {"method": "set_property", "params": {"path": 'objects["A"].location[1]', "value": 2}},
            {"method": "set_property", "params": {"path": 'objects["A"].scale', "value": [3, 3, 3]}},
        ])
        self.assertVectorAlmostEqual(bpy.data.objects["A"].location, (1.0, 2.0, 0.0))
        # A single undo restores the state from before the whole batch.
        bpy.ops.ed.undo()
        ob = bpy.data.objects["A"]
        self.assertVectorAlmostEqual(ob.location, (0.0, 0.0, 0.0))
        self.assertVectorAlmostEqual(ob.scale, (1.0, 1.0, 1.0))

    def test_batch_failure(self):
        helpers.objects_new("A")
        error = self.assertServerError(BATCH_FAILED, "batch", calls=[
            {"method": "set_property", "params": {"path": 'objects["A"].location', "value": [1, 0, 0]}},
            {"method": "exec", "params": {"code": "1 / 0"}},
            {"method": "set_property", "params": {"path": 'objects["A"].scale', "value": [3, 3, 3]}},
        ])
        self.assertEqual(error.data["index"], 1)
        self.assertEqual(error.data["error"]["code"], PYTHON_EXCEPTION)
        self.assertEqual(error.data["results"], [None])
        # The calls after the failing one did not run.
        self.assertEqual(tuple(bpy.data.objects["A"].scale), (1.0, 1.0, 1.0))

    def test_batch_invalid_calls(self):
        self.assertServerError(INVALID_PARAMS, "batch", calls="not a list")
        error = self.assertServerError(BATCH_FAILED, "batch", calls=[{"method": "shutdown"}])
        self.assertEqual(error.data["index"], 0)
        self.assertServerError(BATCH_FAILED, "batch", calls=[42])
        self.assertTrue(self.cs.is_running())

    def test_batch_empty(self):
        self.assertEqual(self.call("batch", calls=[]), [])


class EventsTest(CommandServerTestCase):

    def test_subscribe(self):
        self.assertEqual(self.call("subscribe", events=["frame_change", "load_post"]), ["frame_change", "load_post"])
        self.assertServerError(INVALID_PARAMS, "subscribe", events=["not_an_event"])

    def test_frame_change_event(self):
        def function():
            with self.connect() as client:
                client.call("subscribe", events=["frame_change"])
                client.call("exec", code="bpy.context.scene.frame_set(12)")
                return client.events()
        self.assertEqual(self.run_client(function), [{"event": "frame_change", "frame": 12}])

    def test_only_subscribed_events(self):
        def function():
            with self.connect() as client:
                client.call("subscribe", events=["load_post"])
                client.call("exec", code="bpy.context.scene.frame_set(3)")
                after_frame = client.events()
                client.call("unsubscribe", events=["load_post"])
                return after_frame
        self.assertEqual(self.run_client(function), [])

    def test_depsgraph_update_event(self):
        helpers.objects_new("Moving")
        bpy.context.view_layer.update()

        def function():
            with self.connect() as client:
                client.call("subscribe", events=["depsgraph_update"])
                client.call("exec", code=(
                    "bpy.data.objects['Moving'].location.x = 5.0\n"
                    "bpy.context.view_layer.update()\n"
                ))
                return client.events()
        events = self.run_client(function)
        self.assertTrue(events)
        ids = [item for event in events for item in event["ids"]]
        self.assertIn({"id_type": "OBJECT", "name": "Moving"}, ids)

    def test_progress_event(self):
        def function():
            with self.connect() as client:
                client.call("subscribe", events=["progress"])
                client.call("exec", code="for i in range(3):\n    notify_progress((i + 1) / 3, 'step')\n")
                return client.events()
        events = self.run_client(function)
        self.assertEqual([round(event["value"], 3) for event in events], [0.333, 0.667, 1.0])
        self.assertTrue(all(event["message"] == "step" for event in events))

    def test_load_post_event(self):
        def function():
            with self.connect() as client:
                client.call("subscribe", events=["load_post"])
                client.call("call_operator", operator="wm.read_homefile",
                            properties={"use_factory_startup": True, "use_empty": True})
                return client.events(), client.call("ping")
        events, pong = self.run_client(function)
        self.assertEqual([event["event"] for event in events], ["load_post"])
        self.assertEqual(pong, "pong")

    def test_events_on_other_connection(self):
        # Events reach a listening client while another client makes the changes.
        def function():
            with self.connect() as listener, self.connect() as actor:
                listener.call("subscribe", events=["frame_change"])
                actor.call("exec", code="bpy.context.scene.frame_set(20)")
                return listener.events(wait=5.0)
        self.assertEqual(self.run_client(function), [{"event": "frame_change", "frame": 20}])


class PerformanceTest(CommandServerTestCase):

    def test_many_requests_latency(self):
        def function():
            latencies = []
            with self.connect() as client:
                for _ in range(1000):
                    time_start = time.perf_counter()
                    client.call("ping")
                    latencies.append(time.perf_counter() - time_start)
            return latencies
        latencies = self.run_client(function)
        self.assertEqual(len(latencies), 1000)
        p50 = statistics.median(latencies)
        print("Command Server ping latency: p50 {:.3f} ms, max {:.3f} ms".format(p50 * 1000.0, max(latencies) * 1000.0))
        self.assertLess(p50, 0.001)

    def test_large_payload_round_trip(self):
        payload = secrets.token_hex(5 * 1024 * 1024)  # 10 MiB of text.

        def function():
            with self.connect() as client:
                client.call("exec", code="import hashlib\npayload = None")
                client.call("set_property", path='scenes["Scene"]["payload"]', value=payload)
                digest = client.call("eval", expression=(
                    "hashlib.sha256(bpy.data.scenes['Scene']['payload'].encode()).hexdigest()"))
                echoed = client.call("get_property", path='scenes["Scene"]["payload"]')
                return digest, echoed
        digest, echoed = self.run_client(function)
        self.assertEqual(digest, hashlib.sha256(payload.encode()).hexdigest())
        self.assertEqual(echoed, payload)


class BackgroundServeTest(unittest.TestCase):
    """``wbp.command_server_serve`` keeps a background Blender serving until ``shutdown``."""

    def test_serve_until_shutdown(self):
        with tempfile.TemporaryDirectory() as tempdir:
            discovery_filepath = os.path.join(tempdir, "command_server.json")
            script = textwrap.dedent("""\
                import sys
                sys.path.insert(0, {tests_dir!r})
                import harness
                harness.addon_enable()
                cs = sys.modules[harness.ADDON_MODULE + ".command_server"]
                cs.server_start(discovery_filepath={discovery_filepath!r})
                import bpy
                bpy.ops.wbp.command_server_serve(timeout=60.0)
                harness.addon_disable()
                harness.extension_repo_remove()
                print("SERVE_RETURNED")
            """).format(tests_dir=os.path.dirname(os.path.abspath(__file__)), discovery_filepath=discovery_filepath)
            script_filepath = os.path.join(tempdir, "serve.py")
            with open(script_filepath, "w", encoding="utf-8") as fh:
                fh.write(script)

            env = dict(os.environ, BLENDER_USER_RESOURCES=os.path.join(tempdir, "user"))
            proc = subprocess.Popen(
                [
                    bpy.app.binary_path, "-b", "--factory-startup",
                    "--python-exit-code", "1", "--python", script_filepath,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
            )
            try:
                time_end = time.monotonic() + 60.0
                while not os.path.exists(discovery_filepath):
                    self.assertIsNone(proc.poll(), msg="Blender exited early")
                    self.assertLess(time.monotonic(), time_end, msg="Server did not start")
                    time.sleep(0.05)
                # The file is created before the server accepts connections; retry briefly.
                for _ in range(100):
                    try:
                        client = Client.from_discovery_file(discovery_filepath, timeout=30.0)
                        break
                    except (OSError, ValueError, KeyError):
                        time.sleep(0.05)
                else:
                    self.fail("Could not connect to the background server")
                with client:
                    self.assertEqual(client.call("ping"), "pong")
                    self.assertEqual(client.call("eval", expression="bpy.app.background"), True)
                    client.call("shutdown")
                output, _ = proc.communicate(timeout=60.0)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.communicate()
            self.assertEqual(proc.returncode, 0, msg=output)
            self.assertIn("SERVE_RETURNED", output)


if __name__ == "__main__":
    harness.main()
