import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from app.db import (
    add_text_source, create_project, get_project, initialize_database, list_optional_runs,
    list_run_requests, save_project_inputs,
)
from app.openrouter import parse_catalog
from app.review import get_primary_response, list_versions, save_review_version, select_primary_response
from test_openrouter import completion_payload, model_entry
from test_optional_compute import critique_result, fact_result
from test_review import seed_run

UI_PATH = Path(__file__).resolve().parents[1] / 'app' / 'ui.py'


def widget(elements, label):
    return next(element for element in elements if element.label == label)


@pytest.fixture
def optional_ui_environment(app_environment, monkeypatch):
    initialize_database(app_environment)
    project = create_project(app_environment, 'Optional UI')
    source = add_text_source(app_environment, project.id, 'The launch was in 2024.')
    save_project_inputs(app_environment, project.id, 'Explain the launch.', 'Plain answer')
    run_id = seed_run(app_environment, project.id, 2)
    responses = [request.response for request in list_run_requests(app_environment, run_id)]
    select_primary_response(app_environment, project.id, responses[0].id)
    version = save_review_version(app_environment, project.id, responses[0].id, 'Saved initial text')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'mock-key')
    def forbidden(*args, **kwargs):
        raise AssertionError('No automatic catalog fetch or initial generation allowed.')
    monkeypatch.setattr('app.openrouter.OpenRouter.catalog', forbidden)
    monkeypatch.setattr('app.generation_ui.generate', forbidden)
    catalog = parse_catalog({'data': [model_entry('author/free', '0', '0'), model_entry('author/unknown', None, None)]})
    app = AppTest.from_file(str(UI_PATH))
    app.session_state['loaded_catalog'] = catalog
    app.run()
    return project, source, responses, version, app


