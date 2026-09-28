import asyncio
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.db import (
    add_text_source, connect, create_project, get_project, get_run, list_optional_runs,
    list_run_requests, save_project_inputs,
)
from app.generation import execute_run, generate
from app.openrouter import OpenRouter, parse_catalog
from app.optional_compute import draft_inputs, prepare_optional_operation
from app.optional_prompts import Critique, validate_structured_result
from app.review import adopt_candidate, get_primary_response, list_versions, save_review_version, select_primary_response
from test_openrouter import completion_payload, model_entry
from test_review import counts, seed_run


@pytest.fixture
def optional_project(database_path):
    project = create_project(database_path, 'Optional compute')
    source = add_text_source(database_path, project.id, 'The launch was in 2024. The budget was 10 units.', 'Original')
    save_project_inputs(database_path, project.id, 'Explain the launch.', 'Custom', 'A concise memo')
    run_id = seed_run(database_path, project.id, 3, failed=True)
    responses = [request.response for request in list_run_requests(database_path, run_id) if request.response]
    select_primary_response(database_path, project.id, responses[0].id)
    version_id = save_review_version(database_path, project.id, responses[0].id, 'The launch was in 2024.')
    return project, source, run_id, responses, version_id


@pytest.fixture
def catalog():
    return parse_catalog({'data': [model_entry('author/free-today', '0', '0'), model_entry('author/unknown', None, None)]})


@pytest.fixture
def mocked_optional_http(monkeypatch):
    real_client = httpx.AsyncClient
    calls = []
    def install(handler):
        async def record(request):
            calls.append(json.loads(request.content))
            return handler(request)
        monkeypatch.setattr('app.generation.httpx.AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(record), **kwargs))
        return calls
    return install


def critique_result():
    return {field: ['Qualitative feedback'] for field in Critique.model_fields}


def fact_result(source_id):
    return {'claims': [
        {'claim': 'Launch was in 2024.', 'classification': 'SUPPORTED', 'explanation': 'Stated in the source.',
         'references': [{'source_id': source_id, 'excerpt': 'The launch was in 2024.'}]},
        {'claim': 'The team has ten people.', 'classification': 'UNSUPPORTED', 'explanation': 'Not substantiated by supplied sources; not a finding of falsity.', 'references': []},
        {'claim': 'The budget covered the launch.', 'classification': 'UNCLEAR', 'explanation': 'The budget scope is not given.', 'references': []},
    ]}


@pytest.mark.parametrize('operation', ['rewrite', 'merge', 'critique', 'fact_check'])
def test_optional_operation_persistence_and_no_automatic_adoption(database_path, optional_project, catalog, mocked_optional_http, operation):
    project, source, parent_run, responses, version_id = optional_project
    refs = [('response', response.id) for response in responses] if operation == 'merge' else [('version', version_id)]
    output = (json.dumps(critique_result()) if operation == 'critique' else
              json.dumps(fact_result(source.id)) if operation == 'fact_check' else 'Candidate derivative')
    calls = mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload(
        model='author/free-today', choices=[{'message': {'content': output}, 'finish_reason': 'stop'}], usage=None)))
    before = counts(database_path)
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', operation, refs, 'Keep it concise.')
    assert calls == []
    assert counts(database_path) == before
    assert plan.estimate.cost_usd == 0
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    assert len(calls) == 1
    assert calls[0]['model'] == 'author/free-today'
    assert 'tools' not in calls[0] and 'plugins' not in calls[0]
    run = get_run(database_path, run_id)
    assert run.operation == operation and run.status == 'succeeded'
    assert run.parent_run_id == parent_run
    snapshot = json.loads(run.input_snapshot_json)
    assert snapshot['operation_instructions'] == 'Keep it concise.'
    assert snapshot['task'] == 'Explain the launch.'
    assert snapshot['custom_output_format'] == 'A concise memo'
    assert [(item['kind'], item['id']) for item in snapshot['selected_inputs']] == refs
    assert snapshot['sources'][0]['content'] == source.content
    assert 'model agreement' not in json.dumps(snapshot['selected_inputs'])
    result = list_run_requests(database_path, run_id)[0]
    assert result.status == 'succeeded'
    assert result.input_tokens is result.output_tokens is result.cost_usd is None
    assert result.latency_ms is not None
    assert result.provider_request_id == 'gen-example'
    assert result.response.text == output
    assert result.response.kind == (operation if operation in {'critique', 'fact_check'} else 'draft')
    assert result.response.origin == 'api'
    assert result.response.verified_provider == 'openrouter'
    assert json.loads(result.response.generation_metadata_json)['upstream_provider'] == 'Upstream host'
    if operation in {'critique', 'fact_check'}:
        assert json.loads(result.response.structured_result_json) == json.loads(output)
    assert get_project(database_path, project.id).current_version_id == version_id
    assert get_primary_response(database_path, project.id).id == responses[0].id
    assert len(list_versions(database_path, project.id)) == 1
    assert list_run_requests(database_path, parent_run)[0].response.text == responses[0].text
    assert not asyncio.run(execute_run(database_path, run_id, OpenRouter(SecretStr('test-key'))))
    assert len(calls) == 1
    assert list_optional_runs(database_path, project.id)[0].id == run_id


