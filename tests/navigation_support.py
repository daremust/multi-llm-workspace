"""Simulate the browser handshake; keep real navigation component mounting."""
from streamlit.testing.v1 import AppTest
from app.config import load_settings
from app.db import list_projects
from app.navigation_ui import accept_location


def browser_app(path, project_id=None):
    app = AppTest.from_file(path).run()
    assert not app.exception, str(app.exception)
    assert any(c.value == 'Opening workspace…' for c in app.caption)
    ids = {p.id for p in list_projects(load_settings().database_path)}
    assert project_id is None or project_id in ids
    state = {}
    assert accept_location(state, {
        'hash': f'#project-{project_id}' if project_id is not None else '',
        'revision': 0, 'event': 'test-initial-location',
    }, ids)
    for key, value in state.items():
        app.session_state[key] = value
    return app
