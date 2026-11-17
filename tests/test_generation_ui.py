from navigation_support import browser_app
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
    app = browser_app(str(UI_PATH), ui_project.id).run()
    assert not app.exception
    assert not catalog_mock
    assert any('OPENROUTER_API_KEY is not configured' in caption.value for caption in app.caption)
    widget(app.button, 'Load / refresh model catalog').click().run()
    assert catalog_mock == ['catalog']
    widget(app.checkbox, 'Free token pricing only').check().run()
    assert len(widget(app.multiselect, 'Models').options) == 1
    widget(app.multiselect, 'Models').select('author/free').run()
    assert any('Base-rate token estimate: $0' in entry.value for entry in app.markdown)
    assert widget(app.button, 'Generate').disabled
    assert list_generation_runs(app_environment, ui_project.id) == []
    app.run()
    assert len(catalog_mock) == 1
    widget(app.button, 'Load / refresh model catalog').click().run()
    assert len(catalog_mock) == 2
    assert not app.exception


@pytest.mark.parametrize('with_source', [False, True])
def test_explicit_generation_reruns_and_reopen(app_environment, ui_project, catalog_mock, monkeypatch, with_source):
    if not with_source:
        from app.db import list_sources, remove_source
        for source in list_sources(app_environment, ui_project.id):
            remove_source(app_environment, ui_project.id, source.id)
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
    app = browser_app(str(UI_PATH), ui_project.id).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').set_value(['author/free', 'author/paid']).run()
    widget(app.number_input, 'Maximum output tokens per model').set_value(2048).run()
    widget(app.slider, 'Temperature').set_value(0.4).run()
    widget(app.text_area, 'Task / instructions').set_value('Unsaved task').run()
    widget(app.selectbox, 'Output format').select('Email').run()
    app.run()
    assert any('2 API calls' in c.value for c in app.caption)
    assert not sent
    assert not widget(app.button, 'Generate').disabled
    widget(app.selectbox, 'Model to star / unstar').select('author/paid').run()
    widget(app.button, 'Star').click().run()
    assert widget(app.multiselect, 'Models').value == ['author/free', 'author/paid']
    assert not sent
    widget(app.button, 'Generate').click().run()
    assert not app.exception
    assert len(sent) == 2
    assert {p['model'] for p in sent} == {'author/free', 'author/paid'}
    assert all(bool(json.loads(p['messages'][1]['content'])['sources']) == with_source for p in sent)
    assert all(json.loads(p['messages'][1]['content'])['output_format'] == 'Plain answer' for p in sent)
    assert all(p['max_tokens'] == 2048 and p['temperature'] == 0.4 for p in sent)
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
    reopened = browser_app(str(UI_PATH), ui_project.id).run()
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
    app = browser_app(str(UI_PATH), ui_project.id).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').select('author/unknown').run()
    assert any(entry.value == 'Total cost: unknown' for entry in app.markdown)
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
    app = browser_app(str(UI_PATH), ui_project.id).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').set_value(['author/unknown']).run()
    widget(app.number_input, 'Maximum output tokens per model').set_value(2048).run()
    app.run()
    section = next(e for e in app.expander if e.label.startswith('03  GENERATE'))
    assert not section.proto.expanded
    assert section.label == '03  GENERATE'
    assert any('1 API call' in c.value and 'cost: unknown' in c.value for c in app.caption)
    assert widget(app.number_input, 'Maximum output tokens per model').value == 2048
    assert widget(app.multiselect, 'Models').value == ['author/unknown']
    assert catalog_mock == ['catalog']
    assert list_generation_runs(app_environment, ui_project.id) == []
    assert not app.exception


def test_reported_models_show_base_estimate_and_warning_without_dispatch(
        app_environment, ui_project, pricing_catalog, monkeypatch):
    from app.generation import prepare_generation
    from app.cost_ui import UNKNOWN_TOTAL_WARNING, estimate_summary, money

    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    def forbidden(*args, **kwargs):
        pytest.fail('Estimate display must not fetch or generate')
    monkeypatch.setattr('app.openrouter.OpenRouter.catalog', forbidden)
    monkeypatch.setattr('app.openrouter.OpenRouter.complete', forbidden)
    save_project_inputs(app_environment, ui_project.id, 'Saved task', 'LinkedIn post')
    app = browser_app(str(UI_PATH), ui_project.id)
    app.session_state['loaded_catalog'] = pricing_catalog
    app.run()
    ids = [model.id for model in pricing_catalog.models]
    selections = [[model_id] for model_id in ids] + [
        ['openai/gpt-6-luna', '~anthropic/claude-sonnet-latest'], ids]
    for selected in selections:
        widget(app.multiselect, 'Models').set_value(selected).run()
        app.run()  # The section heading is computed before the selector on each rerun.
        plan = prepare_generation(app_environment, ui_project.id, pricing_catalog, selected)
        assert any(entry.value == f'Base-rate token estimate: {money(plan.estimate.token_cost_usd)}'
                   for entry in app.markdown)
        assert any(entry.value == 'Total cost: unknown' for entry in app.markdown)
        assert any(entry.value == UNKNOWN_TOTAL_WARNING for entry in app.warning)
        section = next(e for e in app.expander if e.label.startswith('03  GENERATE'))
        assert section.label == '03  GENERATE'
        assert any(estimate_summary(plan.estimate) in c.value for c in app.caption)
        assert not widget(app.button, 'Generate').disabled
        assert not app.exception
    assert list_generation_runs(app_environment, ui_project.id) == []