def test_working_text_exact_snapshot_and_original_source_grounding(database_path, optional_project, catalog):
    project, source, _, _, version_id = optional_project
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'fact_check',
                                      [('version', version_id)], working_text='Unsaved working claim.')
    user = json.loads(plan.snapshot['messages'][1]['content'])
    assert user['selected_inputs'][0]['text'] == 'Unsaved working claim.'
    assert user['selected_inputs'][0]['input_mode'] == 'working_copy'
    assert user['sources'][0]['content'] == source.content
    assert len(user['selected_inputs']) == 1
    prompt = plan.snapshot['messages'][0]['content']
    assert 'SOURCE VERIFICATION ONLY' in prompt
    assert 'does NOT mean false' in prompt
    assert 'Other model responses are never evidence' in prompt
    add_text_source(database_path, project.id, 'A later source')
    assert len(plan.snapshot['sources']) == 1


@pytest.mark.parametrize('refs', [[], [('response', 1)], [('response', 1), ('response', 1)]])
def test_merge_requires_two_distinct_explicit_inputs(database_path, optional_project, catalog, refs):
    project = optional_project[0]
    with pytest.raises(ValueError):
        prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'merge', refs)
    assert list_optional_runs(database_path, project.id) == []


def test_unknown_cost_unavailable_model_stale_catalog_and_input_isolation(database_path, optional_project, catalog):
    project, _, _, responses, version_id = optional_project
    refs = [('version', version_id)]
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/unknown', 'rewrite', refs)
    assert plan.estimate.cost_usd is None
    with pytest.raises(ValueError, match='no longer'):
        prepare_optional_operation(database_path, project.id, catalog, 'missing', 'rewrite', refs)
    with pytest.raises(ValueError, match='stale'):
        prepare_optional_operation(database_path, project.id, replace(catalog, fetched_at=catalog.fetched_at - timedelta(hours=1)), 'author/free-today', 'rewrite', refs)
    second = create_project(database_path, 'Other')
    with pytest.raises(ValueError, match='unavailable'):
        prepare_optional_operation(database_path, second.id, catalog, 'author/free-today', 'rewrite', refs)
    with pytest.raises(ValueError, match='current saved'):
        prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'rewrite', [('response', responses[1].id)])
    with pytest.raises(ValueError, match='primary'):
        prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'rewrite', [('response', responses[1].id)], working_text='Unrelated')


