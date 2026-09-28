"""Explicit optional-compute controls; candidates never replace drafts automatically."""

import json
from pathlib import Path

import streamlit as st
from pydantic import SecretStr

from app.db import get_project, list_optional_runs, list_run_requests
from app.generation import generate
from app.generation_ui import cached_catalog, model_label, money
from app.openrouter import OpenRouter, OpenRouterError
from app.optional_compute import default_output_tokens, draft_inputs, prepare_optional_operation
from app.optional_prompts import OPERATIONS
from app.review import adopt_candidate, get_primary_response, list_versions
from app.review_ui import draft_state_key, render_response

LABELS = {'rewrite': 'Rewrite', 'merge': 'Merge', 'critique': 'Critique', 'fact_check': 'Fact check · sources only'}


def render_optional_compute(database_path: Path, project_id: int, api_key: SecretStr | None) -> None:
    st.markdown('### OPTIONAL COMPUTE')
    st.caption('Additional compute only when you ask. One request per operation; no chaining, automatic retries, or automatic adoption.')
    with st.expander('Choose an optional operation'):
        render_controls(database_path, project_id, api_key)
    render_optional_history(database_path, project_id)


def render_controls(database_path: Path, project_id: int, api_key: SecretStr | None) -> None:
    inputs = draft_inputs(database_path, project_id)
    if not inputs:
        st.caption('Generate and review a response first. No optional requests have been made.')
        return
    operation = st.selectbox('Optional operation', OPERATIONS, format_func=LABELS.get, key=f'optional_operation_{project_id}')
    input_refs = []
    working_text = None
    if operation == 'merge':
        choices = {(item['kind'], item['id']): item for item in inputs}
        input_refs = st.multiselect('Inputs to merge', list(choices),
                                    format_func=lambda ref: f'{ref[0].title()} #{ref[1]}', key=f'merge_inputs_{project_id}')
        st.caption('Choose at least two saved responses or versions. Only your selected inputs will be merged.')
    else:
        project = get_project(database_path, project_id)
        primary = get_primary_response(database_path, project_id)
        current = next((version for version in list_versions(database_path, project_id)
                        if version.id == project.current_version_id), None)
        modes = (['Working copy'] if primary else []) + (['Current saved version'] if current else [])
        if modes:
            mode = st.radio('Draft input', modes, horizontal=True, key=f'optional_input_{project_id}_{operation}')
            if mode == 'Working copy':
                base = current if current and current.derived_from_response_id == primary.id else None
                key = draft_state_key(project_id, primary.id, base.id if base else None)
                working_text = st.session_state.get(key, base.text if base else primary.text)
                input_refs = [('version', base.id)] if base else [('response', primary.id)]
                st.caption('Uses the current Working copy, including unsaved edits. Its exact text is snapshotted in the run.')
            else:
                input_refs = [('version', current.id)]
                st.caption(f'Uses saved version #{current.id}; unsaved edits are excluded.')
        else:
            st.caption('Select a primary response or save a current version first.')
    if operation == 'fact_check':
        st.caption('Source verification only: SUPPORTED / UNSUPPORTED / UNCLEAR. UNSUPPORTED means not substantiated by supplied sources, not false. No external research or model-consensus evidence.')
    instructions = st.text_area('Optional instructions', key=f'optional_instructions_{project_id}_{operation}')
    if st.button('Load / refresh optional model catalog', key=f'optional_catalog_{project_id}'):
        cached_catalog.clear()
        try:
            st.session_state['loaded_catalog'] = cached_catalog()
        except OpenRouterError as exc:
            st.session_state.pop('loaded_catalog', None)
            st.error(str(exc))
    catalog = st.session_state.get('loaded_catalog')
    plan = None
    if catalog:
        st.caption(f'Catalog fetched {catalog.fetched_at:%Y-%m-%d %H:%M:%S UTC}. Refresh after 15 minutes before executing.')
        free_only = st.checkbox('Optional models: free token pricing only', key=f'optional_free_{project_id}')
        models = {model.id: model for model in catalog.models if not free_only or model.free_tokens}
        key = f'optional_model_{project_id}'
        if st.session_state.get(key) not in models:
            st.session_state[key] = None
        model_id = st.selectbox('Optional operation model', list(models), index=None,
                                format_func=lambda value: model_label(models[value]), key=key)
        with st.expander('Optional generation parameters'):
            max_tokens = st.number_input('Optional maximum output tokens', min_value=1, max_value=32768,
                                         value=default_output_tokens(operation), step=128,
                                         key=f'optional_max_tokens_{project_id}_{operation}')
            temperature = st.slider('Optional temperature', 0.0, 2.0, 0.7, 0.1, key=f'optional_temperature_{project_id}')
        if model_id is not None:
            try:
                plan = prepare_optional_operation(database_path, project_id, catalog, model_id, operation,
                                                  input_refs, instructions, working_text, int(max_tokens), temperature)
            except ValueError as exc:
                st.caption(str(exc))
    else:
        st.caption('Load the catalog and explicitly choose the model for this operation.')
    st.write('Additional API calls: 1')
    st.write(f'Estimated additional cost: {money(plan.estimate.cost_usd if plan else None)}')
    if plan:
        if operation == 'fact_check':
            if plan.parameters['fact_check_output_mode'] == 'json_schema':
                st.caption('API-level JSON Schema: the catalog advertises structured-output support. '
                           'A compatible provider is required; endpoint acceptance is not guaranteed. '
                           'Local schema and source-reference validation still apply. No retry or fallback.')
            else:
                st.caption('Prompt-only / best-effort structured-output compatibility: JSON Schema support '
                           'is unavailable or unknown in the catalog. One request with local schema and '
                           'source-reference validation; no repair, retry, or fallback.')
        if plan.estimate.token_cost_usd == 0:
            st.caption('Estimated token cost: $0')
        st.caption(f'Estimate: ~{plan.estimate.input_tokens:,} input tokens (UTF-8 bytes ÷ 4 plus message overhead) '
                   f'and up to {plan.estimate.max_output_tokens:,} output tokens, using catalog prices and request fees. '
                   'Not a spending cap; actual tokenization, reasoning, routing, fees, or prices can differ.')
        with st.expander('Inspect optional request inputs and prompt'):
            st.json(plan.snapshot)
    if api_key is None:
        st.caption('Set OPENROUTER_API_KEY to execute. Reading and adopting saved candidates remain local.')
    if st.button(f'Run {LABELS[operation]}', key=f'run_optional_{project_id}_{operation}',
                 disabled=plan is None or api_key is None):
        try:
            with st.spinner(f'Running {LABELS[operation]}…'):
                run_id = generate(database_path, plan, OpenRouter(api_key))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[f'optional_history_{project_id}'] = run_id
            st.caption(f'Optional run #{run_id} saved. Your current draft and primary choice are unchanged.')


