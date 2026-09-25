import asyncio
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from app.openrouter import OpenRouter, OpenRouterError, normalize_completion, parse_catalog, price


def model_entry(model_id='author/model', prompt='0.000001', completion='0.000002', **extra):
    return {'id': model_id, 'name': model_id, 'context_length': 32000,
            'architecture': {'input_modalities': ['text'], 'output_modalities': ['text']},
            'pricing': {'prompt': prompt, 'completion': completion, 'request': '0'},
            'supported_parameters': ['temperature', 'max_tokens'], **extra}


def completion_payload(**extra):
    return {'id': 'gen-example', 'model': 'author/model', 'provider': 'Upstream host',
            'created': 1234567890,
            'choices': [{'message': {'content': 'Generated text'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 40, 'completion_tokens': 20, 'cost': 0.001}, **extra}


@pytest.mark.parametrize('value', [None, '', 'not-a-price', '-1', 'NaN', 'Infinity', True])
def test_unknown_prices(value):
    assert price(value) is None


def test_catalog_prices_free_models_and_metadata():
    catalog = parse_catalog({'data': [
        model_entry('author/free-today', '0', '0'),
        model_entry('author/paid:free', '0.000001', '0.000002'),
        model_entry('author/unknown', None, None),
        model_entry('author/missing', pricing={}),
        model_entry('author/invalid', '-1', 'NaN'),
        model_entry('author/image', architecture={'input_modalities': ['text'], 'output_modalities': ['image']}),
        model_entry('openrouter/auto'),
        model_entry('author/model:online'),
        {'name': 'no id'}, None,
    ]})
    models = {model.id: model for model in catalog.models}
    assert len(models) == 5
    assert models['author/free-today'].free_tokens
    assert not models['author/paid:free'].free_tokens
    assert not models['author/unknown'].free_tokens
    assert not models['author/missing'].free_tokens
    assert not models['author/invalid'].free_tokens
    assert price(models['author/paid:free'].pricing['prompt']) == Decimal('0.000001')
    assert models['author/paid:free'].namespace == 'author'
    assert models['author/paid:free'].context_length == 32000
    assert models['author/paid:free'].supported_parameters == ('temperature', 'max_tokens')
    assert catalog.fetched_at.tzinfo is not None


@pytest.mark.parametrize('payload', [None, {}, {'data': {}}, {'data': 'invalid'}])
def test_invalid_catalog(payload):
    with pytest.raises(OpenRouterError):
        parse_catalog(payload)


def test_public_catalog_http_and_cache(monkeypatch):
    from app.generation_ui import cached_catalog

    calls = []
    def handler(request):
        calls.append(request)
        assert request.url == 'https://openrouter.ai/api/v1/models'
        assert 'authorization' not in request.headers
        return httpx.Response(200, json={'data': [model_entry()]})
    real_client = httpx.Client
    monkeypatch.setattr('app.openrouter.httpx.Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    cached_catalog.clear()
    try:
        first = cached_catalog()
        assert cached_catalog() == first
        assert len(calls) == 1
        cached_catalog.clear()
        assert cached_catalog().models == first.models
        assert len(calls) == 2
    finally:
        cached_catalog.clear()


@pytest.mark.parametrize('kind', ['timeout', 'http', 'malformed'])
def test_catalog_errors_are_safe_and_not_retried(monkeypatch, kind):
    calls = []
    def handler(request):
        calls.append(request)
        if kind == 'timeout':
            raise httpx.ReadTimeout('private detail')
        if kind == 'http':
            return httpx.Response(500, text='sensitive data')
        return httpx.Response(200, text='bad json')
    real_client = httpx.Client
    monkeypatch.setattr('app.openrouter.httpx.Client', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(OpenRouterError) as error:
        OpenRouter().catalog()
    assert 'private detail' not in str(error.value)
    assert 'sensitive' not in str(error.value)
    assert len(calls) == 1


def test_missing_key_never_sends():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail('Unexpected request'))) as client:
            for key in (None, SecretStr(' ')):
                with pytest.raises(OpenRouterError, match='OPENROUTER_API_KEY'):
                    await OpenRouter(key).complete(client, {})
    asyncio.run(run())


def test_success_normalization_and_request():
    def handler(request):
        assert request.headers['authorization'] == 'Bearer test-key'
        assert str(request.url) == 'https://openrouter.ai/api/v1/chat/completions'
        return httpx.Response(200, json=completion_payload())
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OpenRouter(SecretStr('test-key')).complete(client, {'model': 'author/model', 'messages': []})
    result = asyncio.run(run())
    assert result.text == 'Generated text'
    assert result.model == 'author/model'
    assert result.upstream_provider == 'Upstream host'
    assert result.request_id == 'gen-example'
    assert result.input_tokens == 40
    assert result.output_tokens == 20
    assert result.cost_usd == 0.001
    assert result.finish_reason == 'stop'
    assert result.latency_ms >= 0
    assert result.metadata['provider_created_at'] == 1234567890


@pytest.mark.parametrize('usage', [None, {}, {'prompt_tokens': -1, 'completion_tokens': True, 'cost': -1}])
def test_unknown_usage_not_fabricated(usage):
    result = normalize_completion(completion_payload(usage=usage, provider=None, model=None, id=None), 12)
    assert result.input_tokens is result.output_tokens is result.cost_usd is None
    assert result.upstream_provider is result.model is result.request_id is None
    assert result.text == 'Generated text'


def test_reported_zero_is_preserved():
    result = normalize_completion(completion_payload(usage={'prompt_tokens': 0, 'completion_tokens': 0, 'cost': 0}), 0)
    assert result.input_tokens == result.output_tokens == result.cost_usd == 0


@pytest.mark.parametrize('failure', ['timeout', 'connection', 'bad_json', 'missing_text', 'api_error', 401, 402, 403, 429, 500, 302])
def test_failure_no_retry_or_secret_disclosure(failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('test-key')
        if failure == 'connection':
            raise httpx.ConnectError('test-key')
        if failure == 'bad_json':
            return httpx.Response(200, text='test-key')
        if failure == 'missing_text':
            return httpx.Response(200, json=completion_payload(choices=[]))
        if failure == 'api_error':
            return httpx.Response(200, json={'error': {'message': 'test-key'}})
        return httpx.Response(failure, text='test-key')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await OpenRouter(SecretStr('test-key')).complete(client, {})
    result = asyncio.run(run())
    assert result.error
    assert result.text is None
    assert 'test-key' not in result.error
    assert 'retry' in result.error
    assert len(calls) == 1
    if failure in {'timeout', 'connection', 'bad_json', 'missing_text', 500}:
        assert result.uncertain