@pytest.mark.parametrize('failure', ['timeout', 'http'])
def test_optional_failure_no_retry(database_path, optional_project, catalog, mocked_optional_http, failure):
    project, _, _, _, version_id = optional_project
    def handler(request):
        if failure == 'timeout':
            raise httpx.ReadTimeout('timeout')
        return httpx.Response(402)
    calls = mocked_optional_http(handler)
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'rewrite', [('version', version_id)])
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    assert len(calls) == 1
    assert get_run(database_path, run_id).status == ('interrupted' if failure == 'timeout' else 'failed')
    result = list_run_requests(database_path, run_id)[0]
    assert result.error_message and result.response is None
    assert result.cost_usd is None
    assert get_project(database_path, project.id).current_version_id == version_id


@pytest.mark.parametrize('operation,raw', [('critique', 'not json'), ('critique', '{"score": 5}'), ('fact_check', '{"claims": [{"classification": "FALSE"}]}')])
def test_invalid_structure_persisted_raw_without_repair(database_path, optional_project, catalog, mocked_optional_http, operation, raw):
    project, _, _, _, version_id = optional_project
    calls = mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload(
        choices=[{'message': {'content': raw}, 'finish_reason': 'stop'}])))
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', operation, [('version', version_id)])
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    result = list_run_requests(database_path, run_id)[0]
    assert len(calls) == 1
    assert get_run(database_path, run_id).status == 'failed'
    assert result.status == 'failed'
    assert 'Invalid structured output' in result.error_message
    assert result.response.text == raw
    assert result.response.structured_result_json is None
    assert result.cost_usd == 0.001
    assert get_project(database_path, project.id).current_version_id == version_id


def test_fact_check_reference_validation_and_classifications():
    snapshot = {'sources': [{'id': 7, 'content': 'The launch was in 2024.'}]}
    valid = fact_result(7)
    assert validate_structured_result('fact_check', json.dumps(valid), snapshot) == valid
    for classification in ['FALSE', 'TRUE', 'LIKELY']:
        bad = fact_result(7)
        bad['claims'][0]['classification'] = classification
        with pytest.raises(ValueError):
            validate_structured_result('fact_check', json.dumps(bad), snapshot)
    for refs in [[], [{'source_id': 8, 'excerpt': 'The launch was in 2024.'}], [{'source_id': 7, 'excerpt': 'Invented excerpt'}]]:
        bad = fact_result(7)
        bad['claims'][0]['references'] = refs
        with pytest.raises(ValueError):
            validate_structured_result('fact_check', json.dumps(bad), snapshot)


@pytest.mark.parametrize('operation', ['rewrite', 'merge'])
def test_explicit_adoption_reuses_versions_and_preserves_parents(database_path, optional_project, catalog, mocked_optional_http, operation):
    project, _, parent_run, responses, version_id = optional_project
    refs = [('version', version_id), ('response', responses[1].id)] if operation == 'merge' else [('version', version_id)]
    mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload()))
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', operation, refs)
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    candidate = list_run_requests(database_path, run_id)[0].response
    assert candidate.id in [item['id'] for item in draft_inputs(database_path, project.id) if item['kind'] == 'response']
    adopted = adopt_candidate(database_path, project.id, candidate.id, 'Human edited candidate')
    assert get_project(database_path, project.id).current_version_id == adopted
    assert get_primary_response(database_path, project.id).id == candidate.id
    version = list_versions(database_path, project.id)[0]
    assert version.derived_from_response_id == candidate.id
    assert version.derived_from_version_id == (version_id if operation == 'rewrite' else None)
    assert version.operation == operation
    assert version.text == 'Human edited candidate'
    before = counts(database_path)
    assert adopt_candidate(database_path, project.id, candidate.id, 'Human edited candidate') == adopted
    assert counts(database_path) == before
    assert list_run_requests(database_path, parent_run)[0].response.text == responses[0].text
    assert list_run_requests(database_path, run_id)[0].response.text == candidate.text
    saved_edit = save_review_version(database_path, project.id, candidate.id, 'Later manual edit', adopted)
    assert get_project(database_path, project.id).current_version_id == saved_edit
    with connect(database_path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_fact_check_needs_original_sources_and_never_uses_feedback_as_merge_input(database_path, optional_project, catalog, mocked_optional_http):
    project, _, _, _, version_id = optional_project
    mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload(
        choices=[{'message': {'content': json.dumps(critique_result())}, 'finish_reason': 'stop'}])))
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'critique', [('version', version_id)])
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    feedback = list_run_requests(database_path, run_id)[0].response
    with pytest.raises(ValueError, match='unavailable'):
        prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'merge',
                                   [('version', version_id), ('response', feedback.id)])
    with pytest.raises(ValueError):
        adopt_candidate(database_path, project.id, feedback.id, feedback.text)
    with connect(database_path) as connection:
        connection.execute('DELETE FROM sources WHERE project_id = ?', (project.id,))
    with pytest.raises(ValueError, match='original project sources'):
        prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'fact_check', [('version', version_id)])


