"""The test suite is offline: opening a network socket fails instead of reaching a host."""

from __future__ import annotations

import socket

import pytest
from pytest_socket import SocketBlockedError

# pytest-socket warns on every block; here the block is the expected outcome.
pytestmark = pytest.mark.filterwarnings("ignore:A test tried to use socket:UserWarning")


def test_opening_a_network_socket_is_blocked() -> None:
    # Blocked at socket creation, before DNS or connect, so an accidental live call
    # fails in CI instead of passing silently.
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_dns_lookup_is_blocked() -> None:
    with pytest.raises(SocketBlockedError):
        socket.getaddrinfo("example.com", 443)
