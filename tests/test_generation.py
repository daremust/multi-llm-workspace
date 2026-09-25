import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.db import (
    add_manual_response, add_text_source, connect, create_generation_run, create_project,
    get_generation_run, list_manual_responses, list_run_requests, save_project_inputs,
)
from app.generation import MAX_CONCURRENCY, PROMPT_VERSION, estimate_cost, execute_run, generate, prepare_generation
from app.openrouter import OpenRouter, parse_catalog
from test_openrouter import completion_payload, model_entry


@pytest.fixture
def saved_project(database_path):
    project = create_project(database_path, 'Generation')
    add_text_source(database_path, project.id, 'Original source with facts.', 'Reference')
    save_project_inputs(database_path, project.id, 'Summarize facts.', 'Custom', 'Two bullets')
    return project


@pytest.fixture
def mock_async_http(monkeypatch):
    real_client = httpx.AsyncClient
    def install(handler):
        def client(**kwargs):
            assert not kwargs['follow_redirects']
            assert not kwargs['trust_env']
            return real_client(transport=httpx.MockTransport(handler), **kwargs)
        monkeypatch.setattr('app.generation.httpx.AsyncClient', client)
    return install


def test_saved_input_grounding_and_snapshot(database_path, saved_project, mock_async_http):
    catalog = parse_catalog({'data': [model_entry()]})
    with connect(database_path) as connection:
        connection.execute('UPDATE projects SET additional_instructions = ? WHERE id = ?', ('Keep nuance.', saved_project.id))
    plan = prepare_generation(database_path, saved_project.id, catalog, ['author/model'])
    sent = []
    def handler(request):
        payload = json.loads(request.content)
        sent.append(payload)
        user = json.loads(payload['messages'][1]['content'])
        assert user['task'] == 'Summarize facts.'
        assert user['additional_instructions'] == 'Keep nuance.'
        assert user['sources'][0]['content'] == 'Original source with facts.'
        assert user['output_format'] == 'Custom'
        assert user['custom_output_format'] == 'Two bullets'
        assert payload['model'] == 'author/model'
        assert payload['max_tokens'] == 1024
        assert payload['temperature'] == 0.7
        assert payload['stream'] is False
        assert payload['provider'] == {'allow_fallbacks': False, 'require_parameters': True}
        assert payload['transforms'] == []
        return httpx.Response(200, json=completion_payload())
    mock_async_http(handler)
    manual = add_manual_response(database_path, saved_project.id, 'Manual content', 'Claim', 'Model')
    run_id = generate(database_path, plan, OpenRouter(SecretStr('fake')))
    run = get_generation_run(database_path, run_id)
    assert run.operation == 'generation'
    assert run.status == 'succeeded'
    assert run.prompt_version == PROMPT_VERSION
    assert run.completed_at is not None
    assert run.estimated_cost_usd == float(plan.estimate.cost_usd)
    assert json.loads(run.parameters_json)['requests']['author/model'] == sent[0]
    assert json.loads(run.parameters_json)['catalog_models']['author/model']['pricing']['prompt'] == '0.000001'
    request = list_run_requests(database_path, run_id)[0]
    assert request.status == 'succeeded'
    assert request.input_tokens == 40 and request.output_tokens == 20
    assert request.cost_usd == 0.001 and request.latency_ms >= 0
    response = request.response
    assert response.origin == 'api'
    assert response.verified_provider == 'openrouter'
    assert response.verified_model == 'author/model'
    assert response.claimed_provider is response.claimed_model is None
    assert response.run_request_id == request.id
    metadata = json.loads(response.generation_metadata_json)
    assert metadata['gateway'] == 'openrouter'
    assert metadata['model_namespace'] == 'author'
    assert metadata['upstream_provider'] == 'Upstream host'
    assert list_manual_responses(database_path, saved_project.id) == [manual]
    save_project_inputs(database_path, saved_project.id, 'Different task', 'Email')
    add_text_source(database_path, saved_project.id, 'Later source')
    assert get_generation_run(database_path, run_id).input_snapshot_json == run.input_snapshot_json
    assert len(json.loads(run.input_snapshot_json)['sources']) == 1
    # Re-dispatching a persisted run never spends again.
    assert asyncio.run(execute_run(database_path, run_id, OpenRouter(SecretStr('fake')))) is False
    assert len(sent) == 1
    with connect(database_path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_parallel_bounded_partial_results_and_null_usage(database_path, saved_project, mock_async_http):
    entries = [model_entry(f'author/model-{i}') for i in range(6)]
    catalog = parse_catalog({'data': entries})
    plan = prepare_generation(database_path, saved_project.id, catalog, [entry['id'] for entry in entries])
    active = 0
    maximum = 0
    calls = []
    async def handler(request):
        nonlocal active, maximum
        model = json.loads(request.content)['model']
        calls.append(model)
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        if model.endswith('-1'):
            raise httpx.ReadTimeout('uncertain')
        if model.endswith('-3'):
            return httpx.Response(429)
        return httpx.Response(200, json=completion_payload(model=model, usage=None))
    mock_async_http(handler)
    run_id = generate(database_path, plan, OpenRouter(SecretStr('fake')))
    assert maximum == MAX_CONCURRENCY
    assert len(calls) == 6 and len(set(calls)) == 6
    assert get_generation_run(database_path, run_id).status == 'partial'
    requests = list_run_requests(database_path, run_id)
    assert [request.status for request in requests] == ['succeeded', 'interrupted', 'succeeded', 'failed', 'succeeded', 'succeeded']
    assert sum(request.response is not None for request in requests) == 4
    for request in requests:
        assert request.input_tokens is request.output_tokens is request.cost_usd is None
    assert 'uncertain' in requests[1].error_message


@pytest.mark.parametrize('prompt,completion,expected', [('0', '0', Decimal(0)), ('0.001', '0.002', 'paid'), (None, '0', None), ('-1', '0', None)])
def test_estimates(prompt, completion, expected):
    model = parse_catalog({'data': [model_entry(prompt=prompt, completion=completion)]}).models[0]
    messages = [{'role': 'user', 'content': 'abcd'}]
    estimate = estimate_cost([model], messages, 100)
    assert estimate.input_tokens == 9
    assert estimate.max_output_tokens == 100
    assert estimate.cost_usd == (Decimal('0.209') if expected == 'paid' else expected)


def test_estimate_aggregate_unknown_and_extra_fees():
    catalog = parse_catalog({'data': [model_entry('a', '0', '0'), model_entry('b', None, None)]})
    assert estimate_cost(list(catalog.models), [], 100).cost_usd is None
    model = parse_catalog({'data': [model_entry(pricing={'prompt': '0', 'completion': '0', 'request': '0.01'})]}).models[0]
    estimate = estimate_cost([model], [], 100)
    assert estimate.token_cost_usd == 0
    assert estimate.cost_usd == Decimal('0.01')
    model = replace(model, pricing={**model.pricing, 'unknown_fee': '1'})
    assert estimate_cost([model], [], 100).cost_usd is None


def test_unsupported_temperature_omitted_and_limit_validated(database_path, saved_project):
    catalog = parse_catalog({'data': [model_entry(supported_parameters=['max_tokens'], top_provider={'max_completion_tokens': 500})]})
    plan = prepare_generation(database_path, saved_project.id, catalog, ['author/model'], max_tokens=400)
    assert 'temperature' not in plan.parameters['requests']['author/model']
    with pytest.raises(ValueError, match='reduce maximum'):
        prepare_generation(database_path, saved_project.id, catalog, ['author/model'])


def test_validation_happens_before_any_run(database_path, saved_project):
    catalog = parse_catalog({'data': [model_entry()]})
    for ids in ([], ['missing'], ['author/model', 'author/model']):
        with pytest.raises(ValueError):
            prepare_generation(database_path, saved_project.id, catalog, ids)
    with pytest.raises(ValueError, match='stale'):
        prepare_generation(database_path, saved_project.id, replace(catalog, fetched_at=catalog.fetched_at - timedelta(hours=1)), ['author/model'])
    plan = prepare_generation(database_path, saved_project.id, catalog, ['author/model'])
    with pytest.raises(ValueError, match='OPENROUTER_API_KEY'):
        generate(database_path, plan, OpenRouter())
    with connect(database_path) as connection:
        assert connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 0


def test_incomplete_persisted_input_rejected(database_path):
    project = create_project(database_path, 'Empty')
    catalog = parse_catalog({'data': [model_entry()]})
    with pytest.raises(ValueError, match='source'):
        prepare_generation(database_path, project.id, catalog, ['author/model'])
    add_text_source(database_path, project.id, 'Source')
    with pytest.raises(ValueError, match='task'):
        prepare_generation(database_path, project.id, catalog, ['author/model'])
    with connect(database_path) as connection:
        connection.execute('UPDATE projects SET task = ? WHERE id = ?', ('Task', project.id))
    with pytest.raises(ValueError, match='custom'):
        prepare_generation(database_path, project.id, catalog, ['author/model'])


@pytest.mark.parametrize('status,expected', [(500, 'interrupted'), (400, 'failed')])
def test_all_failed_run(database_path, saved_project, mock_async_http, status, expected):
    plan = prepare_generation(database_path, saved_project.id, parse_catalog({'data': [model_entry()]}), ['author/model'])
    mock_async_http(lambda request: httpx.Response(status))
    run_id = generate(database_path, plan, OpenRouter(SecretStr('fake')))
    assert get_generation_run(database_path, run_id).status == expected
    assert list_run_requests(database_path, run_id)[0].response is None


def test_interrupted_dispatch_persists_and_does_not_resume(database_path, saved_project, monkeypatch):
    plan = prepare_generation(database_path, saved_project.id, parse_catalog({'data': [model_entry()]}), ['author/model'])
    run_id = create_generation_run(database_path, plan.snapshot, plan.parameters, PROMPT_VERSION, None)
    def fail(**kwargs):
        raise RuntimeError('client setup failed')
    monkeypatch.setattr('app.generation.httpx.AsyncClient', fail)
    with pytest.raises(RuntimeError):
        asyncio.run(execute_run(database_path, run_id, OpenRouter(SecretStr('fake'))))
    assert get_generation_run(database_path, run_id).status == 'interrupted'
    assert list_run_requests(database_path, run_id)[0].status == 'interrupted'
    assert not asyncio.run(execute_run(database_path, run_id, OpenRouter(SecretStr('fake'))))


def test_missing_request_fee_does_not_imply_zero_total():
    model = parse_catalog({'data': [model_entry(pricing={'prompt': '0', 'completion': '0'})]}).models[0]
    estimate = estimate_cost([model], [], 100)
    assert estimate.token_cost_usd == 0
    assert estimate.cost_usd is None


def test_free_generation_preserves_reported_zero_cost(database_path, saved_project, mock_async_http):
    catalog = parse_catalog({'data': [model_entry('author/free-today', '0', '0')]})
    plan = prepare_generation(database_path, saved_project.id, catalog, ['author/free-today'])
    assert plan.estimate.cost_usd == 0
    mock_async_http(lambda request: httpx.Response(200, json=completion_payload(
        model='author/free-today', usage={'prompt_tokens': 40, 'completion_tokens': 20, 'cost': 0},
    )))
    run_id = generate(database_path, plan, OpenRouter(SecretStr('fake')))
    assert get_generation_run(database_path, run_id).estimated_cost_usd == 0
    request = list_run_requests(database_path, run_id)[0]
    assert request.cost_usd == 0
    assert request.input_tokens == 40 and request.output_tokens == 20
    assert request.response.origin == 'api'


def test_concurrent_dispatch_claims_run_once(database_path, saved_project, mock_async_http):
    plan = prepare_generation(database_path, saved_project.id, parse_catalog({'data': [model_entry()]}), ['author/model'])
    run_id = create_generation_run(database_path, plan.snapshot, plan.parameters, PROMPT_VERSION, None)
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0.01)
        return httpx.Response(200, json=completion_payload())
    mock_async_http(handler)
    async def run():
        gateway = OpenRouter(SecretStr('fake'))
        return await asyncio.gather(execute_run(database_path, run_id, gateway), execute_run(database_path, run_id, gateway))
    assert sorted(asyncio.run(run())) == [False, True]
    assert len(calls) == 1
    assert get_generation_run(database_path, run_id).status == 'succeeded'