def test_merge_multiple_parent_runs_preserves_all_provenance(database_path, optional_project, catalog, mocked_optional_http):
    project, _, first_run, responses, _ = optional_project
    second_run = seed_run(database_path, project.id, 1)
    other = list_run_requests(database_path, second_run)[0].response
    refs = [('response', responses[0].id), ('response', other.id)]
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'merge', refs)
    assert plan.parent_run_id is None
    assert {item['run_id'] for item in plan.snapshot['selected_inputs']} == {first_run, second_run}
    assert [(item['kind'], item['id']) for item in plan.snapshot['selected_inputs']] == refs
    mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload()))
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    run = get_run(database_path, run_id)
    assert run.parent_run_id is None
    assert {item['run_id'] for item in json.loads(run.input_snapshot_json)['selected_inputs']} == {first_run, second_run}


@pytest.mark.parametrize('capabilities,supported', [
    (['structured_outputs', 'response_format'], True),
    (['structured_outputs'], True), (['response_format'], False), ([], False), (None, False),
])
def test_fact_check_capability_payload(database_path, optional_project, mocked_optional_http, capabilities, supported):
    from app.optional_prompts import FactCheck

    project, source, _, _, version = optional_project
    catalog = parse_catalog({'data': [model_entry('author/model', '0', '0', supported_parameters=capabilities)]})
    calls = mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload(
        choices=[{'message': {'content': json.dumps(fact_result(source.id))}, 'finish_reason': 'stop'}])))
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/model', 'fact_check', [('version', version)])
    assert plan.parameters['fact_check_output_mode'] == ('json_schema' if supported else 'prompt_only')
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    assert len(calls) == 1
    assert calls[0]['provider'] == {'allow_fallbacks': False, 'require_parameters': True}
    if supported:
        assert calls[0]['response_format'] == {
            'type': 'json_schema', 'json_schema': {'name': 'fact_check', 'strict': True, 'schema': FactCheck.model_json_schema()}}
        schema = calls[0]['response_format']['json_schema']['schema']
        assert schema['required'] == ['claims']
        assert schema['$defs']['Claim']['properties']['classification']['enum'] == ['SUPPORTED', 'UNSUPPORTED', 'UNCLEAR']
        assert set(schema['$defs']['Claim']['required']) == {'claim', 'classification', 'explanation', 'references'}
        assert set(schema['$defs']['SourceReference']['required']) == {'source_id', 'excerpt'}
        assert all(item['additionalProperties'] is False for item in [schema, *schema['$defs'].values()])
    else:
        assert 'response_format' not in calls[0]
    assert list_run_requests(database_path, run_id)[0].status == 'succeeded'
    assert json.loads(get_run(database_path, run_id).parameters_json) == json.loads(json.dumps(plan.parameters))
    prompt = calls[0]['messages'][0]['content']
    assert 'SUPPORTED|UNSUPPORTED|UNCLEAR' not in prompt
    assert 'short, verbatim, contiguous' in prompt
    assert 'separate references for separate source passages' in prompt


