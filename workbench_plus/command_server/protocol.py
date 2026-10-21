# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Wire format of the Command Server: JSON-RPC 2.0 messages framed by a 4 byte big-endian length.

Only uses the standard library (no ``bpy``), see ``docs/command_server.md``.
"""

__all__ = (
    "MESSAGE_SIZE_MAX",
    "PROTOCOL_VERSION",
    "RpcError",
    "error_response",
    "message_encode",
    "message_recv",
    "result_response",
)

import json
import struct

PROTOCOL_VERSION = 1

MESSAGE_SIZE_MAX = 256 * 1024 * 1024

_HEADER = struct.Struct(">I")

# JSON-RPC 2.0 error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# Server defined error codes.
PYTHON_EXCEPTION = -32000
UNAUTHORIZED = -32001
OPERATOR_FAILED = -32002
BATCH_FAILED = -32003


class RpcError(Exception):
    """An error to send back to the client as a JSON-RPC error object."""

    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def as_dict(self):
        error = {"code": self.code, "message": self.message}
        if self.data is not None:
            error["data"] = self.data
        return error


class ConnectionClosed(Exception):
    """The peer closed the connection, or sent a message that cannot be framed."""


def message_encode(message):
    """Return ``message`` (a JSON serializable object) as framed bytes."""
    data = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > MESSAGE_SIZE_MAX:
        raise ValueError("Message of {:d} bytes exceeds the limit of {:d}".format(len(data), MESSAGE_SIZE_MAX))
    return _HEADER.pack(len(data)) + data


def _recv_exact(sock, size):
    buf = bytearray(size)
    view = memoryview(buf)
    offset = 0
    while offset < size:
        count = sock.recv_into(view[offset:], size - offset)
        if count == 0:
            raise ConnectionClosed("Connection closed by the peer")
        offset += count
    return buf


def message_recv(sock):
    """
    Read one framed message from ``sock`` and return its JSON bytes (decoding is left to the caller,
    so invalid JSON can be answered with a parse error).

    Raises :class:`ConnectionClosed` at the end of the stream or when the size exceeds the limit.
    """
    (size,) = _HEADER.unpack(_recv_exact(sock, _HEADER.size))
    if size > MESSAGE_SIZE_MAX:
        raise ConnectionClosed("Message of {:d} bytes exceeds the limit of {:d}".format(size, MESSAGE_SIZE_MAX))
    return bytes(_recv_exact(sock, size))


def result_response(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(request_id, error):
    return {"jsonrpc": "2.0", "id": request_id, "error": error.as_dict()}