@pytest.mark.parametrize('task,output', [('', 'Plain answer'), ('Saved task', 'Custom')])
def test_source_free_invalid_saved_inputs_disable_generation(app_environment, catalog_mock, monkeypatch, task, output):
    from app.db import initialize_database, connect
    initialize_database(app_environment)
    project = create_project(app_environment, 'Incomplete source-free')
    with connect(app_environment) as connection:
        connection.execute('UPDATE projects SET task = ?, output_format = ? WHERE id = ?',
                           (task, output, project.id))
    monkeypatch.setenv('OPENROUTER_API_KEY', 'fake')
    app = browser_app(str(UI_PATH), project.id).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    widget(app.multiselect, 'Models').set_value(['author/free']).run()
    widget(app.text_area, 'Task / instructions').set_value('Unsaved valid task').run()
    if output == 'Custom':
        widget(app.text_area, 'Custom format / instructions').set_value('Unsaved format').run()
    app.run()
    assert not app.exception
    assert widget(app.button, 'Generate').disabled
    assert any('Check inputs / catalog' in c.value for c in app.caption)
    assert list_generation_runs(app_environment, project.id) == []


def test_favorites_preserve_selection_estimates_and_history(app_environment, ui_project, catalog_mock, monkeypatch):
    from app.model_favorites import load_favorites, set_favorite
    from app.generation_ui import model_label
    from app.generation import prepare_generation
    from streamlit.elements.lib import policies

    original = policies.check_session_state_rules
    monkeypatch.setattr(policies, '_shown_default_value_warning', False)
    def check(default_value, key, writes_allowed=True):
        watched = key is not None and key.startswith(('models_', 'favorite_model_'))
        if watched:
            policies._shown_default_value_warning = False
            assert not policies.config.get_option('global.disableWidgetStateDuplicationWarning')
        result = original(default_value, key, writes_allowed)
        if watched:
            assert not policies._shown_default_value_warning
        return result
    monkeypatch.setattr(policies, 'check_session_state_rules', check)
    other = create_project(app_environment, 'Other favorites')
    app = browser_app(str(UI_PATH), ui_project.id).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    catalog = app.session_state['loaded_catalog']
    names = {m.id: model_label(m) for m in catalog.models}
    def selections(expected):
        assert not app.exception
        assert widget(app.multiselect, 'Models').value == expected
    def star(mid):
        widget(app.selectbox, 'Model to star / unstar').select(mid).run()
        widget(app.button, 'Star').click().run()
    star('author/paid')
    selections([])
    assert widget(app.multiselect, 'Models').options == [names['author/paid'], names['author/free'], names['author/unknown']]
    widget(app.multiselect, 'Models').set_value(['author/free', 'author/paid']).run()
    before = prepare_generation(app_environment, ui_project.id, catalog, ['author/free', 'author/paid'])
    star('author/unknown')
    selections(['author/free', 'author/paid'])
    widget(app.button, 'Unstar').click().run()
    app.run()
    selections(['author/free', 'author/paid'])
    widget(app.sidebar.button, 'Multi-LLM\nWorkspace').click().run()
    widget(app.sidebar.selectbox, 'Open project').select(other.id).run()
    selections([])
    assert widget(app.multiselect, 'Models').options[0] == names['author/paid']
    widget(app.sidebar.selectbox, 'Open project').select(ui_project.id).run()
    selections(['author/free', 'author/paid'])
    after = prepare_generation(app_environment, ui_project.id, catalog, widget(app.multiselect, 'Models').value)
    assert after.parameters == before.parameters and after.estimate == before.estimate
    widget(app.checkbox, 'Free token pricing only').check().run()
    selections(['author/free'])  # Existing eligibility rule still removes paid selections.
    assert load_favorites(app_environment) == {'author/paid'}
    set_favorite(app_environment, 'missing/model', True)
    app.session_state['loaded_catalog'] = parse_catalog({'data': [model_entry('author/free', '0', '0')]})
    app.run()
    assert load_favorites(app_environment) == {'author/paid', 'missing/model'}
    assert any('unavailable' in option for option in widget(app.selectbox, 'Model to star / unstar').options)
    fresh = browser_app(str(UI_PATH), ui_project.id)
    fresh.session_state['loaded_catalog'] = catalog
    fresh.run()
    assert widget(fresh.multiselect, 'Models').value == []
    assert widget(fresh.multiselect, 'Models').options[0] == names['author/paid']
    assert catalog_mock == ['catalog']
    assert list_generation_runs(app_environment, ui_project.id) == []


def test_favorites_preferences_errors_do_not_break_generation(app_environment, ui_project, catalog_mock, monkeypatch):
    from app.model_favorites import FavoritesError, favorites_path
    path = favorites_path(app_environment)
    path.write_text('malformed')
    app = browser_app(str(UI_PATH), ui_project.id).run()
    widget(app.button, 'Load / refresh model catalog').click().run()
    assert not app.exception
    assert any('Could not read model favorites' in error.value for error in app.error)
    assert not any(w.label == 'Model to star / unstar' for w in app.selectbox)
    assert path.read_text() == 'malformed'
    assert len(widget(app.multiselect, 'Models').options) == 3
    path.write_text('[]')
    app.run()
    widget(app.selectbox, 'Model to star / unstar').select('author/free').run()
    def denied(*args):
        raise FavoritesError('Could not save model favorites.')
    monkeypatch.setattr('app.generation_ui.set_favorite', denied)
    widget(app.button, 'Star').click().run()
    assert not app.exception
    assert any('Could not save model favorites' in error.value for error in app.error)
    assert widget(app.multiselect, 'Models').value == []
    assert path.read_text() == '[]'
    assert catalog_mock == ['catalog']
    assert list_generation_runs(app_environment, ui_project.id) == []