@pytest.mark.parametrize('case', ['valid', 'malformed', 'source_id', 'stitched', 'enum', 'provider_rejection'])
def test_schema_output_still_validated_locally_without_retry(database_path, optional_project, mocked_optional_http, monkeypatch, case):
    project, source, _, responses, version = optional_project
    catalog = parse_catalog({'data': [model_entry('author/model', '0', '0', supported_parameters=['structured_outputs'])]})
    output = fact_result(source.id)
    claim = output['claims'][0]
    if case == 'source_id':
        claim['references'][0]['source_id'] = source.id + 999
    elif case == 'stitched':
        claim['references'][0]['excerpt'] = 'The launch was in 2024. 10 units.'
    elif case == 'enum':
        claim['classification'] = 'SUPPORTED|UNSUPPORTED|UNCLEAR'
    raw = 'not JSON' if case == 'malformed' else json.dumps(output)
    calls = mocked_optional_http(lambda request: httpx.Response(400, json={'error': {'message': 'Schema unsupported'}})
                                if case == 'provider_rejection' else httpx.Response(200, json=completion_payload(
                                    choices=[{'message': {'content': raw}, 'finish_reason': 'stop'}])))
    validations = []
    def validate(operation, text, snapshot):
        validations.append(text)
        return validate_structured_result(operation, text, snapshot)
    monkeypatch.setattr('app.optional_prompts.validate_structured_result', validate)
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/model', 'fact_check', [('version', version)])
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    result = list_run_requests(database_path, run_id)[0]
    assert result.status == ('succeeded' if case == 'valid' else 'failed')
    assert validations == ([] if case == 'provider_rejection' else [raw])
    if case != 'provider_rejection':
        assert result.response.text == raw
        assert result.cost_usd == 0.001
        assert result.input_tokens == 40 and result.output_tokens == 20
        assert bool(result.response.structured_result_json) == (case == 'valid')
    assert get_project(database_path, project.id).current_version_id == version
    assert get_primary_response(database_path, project.id).id == responses[0].id
    assert len(list_versions(database_path, project.id)) == 1
    assert not asyncio.run(execute_run(database_path, run_id, OpenRouter(SecretStr('test-key'))))
    assert len(calls) == 1
    assert 'response_format' in calls[0]


@pytest.mark.parametrize('operation', ['rewrite', 'merge', 'critique'])
def test_schema_capability_does_not_change_other_operations(database_path, optional_project, operation):
    project, _, _, responses, version = optional_project
    catalog = parse_catalog({'data': [model_entry('author/model', '0', '0', supported_parameters=['structured_outputs'])]})
    refs = [('response', response.id) for response in responses] if operation == 'merge' else [('version', version)]
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/model', operation, refs)
    assert 'response_format' not in plan.parameters['requests']['author/model']
    assert 'fact_check_output_mode' not in plan.parameters
    assert plan.prompt_version == f'optional-compute-v1:{operation}'


@pytest.mark.parametrize('operation,default', [('fact_check', 4096), ('rewrite', 1024), ('merge', 1024), ('critique', 1024)])
def test_operation_token_defaults_and_override(database_path, optional_project, catalog, operation, default):
    project, _, _, responses, version = optional_project
    refs = [('response', response.id) for response in responses] if operation == 'merge' else [('version', version)]
    for override in (None, 2048):
        plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', operation, refs, max_tokens=override)
        expected = default if override is None else override
        assert plan.parameters['requests']['author/free-today']['max_tokens'] == expected
        assert plan.estimate.max_output_tokens == expected


def test_fact_check_default_respects_catalog_limit(database_path, optional_project, catalog):
    project, _, _, _, version = optional_project
    limited = replace(catalog, models=(replace(catalog.models[0], max_completion_tokens=2048),))
    with pytest.raises(ValueError, match='reduce maximum output tokens to 2048'):
        prepare_optional_operation(database_path, project.id, limited, 'author/free-today', 'fact_check', [('version', version)])
    plan = prepare_optional_operation(database_path, project.id, limited, 'author/free-today', 'fact_check', [('version', version)], max_tokens=2048)
    assert plan.parameters['max_tokens'] == 2048


