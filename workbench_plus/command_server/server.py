# SPDX-FileCopyrightText: 2026 Blender Workbench Plus Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Socket side of the Command Server, independent of ``bpy``.

Connections are read in background threads, which only parse and authenticate requests and queue them.
The queued requests are executed by :meth:`Server.pump`, which must be called from Blender's main thread
(by a timer with the interface, or in a loop in background mode).
"""

__all__ = (
    "Server",
)

import hmac
import json
import queue
import socket
import threading

from . import protocol
from .protocol import RpcError

HOST = "127.0.0.1"


class _Request:
    __slots__ = ("connection", "id", "method", "params", "is_notification")

    def __init__(self, connection, request_id, method, params, is_notification):
        self.connection = connection
        self.id = request_id
        self.method = method
        self.params = params
        self.is_notification = is_notification


def _request_parse(data):
    """Return ``(id, method, params, is_notification)`` from JSON bytes or raise :class:`RpcError`."""
    try:
        message = json.loads(data)
    except (UnicodeDecodeError, ValueError) as ex:
        raise RpcError(protocol.PARSE_ERROR, "Parse error: {:s}".format(str(ex)))
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise RpcError(protocol.INVALID_REQUEST, "Invalid request: expected a JSON-RPC 2.0 object")
    method = message.get("method")
    if not isinstance(method, str):
        raise RpcError(protocol.INVALID_REQUEST, "Invalid request: 'method' must be a string")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise RpcError(protocol.INVALID_PARAMS, "Invalid params: 'params' must be an object")
    is_notification = "id" not in message
    return message.get("id"), method, params, is_notification


class Connection:
    """A client connection. Sending is thread safe."""

    def __init__(self, server, sock, address):
        self.server = server
        self.address = address
        self.is_authenticated = False
        # Names of the events this client receives, see `Server.notify`.
        self.events = set()
        self._sock = sock
        self._send_lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(
            target=self._read_loop,
            name="wbp-command-server-{:s}:{:d}".format(*address),
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def send(self, message):
        """Send ``message``, returning False when the connection is (or just got) closed."""
        data = protocol.message_encode(message)
        with self._send_lock:
            if self._closed:
                return False
            try:
                self._sock.sendall(data)
            except OSError:
                self._close_locked()
                return False
        return True

    def close(self):
        with self._send_lock:
            self._close_locked()

    def join(self, timeout=None):
        if self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def _close_locked(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self.server._connection_remove(self)

    def _read_loop(self):
        try:
            while True:
                data = protocol.message_recv(self._sock)
                try:
                    request_id, method, params, is_notification = _request_parse(data)
                except RpcError as ex:
                    self.send(protocol.error_response(None, ex))
                    continue

                if not self.is_authenticated:
                    if not self._authenticate(request_id, method, params):
                        break
                    continue

                self.server._queue.put(_Request(self, request_id, method, params, is_notification))
        except (protocol.ConnectionClosed, OSError):
            pass
        finally:
            self.close()

    def _authenticate(self, request_id, method, params):
        token = params.get("token")
        if method == "auth" and isinstance(token, str) and hmac.compare_digest(
                token.encode("utf-8"), self.server.token.encode("utf-8")):
            self.is_authenticated = True
            self.send(protocol.result_response(request_id, True))
            return True
        error = RpcError(protocol.UNAUTHORIZED, "Unauthorized: the first request must be 'auth' with a valid token")
        self.send(protocol.error_response(request_id, error))
        return False


class Server:
    """
    Accepts connections on ``127.0.0.1:port`` (``port`` 0 picks a free port).

    :arg token: The token clients must send with ``auth``.
    :arg dispatch: ``dispatch(method, params, connection) -> result``, called on the main thread by
       :meth:`pump`. It raises :class:`RpcError` for errors to send back.
    """

    _ACCEPT_POLL_INTERVAL = 0.1

    def __init__(self, *, port, token, dispatch):
        self.token = token
        self._dispatch = dispatch
        self._queue = queue.SimpleQueue()
        self._connections = set()
        self._connections_lock = threading.Lock()
        self._listener = socket.create_server((HOST, port))
        # Closing a socket does not wake up a blocked `accept()` on every platform, so poll instead.
        self._listener.settimeout(self._ACCEPT_POLL_INTERVAL)
        self.port = self._listener.getsockname()[1]
        self._accept_thread = threading.Thread(target=self._accept_loop, name="wbp-command-server", daemon=True)
        self._running = False

    @property
    def is_running(self):
        return self._running

    @property
    def connections(self):
        with self._connections_lock:
            return tuple(self._connections)

    def start(self):
        self._running = True
        self._accept_thread.start()

    def stop(self, timeout=1.0):
        """Close the listening socket and every connection. Requests still queued are dropped."""
        if not self._running:
            # Never started, or already stopped.
            self._listener.close()
            return
        self._running = False
        self._accept_thread.join(timeout)
        self._listener.close()
        for connection in self.connections:
            connection.close()
            connection.join(timeout)

    def pump(self, timeout=0.0):
        """
        Execute queued requests on the calling (main) thread and return how many were executed.

        Waits up to ``timeout`` seconds for the first request (``None`` waits forever), then executes
        everything already queued without waiting.
        """
        count = 0
        block = timeout is None or timeout > 0.0
        while self._running:
            try:
                request = self._queue.get(block=block, timeout=timeout if block else None)
            except queue.Empty:
                break
            block = False
            self._execute(request)
            count += 1
        return count

    def notify(self, event, params=None):
        """Send the ``event`` notification to the connections subscribed to it."""
        message = {"jsonrpc": "2.0", "method": "event", "params": {"event": event, **(params or {})}}
        for connection in self.connections:
            if event in connection.events:
                connection.send(message)

    def _execute(self, request):
        try:
            result = self._dispatch(request.method, request.params, request.connection)
            response = protocol.result_response(request.id, result)
        except RpcError as ex:
            response = protocol.error_response(request.id, ex)
        except Exception as ex:
            response = protocol.error_response(request.id, RpcError(
                protocol.INTERNAL_ERROR, "Internal error: {:s}: {:s}".format(type(ex).__name__, str(ex))))
        if request.is_notification:
            return
        try:
            request.connection.send(response)
        except (TypeError, ValueError) as ex:
            # The result is too large or not serializable.
            request.connection.send(protocol.error_response(request.id, RpcError(
                protocol.INTERNAL_ERROR, "Internal error: {:s}".format(str(ex)))))

    def _accept_loop(self):
        while self._running:
            try:
                sock, address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            connection = Connection(self, sock, address)
            with self._connections_lock:
                if not self._running:
                    sock.close()
                    break
                self._connections.add(connection)
            connection.start()

    def _connection_remove(self, connection):
        with self._connections_lock:
            self._connections.discard(connection)
