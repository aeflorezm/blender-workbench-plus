# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Reference client for the Workbench Plus Command Server.

A single file without dependencies (Python 3.10+): copy it next to your tool. Example::

   from client import Client

   with Client.from_discovery_file(path) as blender:
       blender.call("exec", code="bpy.ops.mesh.primitive_cube_add()")
       print(blender.call("get_property", path='objects["Cube"].location'))

See ``docs/command_server.md`` for the protocol.
"""

__all__ = (
    "Client",
    "ServerError",
)

import collections
import itertools
import json
import socket
import struct
import threading

_HEADER = struct.Struct(">I")
MESSAGE_SIZE_MAX = 256 * 1024 * 1024


class ServerError(Exception):
    """An error response from the server."""

    def __init__(self, code, message, data=None):
        super().__init__("[{:d}] {:s}".format(code, message))
        self.code = code
        self.message = message
        self.data = data

    @property
    def traceback(self):
        """The Python traceback of the server side exception, if any."""
        return (self.data or {}).get("traceback") if isinstance(self.data, dict) else None


class Client:
    """
    A connection to the Command Server. Not thread safe: use one client per thread.

    Notifications (events) received while waiting for a response are queued, see :meth:`events`.
    """

    def __init__(self, host, port, token, *, timeout=None):
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._ids = itertools.count(1)
        self._events = collections.deque()
        self._lock = threading.Lock()
        self.call("auth", token=token)

    @classmethod
    def from_discovery_file(cls, filepath, **kwargs):
        """Connect with the host, port and token from the server's ``command_server.json``."""
        with open(filepath, encoding="utf-8") as fh:
            info = json.load(fh)
        return cls(info["host"], info["port"], info["token"], **kwargs)

    def close(self):
        self._sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def _send(self, message):
        data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self._sock.sendall(_HEADER.pack(len(data)) + data)

    def _recv_exact(self, size):
        buf = bytearray(size)
        view = memoryview(buf)
        offset = 0
        while offset < size:
            count = self._sock.recv_into(view[offset:], size - offset)
            if count == 0:
                raise ConnectionError("Connection closed by the server")
            offset += count
        return buf

    def _recv(self):
        (size,) = _HEADER.unpack(self._recv_exact(_HEADER.size))
        if size > MESSAGE_SIZE_MAX:
            raise ConnectionError("Message of {:d} bytes exceeds the limit".format(size))
        return json.loads(self._recv_exact(size))

    def call(self, method, **params):
        """Call ``method`` and return its result, raising :class:`ServerError` for an error response."""
        with self._lock:
            request_id = next(self._ids)
            self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            while True:
                message = self._recv()
                if "id" not in message:
                    self._events.append(message.get("params", {}))
                    continue
                if message["id"] != request_id:
                    # A response to a request without id (parse errors): report it.
                    if "error" in message:
                        error = message["error"]
                        raise ServerError(error["code"], error["message"], error.get("data"))
                    continue
                if "error" in message:
                    error = message["error"]
                    raise ServerError(error["code"], error["message"], error.get("data"))
                return message.get("result")

    def notify(self, method, **params):
        """Send a request without waiting for (or getting) a response."""
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def events(self, *, wait=None):
        """
        Return the events received so far (and clear them).

        :arg wait: When there are none, wait up to this many seconds for one (``None``: do not wait).
        """
        with self._lock:
            if not self._events and wait:
                timeout = self._sock.gettimeout()
                self._sock.settimeout(wait)
                try:
                    message = self._recv()
                    if "id" not in message:
                        self._events.append(message.get("params", {}))
                except TimeoutError:
                    pass
                finally:
                    self._sock.settimeout(timeout)
            events = list(self._events)
            self._events.clear()
        return events
