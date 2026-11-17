from navigation_support import browser_app
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.clipboard_ui import bulk_payload
from app.db import (create_project, create_run, list_run_requests, mark_request_running,
                    save_completion, finish_generation_run)
from app.openrouter import normalize_completion
from app.review import select_primary_response
from app.review_ui import provider_sources
from test_openrouter import completion_payload


def annotation(url='https://example.org/evidence', **extra):
    return {'type': 'url_citation', 'url_citation': {'url': url, 'title': 'Evidence', **extra}}


@pytest.mark.parametrize('annotations', [None, {}, 'wrong', [None, 3, 'wrong'],
                                        [{'type': 'future_annotation'}]])
def test_missing_malformed_and_unfamiliar_annotations(annotations):
    payload = completion_payload()
    payload['choices'][0]['message']['annotations'] = annotations
    result = normalize_completion(payload, 0)
    assert result.text == 'Generated text'
    assert provider_sources(result.metadata) == []


def test_citation_validation_deduplication_and_preservation():
    entries = [annotation(start_index=0, end_index=4), annotation(),
               annotation('javascript:alert(1)'), annotation('file:///tmp/private'),
               annotation('https://user:secret@example.org'), annotation('https://[broken'),
               annotation('https://example.org:bad'), annotation('https://example.org/\nfoo'),
               annotation('https://example.org/other', title=None),
               {'type': 'future_annotation', 'data': 'retain'},
               {'type': 'url_citation', 'url_citation': None}]
    payload = completion_payload()
    payload['choices'][0]['message'].update(content='  Original [1][3] and array[0].\n', annotations=entries)
    result = normalize_completion(payload, 0)
    assert result.text == '  Original [1][3] and array[0].\n'
    assert result.metadata['annotations'] == entries
    assert provider_sources(result.metadata) == [('Evidence', 'https://example.org/evidence'),
                                                ('https://example.org/other', 'https://example.org/other')]
    payload['choices'][0]['message']['annotations'] = [{'bad': float('nan')}, annotation()]
    assert normalize_completion(payload, 0).metadata['annotations'] == [annotation()]


def test_citations_persist_render_in_compare_and_final_without_changing_copy(database_path, monkeypatch):
    monkeypatch.setenv('WORKSPACE_DB_PATH', str(database_path))
    monkeypatch.setenv('OPENROUTER_API_KEY', '')
    project = create_project(database_path, 'Citation fixture')
    run = create_run(database_path, {'project_id': project.id, 'task': 'Historical task',
                                    'output_format': 'Custom', 'sources': []},
                     {'model_ids': ['fixture/model']}, 'test', None)
    request = list_run_requests(database_path, run)[0]
    payload = completion_payload()
    original = 'Original [1][3], legitimate array[0].'
    payload['choices'][0]['message'].update(content=original, annotations=[annotation(), annotation()])
    result = normalize_completion(payload, 0)
    mark_request_running(database_path, request.id)
    save_completion(database_path, request.id, result, result.metadata)
    finish_generation_run(database_path, run)
    request = list_run_requests(database_path, run)[0]
    assert json.loads(request.response.generation_metadata_json)['annotations'] == [annotation(), annotation()]
    assert request.response.text == original
    assert bulk_payload([request]).endswith(original)
    assert 'https://example.org/evidence' not in bulk_payload([request])
    select_primary_response(database_path, project.id, request.response.id)
    app = browser_app(str(Path(__file__).resolve().parents[1] / 'app/ui.py'), project.id).run()
    assert not app.exception
    assert len([e for e in app.expander if e.label == 'Provider-reported sources']) == 2
    links = app.get('link_button')
    assert len(links) == 2
    assert all(link.proto.url == 'https://example.org/evidence' for link in links)
    editor = next(w for w in app.text_area if w.label == 'Working copy')
    assert editor.value == original
    editor.set_value('Unsaved edited [2]').run()
    assert not app.exception
    assert next(w for w in app.text_area if w.label == 'Working copy').value == 'Unsaved edited [2]'
    assert list_run_requests(database_path, run)[0].response.text == original