@pytest.fixture
def ui_http(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []
    def install(content):
        async def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json=completion_payload(
                model='author/free', choices=[{'message': {'content': content}, 'finish_reason': 'stop'}], usage=None))
        monkeypatch.setattr('app.generation.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
        return calls
    return install


@pytest.mark.parametrize('operation,label', [('rewrite', 'Rewrite'), ('merge', 'Merge'), ('critique', 'Critique'), ('fact_check', 'Fact check · sources only')])
def test_explicit_operation_estimate_reruns_and_history(app_environment, optional_ui_environment, ui_http, operation, label):
    project, source, responses, version, app = optional_ui_environment
    content = json.dumps(critique_result()) if operation == 'critique' else json.dumps(fact_result(source.id)) if operation == 'fact_check' else 'Candidate output'
    calls = ui_http(content)
    assert not app.exception
    widget(app.selectbox, 'Optional operation').select(operation).run()
    assert widget(app.button, f'Run {label}').disabled
    widget(app.selectbox, 'Optional operation model').select('author/unknown').run()
    if operation == 'merge':
        widget(app.multiselect, 'Inputs to merge').set_value([('response', responses[0].id)]).run()
        assert widget(app.button, 'Run Merge').disabled
        widget(app.multiselect, 'Inputs to merge').set_value([('response', response.id) for response in responses]).run()
    assert any(item.value == 'Estimated additional cost: unknown' for item in app.markdown)
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    assert any(item.value == 'Additional API calls: 1' for item in app.markdown)
    assert any(item.value == 'Estimated additional cost: $0' for item in app.markdown)
    widget(app.text_area, 'Optional instructions').set_value('Keep it concise').run()
    if operation != 'merge':
        widget(app.text_area, 'Working copy').set_value('Unsaved working text').run()
    app.run()
    assert calls == []
    assert list_optional_runs(app_environment, project.id) == []
    widget(app.button, f'Run {label}').click().run()
    assert not app.exception
    assert len(calls) == 1
    payload = json.loads(calls[0]['messages'][1]['content'])
    assert payload['operation_instructions'] == 'Keep it concise'
    if operation != 'merge':
        assert payload['selected_inputs'][0]['text'] == 'Unsaved working text'
    if operation == 'fact_check':
        assert payload['sources'][0]['content'] == source.content
        assert len(payload['selected_inputs']) == 1
    assert get_project(app_environment, project.id).current_version_id == version
    assert get_primary_response(app_environment, project.id).id == responses[0].id
    assert len(list_versions(app_environment, project.id)) == 1
    app.run()
    widget(app.text_area, 'Optional instructions').set_value('Another instruction').run()
    assert len(calls) == 1
    assert len(list_optional_runs(app_environment, project.id)) == 1
    reopened = AppTest.from_file(str(UI_PATH)).run()
    assert not reopened.exception
    assert widget(reopened.selectbox, 'Optional compute history')
    assert len(calls) == 1
    if operation in {'critique', 'fact_check'}:
        assert not any(button.label == 'Save / adopt candidate as current' for button in reopened.button)


def test_candidate_adoption_is_explicit_and_local(app_environment, optional_ui_environment, ui_http, monkeypatch):
    project, _, responses, version, app = optional_ui_environment
    calls = ui_http('Rewrite candidate')
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    widget(app.button, 'Run Rewrite').click().run()
    assert get_project(app_environment, project.id).current_version_id == version
    widget(app.text_area, 'Candidate working copy').set_value('Human edited candidate').run()
    assert len(calls) == 1
    monkeypatch.setenv('OPENROUTER_API_KEY', '')
    app.run()
    widget(app.button, 'Save / adopt candidate as current').click().run()
    assert not app.exception
    adopted = get_project(app_environment, project.id).current_version_id
    assert adopted != version
    assert get_primary_response(app_environment, project.id).id != responses[0].id
    assert widget(app.text_area, 'Working copy').value == 'Human edited candidate'
    assert len(calls) == 1
    before = len(list_versions(app_environment, project.id))
    app.run()
    widget(app.button, 'Save / adopt candidate as current').click().run()
    assert not app.exception
    assert len(list_versions(app_environment, project.id)) == before
    assert len(calls) == 1


def test_invalid_structured_ui_shows_raw_and_does_not_repair(app_environment, optional_ui_environment, ui_http):
    project, _, _, version, app = optional_ui_environment
    calls = ui_http('Malformed feedback')
    widget(app.selectbox, 'Optional operation').select('critique').run()
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    widget(app.button, 'Run Critique').click().run()
    assert not app.exception
    assert any('Invalid structured output' in error.value for error in app.error)
    assert any(text.value == 'Malformed feedback' for text in app.text)
    assert any('no repair request' in caption.value for caption in app.caption)
    app.run()
    assert len(calls) == 1
    assert get_project(app_environment, project.id).current_version_id == version


@pytest.mark.parametrize('capabilities,supported', [(['structured_outputs'], True), (['response_format'], False), (None, False)])
def test_fact_check_compatibility_disclosed_before_execution(optional_ui_environment, ui_http, capabilities, supported):
    _, source, _, _, app = optional_ui_environment
    calls = ui_http(json.dumps(fact_result(source.id)))
    app.session_state['loaded_catalog'] = parse_catalog({'data': [
        model_entry('author/free', '0', '0', supported_parameters=capabilities)]})
    widget(app.selectbox, 'Optional operation').select('fact_check').run()
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    disclosure = 'API-level JSON Schema:' if supported else 'Prompt-only / best-effort structured-output compatibility:'
    assert any(disclosure in caption.value for caption in app.caption)
    assert not app.exception
    assert calls == []
    widget(app.button, 'Run Fact check · sources only').click().run()
    assert not app.exception
    assert len(calls) == 1
    assert ('response_format' in calls[0]) == supported
    app.run()
    assert len(calls) == 1


def test_optional_token_defaults_and_manual_override(optional_ui_environment, ui_http):
    _, _, _, _, app = optional_ui_environment
    calls = ui_http('unused')
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    for operation, expected in [('rewrite', 1024), ('merge', 1024), ('critique', 1024), ('fact_check', 4096)]:
        widget(app.selectbox, 'Optional operation').select(operation).run()
        assert widget(app.number_input, 'Optional maximum output tokens').value == expected
    widget(app.number_input, 'Optional maximum output tokens').set_value(2048).run()
    widget(app.text_area, 'Optional instructions').set_value('Check the sources').run()
    assert widget(app.number_input, 'Optional maximum output tokens').value == 2048
    assert not app.exception
    assert calls == []


@pytest.mark.parametrize('operation,finish,expect_truncation', [
    ('fact_check', 'length', True), ('fact_check', 'stop', False), ('critique', 'length', False),
])
def test_truncated_fact_check_diagnostic_no_retry(app_environment, optional_ui_environment, monkeypatch, operation, finish, expect_truncation):
    project, _, responses, version, app = optional_ui_environment
    calls = []
    raw = '{"claims": [{"claim": "Unfinished'
    real_client = httpx.AsyncClient
    async def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=completion_payload(
            choices=[{'message': {'content': raw}, 'finish_reason': finish}]))
    monkeypatch.setattr('app.generation.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    app.session_state['loaded_catalog'] = parse_catalog({'data': [
        model_entry('author/free', '0', '0', supported_parameters=['structured_outputs'])]})
    widget(app.selectbox, 'Optional operation').select(operation).run()
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    widget(app.number_input, 'Optional maximum output tokens').set_value(2048).run()
    label = 'Fact check · sources only' if operation == 'fact_check' else 'Critique'
    widget(app.button, f'Run {label}').click().run()
    assert not app.exception
    assert any('Invalid structured output' in error.value for error in app.error)
    diagnostic = 'configured token limit (2,048 output tokens) and may have been truncated'
    assert any(diagnostic in error.value for error in app.error) == expect_truncation
    run = list_optional_runs(app_environment, project.id)[0]
    result = list_run_requests(app_environment, run.id)[0]
    assert result.status == 'failed'
    assert (diagnostic in result.error_message) == expect_truncation
    assert result.response.text == raw
    assert result.response.structured_result_json is None
    assert result.cost_usd == 0.001
    assert json.loads(result.response.generation_metadata_json)['structured_output_error'] == result.error_message
    assert get_project(app_environment, project.id).current_version_id == version
    assert get_primary_response(app_environment, project.id).id == responses[0].id
    app.run()
    assert len(calls) == 1
    assert calls[0]['max_tokens'] == 2048
    assert ('response_format' in calls[0]) == (operation == 'fact_check')


def test_fact_check_evidence_disclosed_without_hiding_claim_or_failure(optional_ui_environment, ui_http):
    _, source, _, _, app = optional_ui_environment
    result = fact_result(source.id)
    calls = ui_http(json.dumps(result))
    widget(app.selectbox, 'Optional operation').select('fact_check').run()
    widget(app.selectbox, 'Optional operation model').select('author/free').run()
    widget(app.button, 'Run Fact check · sources only').click().run()
    assert not app.exception
    assert any(t.value == result['claims'][0]['claim'] for t in app.text)
    details = next(e for e in app.expander if e.label == 'Explanation and sources · claim 1')
    assert not details.proto.expanded
    assert any(t.value == result['claims'][0]['references'][0]['excerpt'] for t in details.text)
    assert len(calls) == 1
