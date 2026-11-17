"""Exercise the real entrypoint in a fresh interpreter, independent of pytest imports."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('preloaded_module', [False, True])
def test_workspace_entrypoint_import_and_render(tmp_path, preloaded_module):
    script = '''
import socket
import sys
from pathlib import Path

def no_network(*args, **kwargs):
    raise AssertionError("Startup must not make network requests")
for name in ("connect", "connect_ex", "sendto"):
    setattr(socket.socket, name, no_network)
socket.create_connection = no_network
socket.getaddrinfo = no_network

if sys.argv[1] == "preloaded":
    import app.generation_ui as generation_ui
    # Reproduce the pre-cost-change module retained by a running Streamlit server.
    for name in ("generation_summary", "estimate_summary", "render_estimate"):
        generation_ui.__dict__.pop(name, None)

from app.config import load_settings
from app.db import initialize_database, create_project
path = load_settings().database_path
initialize_database(path)
project = create_project(path, "Startup regression")
import app.ui  # Import the actual entry point before executing it as a script.
sys.path.insert(0, str(Path('tests').resolve()))
from pytest import MonkeyPatch
from apptest_compat import install_registry_adapter
from navigation_support import browser_app
patcher = MonkeyPatch()
install_registry_adapter(patcher)
app = browser_app(str(Path("app/ui.py").resolve()), project.id).run()
patcher.undo()
assert not app.exception, str(app.exception)
assert app.subheader[0].value == "Startup regression"
assert any(e.label.startswith("03  GENERATE") for e in app.expander)
'''
    env = dict(os.environ, OPENROUTER_API_KEY='', WORKSPACE_DB_PATH=str(tmp_path / 'workspace.db'),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-B', '-c', script,
                             'preloaded' if preloaded_module else 'fresh'],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
