"""OpenRouter's catalog and non-streaming chat API. No automatic retries."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any

import httpx
from pydantic import SecretStr

API_URL = 'https://openrouter.ai/api/v1'
CATALOG_TTL_SECONDS = 900


class OpenRouterError(ValueError):
    """Safe, actionable error text, without response bodies or credentials."""


def price(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def token_count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    context_length: int | None
    max_completion_tokens: int | None
    pricing: dict[str, str | None]
    supported_parameters: tuple[str, ...] | None

    @property
    def free_tokens(self) -> bool:
        return price(self.pricing.get('prompt')) == 0 and price(self.pricing.get('completion')) == 0

    @property
    def namespace(self) -> str | None:
        # A catalog namespace is a label, not verified authorship or hosting provenance.
        return self.id.split('/', 1)[0] if '/' in self.id else None


@dataclass(frozen=True)
class Catalog:
    models: tuple[CatalogModel, ...]
    fetched_at: datetime


def parse_catalog(payload: Any) -> Catalog:
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
        raise OpenRouterError('OpenRouter returned an invalid model catalog.')
    models = {}
    for item in payload['data']:
        if not isinstance(item, dict) or not optional_text(item.get('id')):
            continue
        architecture = item.get('architecture') or {}
        if not isinstance(architecture, dict):
            continue
        inputs = architecture.get('input_modalities') or ['text']
        outputs = architecture.get('output_modalities') or ['text']
        if not isinstance(inputs, list) or not isinstance(outputs, list) or 'text' not in inputs or 'text' not in outputs:
            continue
        model_id = item['id']
        # Routers do not represent a human-selected, specific model.
        if model_id.startswith('openrouter/') or model_id.endswith((':online', ':latest')):
            continue
        raw_prices = item.get('pricing')
        prices = raw_prices if isinstance(raw_prices, dict) else {}
        parameters = item.get('supported_parameters')
        top = item.get('top_provider') or {}
        top = top if isinstance(top, dict) else {}
        models[model_id] = CatalogModel(
            id=model_id, name=optional_text(item.get('name')) or model_id,
            context_length=token_count(item.get('context_length')),
            max_completion_tokens=token_count(top.get('max_completion_tokens')),
            pricing={key: str(value) if value is not None else None for key, value in prices.items()},
            supported_parameters=tuple(p for p in parameters if isinstance(p, str))
            if isinstance(parameters, list) else None,
        )
    return Catalog(tuple(sorted(models.values(), key=lambda model: model.name.casefold())), datetime.now(UTC))


@dataclass(frozen=True)
class Completion:
    text: str | None = None
    model: str | None = None
    request_id: str | None = None
    upstream_provider: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    uncertain: bool = False


def normalize_completion(data: Any, latency_ms: int) -> Completion:
    if not isinstance(data, dict):
        raise OpenRouterError('Invalid completion response; outcome and cost may be unknown. No retry was made.')
    usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
    cost = price(usage.get('cost'))
    common = dict(
        model=optional_text(data.get('model')), request_id=optional_text(data.get('id')),
        upstream_provider=optional_text(data.get('provider')),
        input_tokens=token_count(usage.get('prompt_tokens')),
        output_tokens=token_count(usage.get('completion_tokens')),
        cost_usd=float(cost) if cost is not None and cost < Decimal('1e100') else None,
        latency_ms=latency_ms,
        metadata={'usage': usage, 'provider_created_at': data.get('created'),
                  'system_fingerprint': data.get('system_fingerprint')},
    )
    if data.get('error'):
        return Completion(**common, error='OpenRouter reported an API error. No retry was made; billing may be unknown.')
    choices = data.get('choices')
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = first.get('message') if isinstance(first.get('message'), dict) else {}
    text = optional_text(message.get('content'))
    finish = optional_text(first.get('finish_reason'))
    if text is None or finish == 'error':
        return Completion(**common, finish_reason=finish, uncertain=True,
                          error='No usable text was returned. The request may have incurred usage; no retry was made.')
    return Completion(**common, text=text, finish_reason=finish)


class OpenRouter:
    """One gateway, no speculative provider interface or direct-provider adapters."""

    def __init__(self, api_key: SecretStr | None = None):
        self._api_key = api_key

    def require_key(self) -> None:
        if self._api_key is None or not self._api_key.get_secret_value().strip():
            raise OpenRouterError('Set OPENROUTER_API_KEY in your environment or .env to generate.')

    def catalog(self) -> Catalog:
        try:
            # Public catalog: no project content or API key is sent.
            with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
                response = client.get(f'{API_URL}/models')
                response.raise_for_status()
                return parse_catalog(response.json())
        except httpx.TimeoutException as exc:
            raise OpenRouterError('The model catalog timed out. Refresh it explicitly to try again.') from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise OpenRouterError('Could not load the OpenRouter model catalog. Refresh it to try again.') from exc

    async def complete(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> Completion:
        self.require_key()
        start = perf_counter()
        try:
            response = await client.post(
                f'{API_URL}/chat/completions', json=payload,
                headers={'Authorization': f'Bearer {self._api_key.get_secret_value()}'},
            )
            elapsed = round((perf_counter() - start) * 1000)
            if response.is_error or response.is_redirect:
                messages = {401: 'Check your OpenRouter API key.', 402: 'Check your OpenRouter credits.',
                            403: 'The request was not permitted.', 429: 'Rate limited; try again later explicitly.'}
                return Completion(latency_ms=elapsed, uncertain=response.status_code >= 500,
                                  error=f'OpenRouter HTTP {response.status_code}. '
                                        f'{messages.get(response.status_code, "Request failed; billing may be unknown.")} No retry was made.')
            return normalize_completion(response.json(), elapsed)
        except httpx.TimeoutException:
            message = 'Request timed out; completion and billing are uncertain. No retry was made.'
        except httpx.HTTPError:
            message = 'Connection failed; completion and billing are uncertain. No retry was made.'
        except (ValueError, TypeError):
            message = 'Invalid response; completion and billing are uncertain. No retry was made.'
        return Completion(latency_ms=round((perf_counter() - start) * 1000), error=message, uncertain=True)
