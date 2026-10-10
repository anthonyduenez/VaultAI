"""Process-wide outbound connection guard.

Documents never leave the machine, so any socket connect to an address that is
not loopback, private, or link-local is refused before it reaches the network.
"""

from __future__ import annotations

import socket
import threading

from vaultai.config import is_local_ip

_lock = threading.Lock()
_installed = False


class EgressBlocked(OSError):
    pass


def _check(family: int, address: object) -> None:
    if family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple) or not address:
        return
    host, port = address[0], address[1] if len(address) > 1 else 0
    infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
    for info in infos:
        if not is_local_ip(str(info[4][0])):
            raise EgressBlocked(f"outbound connection to a non-local address is blocked ({host})")


def install_egress_guard() -> None:
    global _installed
    with _lock:
        if _installed:
            return
        original_connect = socket.socket.connect
        original_connect_ex = socket.socket.connect_ex

        def connect(self: socket.socket, address: object) -> None:
            _check(self.family, address)
            return original_connect(self, address)

        def connect_ex(self: socket.socket, address: object) -> int:
            _check(self.family, address)
            return original_connect_ex(self, address)

        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        _installed = True

