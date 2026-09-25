"""Compact model selection, explicit generation, and persisted result inspection."""

import json
from decimal import Decimal
from pathlib import Path

import streamlit as st
from pydantic import SecretStr

from app.db import list_generation_runs, list_run_requests
from app.generation import generate, prepare_generation
from app.openrouter import CATALOG_TTL_SECONDS, Catalog, CatalogModel, OpenRouter, OpenRouterError, price


@st.cache_data(ttl=CATALOG_TTL_SECONDS, show_spinner=False)
def cached_catalog() -> Catalog:
    return OpenRouter().catalog()


def money(value: Decimal | float | None) -> str:
    if value is None:
        return 'unknown'
    if value == 0:
        return '$0'
    return f'${value:.8f}'.rstrip('0').rstrip('.') if value >= Decimal('0.00000001') else '<$0.00000001'


def model_label(model: CatalogModel) -> str:
    cost = 'free tokens' if model.free_tokens else 'paid tokens' if (
        price(model.pricing.get('prompt')) is not None and price(model.pricing.get('completion')) is not None
    ) else 'pricing unknown'
    return f'{model.name} · {model.id} · {cost}'


def render_generation(database_path: Path, project_id: int, api_key: SecretStr | None) -> None:
    st.markdown('<div class="stage-arrow" aria-hidden="true">↓</div>', unsafe_allow_html=True)
    st.markdown('### MODEL SELECTION → GENERATE')
    st.caption('Select the models yourself. Generate sends saved sources, task, and output format to OpenRouter. Unsaved edits are excluded.')
    if api_key is None:
        st.caption('OPENROUTER_API_KEY is not configured. Add it to your environment or .env to generate; local inputs and saved results remain available.')
    if st.button('Load / refresh model catalog', key=f'catalog_refresh_{project_id}'):
        cached_catalog.clear()
        st.session_state['catalog_loaded'] = True
    if st.session_state.get('catalog_loaded'):
        try:
            with st.spinner('Loading model catalog…'):
                catalog = cached_catalog()
        except OpenRouterError as exc:
            st.error(str(exc))
            st.session_state['catalog_loaded'] = False
        else:
            render_selector(database_path, project_id, api_key, catalog)
    else:
        st.caption('Load the public catalog to see available models and current prices. This does not generate model output.')
    render_results(database_path, project_id)


def render_selector(database_path: Path, project_id: int, api_key: SecretStr | None, catalog: Catalog) -> None:
    st.caption(f'Catalog fetched {catalog.fetched_at:%Y-%m-%d %H:%M:%S UTC} · cached up to 15 minutes · text-capable models only.')
    free_only = st.checkbox('Free token pricing only', key=f'free_models_{project_id}')
    available = {model.id: model for model in catalog.models if not free_only or model.free_tokens}
    if not available:
        st.caption('No matching models in this catalog. Change the filter or refresh the catalog.')
    selection_key = f'models_{project_id}'
    previous = st.session_state.get(selection_key, [])
    if any(model_id not in available for model_id in previous):
        st.session_state[selection_key] = [model_id for model_id in previous if model_id in available]
        st.caption('Models outside the current catalog/filter were removed from selection. No replacements were selected.')
    selected = st.multiselect('Models', list(available), format_func=lambda model_id: model_label(available[model_id]),
                              key=selection_key, placeholder='Choose one or more models')
    with st.expander('Model details and generation parameters'):
        left, right = st.columns(2)
        max_tokens = left.number_input('Maximum output tokens per model', min_value=1, max_value=32768,
                                       value=1024, step=128, key=f'max_tokens_{project_id}')
        temperature = right.slider('Temperature', min_value=0.0, max_value=2.0, value=0.7,
                                   step=0.1, key=f'temperature_{project_id}')
        st.caption('Temperature is sent only when supported or when support is unspecified. The output limit is sent to every model.')
        for model_id in selected:
            model = available[model_id]
            prompt = price(model.pricing.get('prompt'))
            completion = price(model.pricing.get('completion'))
            st.write(model_label(model))
            st.caption(f'Context: {model.context_length if model.context_length is not None else "unknown"} tokens · '
                       f'Input / 1M tokens: {money(prompt * 1_000_000 if prompt is not None else None)} · '
                       f'Output / 1M tokens: {money(completion * 1_000_000 if completion is not None else None)}')
            st.caption(f'Catalog namespace: {model.namespace or "unknown"} · '
                       f'Supported parameters: {", ".join(model.supported_parameters) if model.supported_parameters else "not reported"}')
    plan = None
    if selected:
        try:
            plan = prepare_generation(database_path, project_id, catalog, selected, int(max_tokens), temperature)
        except ValueError as exc:
            st.caption(str(exc))
    st.write(f'{len(selected)} models · {len(selected)} API calls per Generate')
    if plan is not None:
        estimate = plan.estimate
        if estimate.token_cost_usd == 0:
            st.write('Estimated token cost: $0')
        st.write(f'Estimated cost: {money(estimate.cost_usd)}')
        st.caption(f'Estimate uses ~{estimate.input_tokens:,} input tokens per call '
                   f'(UTF-8 message bytes ÷ 4 plus message overhead), up to {max_tokens:,} output tokens per model, '
                   'and catalog rates plus any request fee. This is not a spending cap; tokenization, routing, reasoning, '
                   'caching, other fees, and price changes can affect actual cost. Free models can be rate limited.')
        for model_id in selected:
            context = available[model_id].context_length
            if context and estimate.input_tokens + max_tokens > context:
                st.warning(f'{model_id}: estimated input plus output exceeds its context length. Shorten the saved input or lower the output limit; sources will not be silently truncated.')
        with st.expander('Saved input preview'):
            st.json(plan.snapshot)
    else:
        st.caption('Estimated cost: unknown — select models and save complete inputs first.')
    if st.button('Generate', type='primary', disabled=api_key is None or plan is None, key=f'generate_{project_id}'):
        try:
            with st.spinner('Generating independently through OpenRouter…'):
                run_id = generate(database_path, plan, OpenRouter(api_key))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[f'run_{project_id}'] = run_id
            st.caption(f'Run #{run_id} saved. Outcomes are shown below; no requests were automatically retried.')


