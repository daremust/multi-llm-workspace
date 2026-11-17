from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from app.db import create_project, initialize_database, get_project
from app.navigation_ui import resolve_hash, accept_location, select_project


@pytest.mark.parametrize('value, expected', [('', None), ('#project-1', 1),
    ('#project-2', 2), ('#project-999', None), ('#project-0', None),
    ('#project-01', None), ('#old-name', None), ('#project-1/extra', None),
    ('#project--1', None), (None, None)])
def test_resolve(value, expected):
    assert resolve_hash(value, {1, 2}) == expected


def test_initial_refresh_and_stale_events():
    for fragment, expected in [('', None), ('#project-2', 2), ('#project-99', None)]:
        state = {}
        event = {'hash': fragment, 'revision': 0, 'event': 'initial'}
        assert accept_location(state, event, {1, 2})
        assert state['project_id'] == expected
        assert state['nav_ready']
        assert not accept_location(state, event, {1, 2})
    state = {'nav_ready': True, 'nav_revision': 1, 'project_id': 2,
             'review_draft_2_1_original': 'unsaved'}
    select_project(state, None)
    assert not accept_location(state, {'hash':'#project-2', 'revision':1, 'event':'stale'}, {1,2})
    assert state['project_id'] is None
    assert state['review_draft_2_1_original'] == 'unsaved'
    assert accept_location(state, {'hash':'#project-1', 'revision':2, 'event':'fresh'}, {1,2})
    assert state['project_id'] == 1


def test_ui_home_selection_and_deleted_target(app_environment):
    initialize_database(app_environment)
    one = create_project(app_environment, 'First')
    two = create_project(app_environment, 'Second')
    before = get_project(app_environment, one.id)
    path = str(Path(__file__).resolve().parents[1] / 'app/ui.py')
    loading = AppTest.from_file(path).run()
    assert not loading.exception
    assert not loading.subheader
    assert any(c.value == 'Opening workspace…' for c in loading.caption)
    app = AppTest.from_file(path)
    app.session_state['nav_ready'] = True
    app.session_state['nav_revision'] = 1
    app.session_state['project_id'] = None
    app.session_state['review_draft_1_1_original'] = 'Keep draft'
    app.run()
    assert not app.exception
    assert not app.subheader
    next(w for w in app.sidebar.selectbox if w.label == 'Open project').select(one.id).run()
    assert app.subheader[0].value == 'First'
    next(w for w in app.sidebar.button if w.key == 'workspace_home').click().run()
    assert not app.subheader
    assert app.session_state['project_id'] is None
    assert app.session_state['review_draft_1_1_original'] == 'Keep draft'
    app.session_state['project_id'] = 999
    app.run()
    assert app.session_state['project_id'] is None
    assert not app.exception
    assert get_project(app_environment, one.id) == before
