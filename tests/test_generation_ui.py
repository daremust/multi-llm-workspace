import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.db import add_text_source, create_project, list_generation_runs, list_run_requests, save_project_inputs
from app.generation_ui import cached_catalog
from app.openrouter import OpenRouterError, parse_catalog
from test_openrouter import completion_payload, model_entry

UI_PATH = Path(__file__).resolve().parents[1] / 'app' / 'ui.py'


def widget(elements, label):
    return next(element for element in elements if element.label == label)


@pytest.fixture
def ui_project(app_environment):
    from app.db import initialize_database

    initialize_database(app_environment)
    project = create_project(app_environment, 'Models')
    add_text_source(app_environment, project.id, 'Saved source')
    save_project_inputs(app_environment, project.id, 'Saved task', 'Plain answer')
    return project


@pytest.fixture
def catalog_mock(monkeypatch):
    calls = []
    catalog = parse_catalog({'data': [model_entry('author/free', '0', '0'), model_entry('author/paid'),
                                    model_entry('author/unknown', None, None)]})
    def load(self):
        calls.append('catalog')
        return catalog
    monkeypatch.setattr('app.openrouter.OpenRouter.catalog', load)
    cached_catalog.clear()
    yield calls
    cached_catalog.clear()


def test_missing_key_catalog_free_filter_and_disabled_generation(app_environment, ui_project, catalog_mock):
    app = AppTest.from_file(str(UI_PATH)).run()
    assert not app.exception
    assert not catalog_mock
    assert any('OPENROUTER_API_KEY is not configured' in caption.value for caption in app.caption)
    widget(app.button, 'Load / refresh model catalog').click().run()
    assert catalog_mock == ['catalog']
    widget(app.checkbox, 'Free token pricing only').check().run()
    assert len(widget(app.multiselect, 'Models').options) == 1
    widget(app.multiselect, 'Models').select('author/free').run()
    assert any('Estimated token cost: $0' in entry.value for entry in app.markdown)
    assert widget(app.button, 'Generate').disabled
    assert list_generation_runs(app_environment, ui_project.id) == []
    app.run()
    assert len(catalog_mock) == 1
    widget(app.button, 'Load / refresh model catalog').click().run()
    assert len(catalog_mock) == 2
    assert not app.exception


def test_explicit_generation_reruns_and_reopen(app_environment, ui_project, catalog_mock, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'ui-test-key')
    sent = []
    async def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        if payload['model'] == 'author/paid':
            raise httpx.ReadTimeout('private detail')
        return httpx.Response(200, json=completion_payload(model=payload['model'], usage=None))
    real_client = httpx.AsyncClient
    monkeypatch.setattr('app.generation.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    app = AppTest.from_file(str(UI_PATH)).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').set_value(['author/free', 'author/paid']).run()
    widget(app.text_area, 'Task / instructions').set_value('Unsaved task').run()
    assert not sent
    assert not widget(app.button, 'Generate').disabled
    widget(app.button, 'Generate').click().run()
    assert not app.exception
    assert len(sent) == 2
    assert all(json.loads(payload['messages'][1]['content'])['task'] == 'Saved task' for payload in sent)
    runs = list_generation_runs(app_environment, ui_project.id)
    assert len(runs) == 1 and runs[0].status == 'partial'
    requests = list_run_requests(app_environment, runs[0].id)
    assert requests[0].response.text == 'Generated text'
    assert requests[1].status == 'interrupted'
    assert any('uncertain' in caption.value for caption in app.caption)
    assert any('Cost unknown' in caption.value for caption in app.caption)
    assert 'ui-test-key' not in str(app)
    app.run()
    widget(app.checkbox, 'Free token pricing only').check().run()
    assert len(sent) == 2
    assert len(list_generation_runs(app_environment, ui_project.id)) == 1
    monkeypatch.setenv('OPENROUTER_API_KEY', '')
    reopened = AppTest.from_file(str(UI_PATH)).run()
    assert not reopened.exception
    assert any(text.value == 'Generated text' for text in reopened.text)
    assert len(sent) == 2
    # Selecting just one model creates one new independent request, without replaying a run.
    monkeypatch.setenv('OPENROUTER_API_KEY', 'ui-test-key')
    app.run()
    widget(app.multiselect, 'Models').set_value(['author/free']).run()
    widget(app.button, 'Generate').click().run()
    assert not app.exception
    assert len(sent) == 3
    assert len(list_generation_runs(app_environment, ui_project.id)) == 2


def test_unknown_cost_and_catalog_failure_do_not_generate(app_environment, ui_project, catalog_mock, monkeypatch):
    app = AppTest.from_file(str(UI_PATH)).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').select('author/unknown').run()
    assert any(entry.value == 'Estimated cost: unknown' for entry in app.markdown)
    def fail(self):
        raise OpenRouterError('Catalog unavailable')
    monkeypatch.setattr('app.openrouter.OpenRouter.catalog', fail)
    widget(app.button, 'Load / refresh model catalog').click().run()
    assert not app.exception
    assert any(error.value == 'Model catalog unavailable: Catalog unavailable' for error in app.error)
    assert not app.multiselect
    app.run()
    assert not app.error  # Failed fetch is not automatically retried on rerun.
    assert list_generation_runs(app_environment, ui_project.id) == []


def test_collapsed_generation_summary_retains_cost_and_parameters(app_environment, ui_project, catalog_mock, monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    app = AppTest.from_file(str(UI_PATH)).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').set_value(['author/unknown']).run()
    widget(app.number_input, 'Maximum output tokens per model').set_value(2048).run()
    app.run()
    section = next(e for e in app.expander if e.label.startswith('03  GENERATE'))
    assert not section.proto.expanded
    assert '1 API call' in section.label and 'cost: unknown' in section.label
    assert widget(app.number_input, 'Maximum output tokens per model').value == 2048
    assert widget(app.multiselect, 'Models').value == ['author/unknown']
    assert catalog_mock == ['catalog']
    assert list_generation_runs(app_environment, ui_project.id) == []
    assert not app.exception
