# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Tests of the MCP server of the Command Server (``workbench_plus/command_server/mcp_server.py``).
"""

import importlib
import json
import os
import subprocess
import sys
import threading
import time
import unittest

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
from test_command_server import CommandServerTestCase

SOURCE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_SERVER_FILEPATH = os.path.join(SOURCE_DIR, "workbench_plus", "command_server", "mcp_server.py")

TOOL_NAMES = {
    "run_python",
    "evaluate",
    "call_operator",
    "get_property",
    "set_property",
    "batch",
    "scene_summary",
}


def _mcp_module():
    harness.addon_enable()
    return importlib.import_module(harness.ADDON_MODULE + ".command_server.mcp_server")


def _request(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


class ProtocolTest(unittest.TestCase):
    """MCP messages that do not need Blender's Command Server."""

    def setUp(self):
        self.mcp = _mcp_module()
        self.bridge = self.mcp.Bridge(None)

    def tearDown(self):
        self.bridge.close()
        harness.addon_disable()

    def test_initialize(self):
        response = self.bridge.handle(_request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}))
        result = response["result"]
        self.assertEqual(response["id"], 1)
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "blender-workbench-plus")

    def test_initialize_unknown_version(self):
        result = self.bridge.handle(_request("initialize", {"protocolVersion": "1999-01-01"}))["result"]
        self.assertEqual(result["protocolVersion"], self.mcp.PROTOCOL_VERSIONS[0])

    def test_tools_list(self):
        tools = self.bridge.handle(_request("tools/list"))["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, TOOL_NAMES)
        for tool in tools:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["description"])
                self.assertEqual(tool["inputSchema"]["type"], "object")
                for name in tool["inputSchema"].get("required", ()):
                    self.assertIn(name, tool["inputSchema"]["properties"])
        # The whole list is valid JSON.
        json.dumps(tools)

    def test_ping(self):
        self.assertEqual(self.bridge.handle(_request("ping", request_id="a")),
                         {"jsonrpc": "2.0", "id": "a", "result": {}})

    def test_notifications_get_no_response(self):
        self.assertIsNone(self.bridge.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertIsNone(self.bridge.handle({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                              "params": {"requestId": 3}}))
        # A tool call sent as a notification runs nowhere and answers nothing.
        self.assertIsNone(self.bridge.handle({"jsonrpc": "2.0", "method": "ping"}))

    def test_errors(self):
        self.assertEqual(self.bridge.handle(_request("resources/list"))["error"]["code"], self.mcp.METHOD_NOT_FOUND)
        self.assertEqual(self.bridge.handle({"id": 4, "method": "ping"})["error"]["code"], self.mcp.INVALID_REQUEST)
        self.assertEqual(self.bridge.handle(_request("tools/call", {"name": "nope"}))["error"]["code"],
                         self.mcp.INVALID_PARAMS)
        self.assertEqual(self.bridge.handle(_request("tools/call", {}))["error"]["code"], self.mcp.INVALID_PARAMS)
        self.assertEqual(
            self.bridge.handle(_request("tools/call", {"name": "evaluate", "arguments": [1]}))["error"]["code"],
            self.mcp.INVALID_PARAMS)
        self.assertEqual(self.bridge.handle(_request("ping", [1]))["error"]["code"], self.mcp.INVALID_PARAMS)
        # Responses (to requests this server never sends) are ignored.
        self.assertIsNone(self.bridge.handle({"jsonrpc": "2.0", "id": 9, "result": {}}))

    def test_lines(self):
        response = json.loads(self.bridge.handle_line("{not json"))
        self.assertEqual(response["error"]["code"], self.mcp.PARSE_ERROR)
        self.assertIsNone(response["id"])
        responses = json.loads(self.bridge.handle_line(json.dumps([
            _request("ping", request_id=1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request("ping", request_id=2),
        ])))
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertIsNone(self.bridge.handle_line(json.dumps({"jsonrpc": "2.0", "method": "notifications/x"})))
        # Responses are single lines (the stdio transport is line based).
        self.assertNotIn("\n", self.bridge.handle_line(json.dumps(_request("tools/list"))))

    def test_without_discovery_file(self):
        result = self.bridge.tool_call("scene_summary", {})
        self.assertTrue(result["isError"])
        self.assertIn("--discovery", result["content"][0]["text"])

    def test_server_not_running(self):
        bridge = self.mcp.Bridge(os.path.join(SOURCE_DIR, "build", "missing", "command_server.json"))
        result = bridge.tool_call("evaluate", {"expression": "1"})
        self.assertTrue(result["isError"])
        self.assertIn("not running", result["content"][0]["text"])


class ConfigTest(unittest.TestCase):

    def setUp(self):
        harness.addon_enable()
        self.cs = sys.modules[harness.ADDON_MODULE + ".command_server"]

    def tearDown(self):
        harness.addon_disable()

    def test_json(self):
        config = json.loads(self.cs.mcp_config('JSON'))["mcpServers"]["blender"]
        self.assertEqual(config["command"], sys.executable)
        script, flag, discovery = config["args"]
        self.assertTrue(os.path.samefile(script, MCP_SERVER_FILEPATH))
        self.assertEqual(flag, "--discovery")
        self.assertEqual(discovery, self.cs.discovery_filepath_default())

    def test_command(self):
        command = self.cs.mcp_config('COMMAND')
        self.assertIn("--discovery", command)
        self.assertIn(os.path.basename(sys.executable), command)
        with self.assertRaises(ValueError):
            self.cs.mcp_config('NOPE')

    def test_operator(self):
        for kind in ('COMMAND', 'JSON'):
            with self.subTest(kind=kind):
                self.assertEqual(bpy.ops.wbp.command_server_mcp_copy(kind=kind), {'FINISHED'})


class ToolsTest(CommandServerTestCase):
    """Tool calls forwarded to a running Command Server."""

    def setUp(self):
        super().setUp()
        self.mcp = importlib.import_module(harness.ADDON_MODULE + ".command_server.mcp_server")
        self.bridge = self.mcp.Bridge(self.discovery_filepath, timeout=30.0)

    def tearDown(self):
        self.bridge.close()
        super().tearDown()

    def tool(self, name, **arguments):
        """Call a tool through the MCP message handler, return ``(text, is_error)``."""
        response = self.run_client(lambda: self.bridge.handle(_request("tools/call", {
            "name": name, "arguments": arguments})))
        result = response["result"]
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        return result["content"][0]["text"], result["isError"]

    def assertTool(self, name, **arguments):
        text, is_error = self.tool(name, **arguments)
        self.assertFalse(is_error, msg=text)
        return text

    def test_run_python_and_evaluate(self):
        self.assertEqual(self.assertTool("run_python", code="value = 21\nprint('hi')"), "hi\n")
        self.assertEqual(self.assertTool("run_python", code="value *= 2"), "(no output)")
        self.assertEqual(json.loads(self.assertTool("evaluate", expression="value")), 42)
        self.assertEqual(json.loads(self.assertTool("evaluate", expression="bpy.app.version_string")),
                         bpy.app.version_string)

    def test_python_error_has_traceback(self):
        text, is_error = self.tool("run_python", code="def f():\n    return 1 / 0\nf()")
        self.assertTrue(is_error)
        self.assertIn("ZeroDivisionError", text)
        self.assertIn("Traceback", text)
        self.assertIn("line 2", text)

    def test_missing_argument(self):
        text, is_error = self.tool("evaluate")
        self.assertTrue(is_error)
        self.assertIn("expression", text)
        text, is_error = self.tool("set_property", path="scenes[0].frame_current")
        self.assertTrue(is_error)
        self.assertIn("value", text)

    def test_call_operator_and_properties(self):
        text = self.assertTool("call_operator", operator="object.empty_add", properties={"location": [1, 2, 3]})
        self.assertEqual(json.loads(text), {"result": ["FINISHED"]})
        name = bpy.context.view_layer.objects.active.name
        path = 'objects["{:s}"].location'.format(name)
        self.assertEqual(json.loads(self.assertTool("get_property", path=path)), [1.0, 2.0, 3.0])
        self.assertEqual(self.assertTool("set_property", path=path, value=[0, 0, 5]), "OK")
        self.assertEqual(tuple(bpy.data.objects[name].location), (0.0, 0.0, 5.0))
        self.assertEqual(json.loads(self.assertTool("get_property", path="scene.name", root="context")),
                         bpy.context.scene.name)

        text, is_error = self.tool("call_operator", operator="object.nope")
        self.assertTrue(is_error)
        self.assertIn("unknown operator", text)

    def test_batch(self):
        text = self.assertTool("batch", undo="Two Empties", calls=[
            {"method": "call_operator", "params": {"operator": "object.empty_add"}},
            {"method": "eval", "params": {"expression": "len(bpy.data.objects)"}},
        ])
        self.assertEqual(json.loads(text), [{"result": ["FINISHED"]}, 1])

        text, is_error = self.tool("batch", calls=[
            {"method": "eval", "params": {"expression": "1"}},
            {"method": "eval", "params": {"expression": "1 / 0"}},
        ])
        self.assertTrue(is_error)
        self.assertIn("call 1", text)
        self.assertIn("ZeroDivisionError", text)

    def test_scene_summary(self):
        bpy.ops.object.empty_add(location=(1, 0, 0))
        empty = bpy.context.view_layer.objects.active
        empty.name = "Pivot"
        child = bpy.data.objects.new("Child", None)
        bpy.context.scene.collection.objects.link(child)
        child.parent = empty
        summary = json.loads(self.assertTool("scene_summary"))
        self.assertEqual(summary["scene"], bpy.context.scene.name)
        self.assertEqual(summary["active_object"], "Pivot")
        self.assertEqual(summary["object_count"], 2)
        objects = {ob["name"]: ob for ob in summary["objects"]}
        self.assertEqual(objects["Pivot"]["location"], [1.0, 0.0, 0.0])
        self.assertTrue(objects["Pivot"]["selected"])
        self.assertEqual(objects["Child"]["parent"], "Pivot")
        self.assertEqual(objects["Child"]["type"], "EMPTY")
        # The helper does not stay in the namespace shared with `run_python`.
        self.assertNotIn("_wbp_scene_summary", self.cs._state.dispatcher.namespace)

    def test_reconnects_after_restart(self):
        self.assertTool("evaluate", expression="1")
        self.cs.server_stop()
        text, is_error = self.tool("evaluate", expression="1")
        self.assertTrue(is_error)
        self.assertIn("not running", text)
        # A new server (new port and token) is picked up from the discovery file.
        self.cs.server_start(discovery_filepath=self.discovery_filepath)
        self.assertEqual(json.loads(self.assertTool("evaluate", expression="2")), 2)

    def test_stdio_subprocess(self):
        """The script runs on its own (outside Blender and the add-on package), with Blender's Python."""
        command = [sys.executable, "-I", MCP_SERVER_FILEPATH, "--discovery", self.discovery_filepath]
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        messages = [
            _request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "test", "version": "0"}}, request_id=1),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _request("tools/list", request_id=2),
            _request("tools/call", {"name": "evaluate", "arguments": {"expression": "'ñ' * 3"}}, request_id=3),
        ]

        def function():
            for message in messages:
                proc.stdin.write(json.dumps(message).encode("utf-8") + b"\n")
            proc.stdin.flush()
            lines = [proc.stdout.readline() for _ in range(3)]
            proc.stdin.close()
            return lines, proc.wait(timeout=30.0)

        try:
            lines, returncode = self.run_client(function)
        finally:
            if proc.poll() is None:
                proc.kill()
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            proc.stdout.close()
            proc.stderr.close()
        self.assertEqual(returncode, 0, msg=stderr)
        responses = [json.loads(line) for line in lines]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual({tool["name"] for tool in responses[1]["result"]["tools"]}, TOOL_NAMES)
        self.assertEqual(responses[2]["result"]["content"][0]["text"], '"ñññ"')


if __name__ == "__main__":
    harness.main()