@pytest.mark.parametrize('source,excerpt,expected', [
    ('Before. Exact passage. After.', 'Exact passage.', 'Exact passage.'),
    ('First\nsecond', 'First second', 'First\nsecond'),
    ('First  \t second', 'First second', 'First  \t second'),
    ('First second', 'First\t\nsecond', 'First second'),
    ('First, second.', 'First second.', None),
    ('First second', 'First third', None),
    ('First second', 'First extra second', None),
    ('First important second', 'First second', None),
    ('First passage. Intervening text. Last passage.', 'First passage. Last passage.', None),
    ('First second', 'first second', None),
    ('café here', 'cafe\u0301 here', None),
    ('first\u00a0second', 'first second', None),
    ('First second', 'Firstsecond', None),
    ('Firstsecond', 'First second', None),
    ('a.*\nb', 'a.* b', 'a.*\nb'),
    ('aXYZ\nb', 'a.* b', None),
    ('First\nsecond; First second', 'First second', 'First second'),
    ('anything', '\n\t ', None),
    ('Python is powerful... and fast;\nplays well with others;\nruns everywhere;\nis friendly & easy to learn;\nis Open.',
     'Python is powerful... and fast; plays well with others; runs everywhere; is friendly & easy to learn; is Open.',
     'Python is powerful... and fast;\nplays well with others;\nruns everywhere;\nis friendly & easy to learn;\nis Open.'),
    ('2026-09-17\nAnnouncing the 2026 PSF Board Election Results!',
     '2026-09-17 Announcing the 2026 PSF Board Election Results!',
     '2026-09-17\nAnnouncing the 2026 PSF Board Election Results!'),
])
def test_whitespace_only_reference_resolution(source, excerpt, expected):
    snapshot = {'sources': [{'id': 7, 'content': source}]}
    raw = fact_result(7)
    raw['claims'][0]['references'][0]['excerpt'] = excerpt
    if expected is None:
        with pytest.raises(ValueError, match='source/excerpt'):
            validate_structured_result('fact_check', json.dumps(raw), snapshot)
    else:
        validated = validate_structured_result('fact_check', json.dumps(raw), snapshot)
        assert validated['claims'][0]['references'][0]['excerpt'] == expected
        assert expected in source
    assert snapshot['sources'][0]['content'] == source
    assert raw['claims'][0]['references'][0]['excerpt'] == excerpt


def test_whitespace_match_cannot_use_another_source():
    raw = fact_result(8)
    raw['claims'][0]['references'][0]['excerpt'] = 'First second'
    for sources in ([{'id': 7, 'content': 'First\nsecond'}],
                    [{'id': 7, 'content': 'First\nsecond'}, {'id': 8, 'content': 'Other text'}]):
        with pytest.raises(ValueError, match='source/excerpt'):
            validate_structured_result('fact_check', json.dumps(raw), {'sources': sources})


def test_original_evidence_persisted_separately_from_raw_output(database_path, optional_project, catalog, mocked_optional_http):
    project, _, _, _, version = optional_project
    source = add_text_source(database_path, project.id, 'First\n\tsecond', 'Whitespace source')
    output = fact_result(source.id)
    output['claims'][0]['references'][0]['excerpt'] = 'First second'
    raw = json.dumps(output)
    calls = mocked_optional_http(lambda request: httpx.Response(200, json=completion_payload(
        choices=[{'message': {'content': raw}, 'finish_reason': 'stop'}])))
    plan = prepare_optional_operation(database_path, project.id, catalog, 'author/free-today', 'fact_check', [('version', version)])
    snapshot_before = json.dumps(plan.snapshot)
    run_id = generate(database_path, plan, OpenRouter(SecretStr('test-key')))
    result = list_run_requests(database_path, run_id)[0]
    assert result.status == 'succeeded'
    assert result.response.text == raw
    validated = json.loads(result.response.structured_result_json)
    assert validated['claims'][0]['references'][0]['excerpt'] == source.content
    assert json.loads(get_run(database_path, run_id).input_snapshot_json) == json.loads(snapshot_before)
    assert len(calls) == 1
