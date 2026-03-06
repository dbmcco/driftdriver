# ABOUTME: WebSocket upgrade, handshake, frame encoding/decoding, and live stream hub.
# ABOUTME: Manages real-time client connections for broadcasting ecosystem snapshots.
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
from typing import Any

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept_key(client_key: str) -> str:
    token = f"{client_key}{_WS_GUID}".encode("ascii")
    digest = hashlib.sha1(token).digest()  # noqa: S324 - websocket protocol requires SHA-1
    return base64.b64encode(digest).decode("ascii")


def _recv_exact(sock_obj: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        piece = sock_obj.recv(count - len(chunks))
        if not piece:
            raise ConnectionError("socket_closed")
        chunks.extend(piece)
    return bytes(chunks)


def _encode_ws_frame(payload: bytes, *, opcode: int = 0x1) -> bytes:
    first = 0x80 | (opcode & 0x0F)
    size = len(payload)
    if size <= 125:
        header = bytes((first, size))
    elif size <= 65535:
        header = bytes((first, 126)) + struct.pack("!H", size)
    else:
        header = bytes((first, 127)) + struct.pack("!Q", size)
    return header + payload


def _read_ws_frame(sock_obj: socket.socket) -> tuple[int, bytes]:
    header = _recv_exact(sock_obj, 2)
    first, second = header[0], header[1]
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    size = second & 0x7F
    if size == 126:
        size = struct.unpack("!H", _recv_exact(sock_obj, 2))[0]
    elif size == 127:
        size = struct.unpack("!Q", _recv_exact(sock_obj, 8))[0]

    mask = _recv_exact(sock_obj, 4) if masked else b""
    payload = _recv_exact(sock_obj, size) if size else b""
    if masked and payload:
        payload = bytes(value ^ mask[idx % 4] for idx, value in enumerate(payload))
    return opcode, payload


class LiveStreamHub:
    def __init__(self, stop_event: threading.Event) -> None:
        self._stop_event = stop_event
        self._lock = threading.Lock()
        self._clients: set[socket.socket] = set()
        self._latest_payload = ""

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def set_latest(self, snapshot: dict[str, Any]) -> str:
        payload = json.dumps(snapshot, sort_keys=False)
        with self._lock:
            self._latest_payload = payload
        return payload

    def latest_payload(self) -> str:
        with self._lock:
            return self._latest_payload

    def register(self, client: socket.socket) -> None:
        with self._lock:
            self._clients.add(client)

    def unregister(self, client: socket.socket) -> None:
        with self._lock:
            self._clients.discard(client)
        try:
            client.close()
        except OSError:
            pass

    def send_payload(self, client: socket.socket, payload: str) -> bool:
        frame = _encode_ws_frame(payload.encode("utf-8"), opcode=0x1)
        try:
            client.sendall(frame)
        except OSError:
            return False
        return True

    def broadcast_snapshot(self, snapshot: dict[str, Any]) -> None:
        payload = self.set_latest(snapshot)
        frame = _encode_ws_frame(payload.encode("utf-8"), opcode=0x1)
        stale: list[socket.socket] = []
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.sendall(frame)
            except OSError:
                stale.append(client)
        for client in stale:
            self.unregister(client)