def render_optional_history(database_path: Path, project_id: int) -> None:
    runs = list_optional_runs(database_path, project_id)
    if not runs:
        return
    by_id = {run.id: run for run in runs}
    key = f'optional_history_{project_id}'
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = runs[0].id
    run_id = st.selectbox('Optional compute history', list(by_id), key=key,
                          format_func=lambda value: f'#{value} · {LABELS[by_id[value].operation]} · {by_id[value].status}')
    run = by_id[run_id]
    if run.status in {'pending', 'running'}:
        st.warning('This run is unfinished or still running. Completion and billing may be uncertain; it will not resume automatically.')
    with st.expander('Optional run inputs and provenance'):
        st.caption(f'Run #{run.id} · Parent run: {run.parent_run_id or "multiple / none"} · Prompt: {run.prompt_version}')
        st.json(json.loads(run.input_snapshot_json))
        st.json(json.loads(run.parameters_json))
    for request in list_run_requests(database_path, run_id):
        render_response(database_path, project_id, request, None, show_body=False, allow_review=False)
        response = request.response
        if response is None:
            continue
        if run.operation in {'rewrite', 'merge'}:
            st.caption('Candidate only. Saving/adopting below explicitly selects this response and makes a saved version current.')
            text = st.text_area('Candidate working copy', value=response.text, height=250,
                                key=f'candidate_text_{response.id}')
            if st.button('Save / adopt candidate as current', key=f'adopt_{response.id}', disabled=request.status != 'succeeded'):
                try:
                    version_id = adopt_candidate(database_path, project_id, response.id, text)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    target = draft_state_key(project_id, response.id, version_id)
                    st.session_state[target] = text
                    st.session_state.pop(f'editor_{target}', None)
                    st.session_state[f'review_notice_{project_id}'] = f'Candidate adopted as current version #{version_id}.'
                    st.rerun()
        elif response.structured_result_json:
            result = json.loads(response.structured_result_json)
            if run.operation == 'critique':
                st.caption('Model-generated qualitative feedback. No rating or selection was changed.')
                for field, notes in result.items():
                    st.markdown(f'**{field.replace("_", " ").capitalize()}**')
                    for note in notes:
                        st.text(note)
                    if not notes:
                        st.caption('None reported.')
            else:
                st.caption('Model-generated source verification, not an independent truth verdict. UNSUPPORTED means not substantiated by supplied sources, not false.')
                for claim in result['claims']:
                    st.write(claim['classification'])
                    st.text(claim['claim'])
                    st.text(claim['explanation'])
                    for ref in claim['references']:
                        st.caption(f'Source #{ref["source_id"]}')
                        st.text(ref['excerpt'])
                if not result['claims']:
                    st.caption('No factual claims were reported by the model.')
            with st.expander('Raw model output'):
                st.text(response.text)
        else:
            st.caption('Structured output could not be validated. Raw result retained; no repair request was made.')
            st.text(response.text)