def render_results(database_path: Path, project_id: int) -> None:
    runs = list_generation_runs(database_path, project_id)
    if not runs:
        return
    st.markdown('### MODEL RESPONSES')
    by_id = {run.id: run for run in runs}
    key = f'run_{project_id}'
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = runs[0].id
    run_id = st.selectbox('Generation run', list(by_id), key=key,
                          format_func=lambda value: f'Run #{value} · {by_id[value].status} · {by_id[value].created_at:%Y-%m-%d %H:%M UTC}')
    run = by_id[run_id]
    if run.status in {'pending', 'running'}:
        st.warning('This run is unfinished or still running. If the app stopped, completion and billing may be uncertain. It will not be resumed or retried automatically.')
    with st.expander('Run inputs, parameters, and estimate'):
        st.caption(f'Prompt version: {run.prompt_version} · Estimated cost: {money(run.estimated_cost_usd)}')
        st.json(json.loads(run.input_snapshot_json))
        st.json(json.loads(run.parameters_json))
    requests = list_run_requests(database_path, run_id)
    for offset in range(0, len(requests), 2):
        group = requests[offset:offset + 2]
        for column, request in zip(st.columns(len(group)), group):
            with column:
                st.markdown('---')
                st.write(request.requested_model)
                status = 'uncertain / interrupted' if request.status == 'interrupted' else request.status
                st.caption(f'Gateway: OpenRouter · Request #{request.id} · {status}')
                if request.error_message:
                    st.error(request.error_message)
                st.caption(f'Input tokens: {request.input_tokens if request.input_tokens is not None else "unknown"} · '
                           f'Output tokens: {request.output_tokens if request.output_tokens is not None else "unknown"} · '
                           f'Reported cost: {money(request.cost_usd)} · '
                           f'Latency: {str(request.latency_ms) + " ms" if request.latency_ms is not None else "unknown"}')
                if request.response:
                    response = request.response
                    metadata = json.loads(response.generation_metadata_json)
                    st.caption(f'Origin: API · Reported model: {response.verified_model or "unknown"} · '
                               f'Upstream provider: {metadata.get("upstream_provider") or "not reported"}')
                    st.caption(f'OpenRouter request ID: {request.provider_request_id or "not reported"} · '
                               f'Finish reason: {response.finish_reason or "not reported"} · '
                               f'Saved {response.created_at:%Y-%m-%d %H:%M:%S UTC}')
                    st.text(response.text)
