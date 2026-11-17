from navigation_support import browser_app
import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.clipboard_ui import bulk_payload, selection_key
from app.db import connect, create_project, get_project, list_run_requests, save_project_inputs
from app.review import select_primary_response
from test_review import counts, seed_run

UI_PATH = Path(__file__).resolve().parents[1] / 'app' / 'ui.py'


def test_bulk_payload_allowlist_and_failed_exclusion(database_path):
    project = create_project(database_path, 'Copy')
    run = seed_run(database_path, project.id, failed=True)
    requests = list_run_requests(database_path, run)
    # A failed response with a saved partial body must also be excluded.
    failed = requests[0].model_copy(update={'status': 'failed'})
    snapshot = dict(task='Old task', additional_instructions='Old instructions', output_format='Custom',
                    custom_output_format='Old format', api_key='SECRET', sources=[dict(
                        id=4, title='Original title', original_url='https://example.org/start',
                        final_url='https://example.org/article', content='Original source', secret='SECRET')])
    text = bulk_payload(requests + [failed], snapshot)
    for expected in ['Old task', 'Old instructions', 'Old format', 'Original title',
                     'https://example.org/start', 'https://example.org/article', 'Original source',
                     'MODEL: Model 0', 'MODEL: Model 1']:
        assert expected in text
    assert 'SECRET' not in text and 'model-2' not in text
    assert text.count('Original response 0') == 1
    selected = bulk_payload([requests[1]])
    assert 'Original response 1' in selected and 'Original response 0' not in selected
    assert 'ORIGINAL TASK' not in selected
    assert bulk_payload([failed], snapshot) == ''
    assert len({selection_key(1, 1, 1), selection_key(2, 1, 1), selection_key(1, 2, 1)}) == 3


def test_copy_ui_historical_context_isolation_and_unsaved_draft(database_path, monkeypatch):
    monkeypatch.setenv('WORKSPACE_DB_PATH', str(database_path))
    monkeypatch.setenv('OPENROUTER_API_KEY', '')
    def forbidden(*args, **kwargs):
        raise AssertionError('Copy must never call a provider')
    monkeypatch.setattr('app.openrouter.OpenRouter.catalog', forbidden)
    monkeypatch.setattr('app.openrouter.OpenRouter.complete', forbidden)
    captured = {}
    def capture(key, actions, **kwargs):
        captured[key] = dict(actions=actions, **kwargs)
    monkeypatch.setattr('app.review_ui.copy_controls', capture)
    project = create_project(database_path, 'First')
    other = create_project(database_path, 'Other')
    old_run = seed_run(database_path, project.id, failed=True)
    new_run = seed_run(database_path, project.id, 1)
    seed_run(database_path, other.id, 1)
    snapshot = dict(task='HISTORICAL TASK', output_format='Plain answer', sources=[
        dict(id=1, title='Historical source', content='HISTORICAL CONTENT')])
    with connect(database_path) as connection:
        connection.execute('UPDATE runs SET input_snapshot_json = ? WHERE id = ?', (json.dumps(snapshot), old_run))
    save_project_inputs(database_path, project.id, 'CURRENT TASK', 'Email')
    response = list_run_requests(database_path, old_run)[0].response
    select_primary_response(database_path, project.id, response.id)
    before = counts(database_path)
    original_project = get_project(database_path, project.id)
    app = browser_app(str(UI_PATH), project.id).run()
    app.sidebar.selectbox[0].select(project.id).run()
    next(w for w in app.selectbox if w.label == 'Generation run').select(old_run).run()
    bulk_key = f'copy_bulk_{project.id}_{old_run}'
    assert captured[bulk_key]['actions'][0]['disabled']
    assert captured[bulk_key]['actions'][1]['label'] == 'Copy all (2)'
    boxes = [w for w in app.checkbox if w.label == 'Select to copy']
    assert len(boxes) == 2
    boxes[0].check().run()
    assert captured[bulk_key]['actions'][0]['label'] == 'Copy selected (1)'
    assert 'HISTORICAL TASK' not in captured[bulk_key]['actions'][0]['text']
    next(w for w in app.checkbox if w.label == 'Include original task and sources').check().run()
    payload = captured[bulk_key]['actions'][0]['text']
    assert 'HISTORICAL TASK' in payload and 'HISTORICAL CONTENT' in payload
    assert 'CURRENT TASK' not in payload and 'Original response 1' not in payload
    # Repeated option changes reuse the same component key and replace its data.
    for index in range(8):
        include = index % 2 == 0
        next(w for w in app.checkbox if w.label == 'Include original task and sources').set_value(include).run()
        selected = index % 3 != 0
        next(w for w in app.checkbox if w.label == 'Select to copy').set_value(selected).run()
        actions = captured[bulk_key]['actions']
        assert len(actions) == 2
        assert actions[0]['label'] == f'Copy selected ({int(selected)})'
        assert actions[0]['disabled'] == (not selected)
        assert ('HISTORICAL TASK' in actions[1]['text']) == include
        assert 'CURRENT TASK' not in actions[1]['text']
        assert len([key for key in captured if key == bulk_key]) == 1
    request = list_run_requests(database_path, old_run)[0]
    individual = captured[f'copy_response_{project.id}_{old_run}_{request.id}']['actions'][0]
    assert individual['text'] == response.text and individual['icon']
    next(w for w in app.text_area if w.label == 'Working copy').set_value('  UNSAVED\n☃ </script>').run()
    final = next(value for key, value in captured.items() if key.startswith(f'copy_final_copy_editor_{project.id}_'))
    assert final['actions'][0]['text'] == '  UNSAVED\n☃ </script>'
    assert final['editor'].startswith(f'copy_editor_{project.id}_')
    next(w for w in app.selectbox if w.label == 'Generation run').select(new_run).run()
    assert not any(w.value for w in app.checkbox if w.label in ('Select to copy', 'Include original task and sources'))
    app.sidebar.selectbox[0].select(other.id).run()
    assert not any(w.value for w in app.checkbox if w.label in ('Select to copy', 'Include original task and sources'))
    assert not app.exception
    assert counts(database_path) == before
    assert get_project(database_path, project.id) == original_project
    assert all(r.response.human_rating is None for r in list_run_requests(database_path, old_run) if r.response)
