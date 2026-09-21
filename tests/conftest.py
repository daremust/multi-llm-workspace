"""All tests use temporary local data and prohibit outbound connections."""

import socket

import pytest

from app.db import initialize_database


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in automated tests.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "data" / "workspace.db"
    initialize_database(path)
    return path


@pytest.fixture
def app_environment(monkeypatch, tmp_path):
    path = tmp_path / "ui" / "workspace.db"
    monkeypatch.setenv("WORKSPACE_DB_PATH", str(path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    return path

