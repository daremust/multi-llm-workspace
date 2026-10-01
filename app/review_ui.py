"""Local comparison and human editing; no provider or generation calls."""

import json
from pathlib import Path

import streamlit as st

from app.db import get_project
from app.review import (
    get_primary_response, list_versions, rate_response, save_review_version,
    select_primary_response, set_current_version,
)
from app.types import RunRequest
from app.ui_style import status_label


def _cost(value: float | None) -> str:
    return 'unknown' if value is None else f'${value:g}'


def render_comparison(database_path: Path, project_id: int, requests: list[RunRequest]) -> None:
    primary = get_primary_response(database_path, project_id)
    st.caption('Human review only · No API calls. Ratings do not reorder or select responses.')
    if not requests:
        st.caption('This run has no saved requests.')
        return
    with st.container(key='review_comparison'):
        for column, request in zip(st.columns(len(requests)), requests):
            with column:
                render_response(database_path, project_id, request, primary.id if primary else None)


def render_response(database_path: Path, project_id: int, request: RunRequest, primary_id: int | None, *,
                    show_body: bool = True, allow_review: bool = True) -> None:
    response = request.response
    metadata = json.loads(response.generation_metadata_json) if response else {}
    name = metadata.get('catalog_name') or request.requested_model.split('/', 1)[-1]
    status = status_label(request.status)
    st.write(f'{name} · {"✓" if request.status == "succeeded" else status}')
    if response and response.id == primary_id:
        st.caption('Primary response · selected by you')
    latency = f'{request.latency_ms / 1000:.1f}s' if request.latency_ms is not None else 'latency unknown'
    tokens = (f'{request.input_tokens + request.output_tokens:,} tok'
              if request.input_tokens is not None and request.output_tokens is not None else 'tokens unknown')
    namespace = metadata.get('model_namespace') or request.requested_model.partition('/')[0]
    cost = 'Cost unknown' if request.cost_usd is None else f'Actual cost: {_cost(request.cost_usd)}'
    st.caption(f'{namespace} · {latency} · {tokens} · {cost}')
    if request.error_message:
        st.error(request.error_message)
    with st.expander('Details'):
        st.caption(f'Gateway: {response.verified_provider if response else "OpenRouter"} · Request #{request.id} · {status}')
        st.caption(f'Input tokens: {request.input_tokens if request.input_tokens is not None else "unknown"} · '
                   f'Output tokens: {request.output_tokens if request.output_tokens is not None else "unknown"} · '
                   f'{cost} · Latency: {latency}')
        st.json({
            'requested_model': request.requested_model,
            'reported_model': response.verified_model if response else None,
            'gateway': response.verified_provider if response else 'openrouter',
            'upstream_provider': metadata.get('upstream_provider'),
            'request_id': request.provider_request_id, 'request_status': request.status,
            'error': request.error_message, 'input_tokens': request.input_tokens,
            'output_tokens': request.output_tokens, 'reported_cost_usd': request.cost_usd,
            'latency_ms': request.latency_ms, 'finish_reason': response.finish_reason if response else None,
            'origin': response.origin if response else None,
            'saved_timestamp': response.created_at.isoformat() if response else None,
            'request_created_at': request.created_at.isoformat(),
            'request_completed_at': request.completed_at.isoformat() if request.completed_at else None,
            'generation_metadata': metadata,
        })
        st.caption('Null metadata means unknown / not reported. Catalog namespace is a label, not verified upstream provenance.')
    if response and show_body:
        st.text(response.text)
    if allow_review and response and request.status == 'succeeded':
        options = [None, 1, 2, 3, 4, 5]
        rating = st.selectbox('Human rating', options, index=options.index(response.human_rating),
                              format_func=lambda value: 'Unrated' if value is None else '★' * value,
                              key=f'rating_{response.id}_{response.human_rating}')
        if st.button('Save rating', key=f'rate_{response.id}'):
            rate_response(database_path, project_id, response.id, rating)
            st.rerun()
        if st.button('Set as primary', key=f'primary_{response.id}', disabled=response.id == primary_id):
            select_primary_response(database_path, project_id, response.id)
            st.rerun()


def draft_state_key(project_id: int, response_id: int, version_id: int | None) -> str:
    return f'review_draft_{project_id}_{response_id}_{version_id if version_id else "original"}'


def _keep_draft(widget_key: str, draft_key: str) -> None:
    st.session_state[draft_key] = st.session_state[widget_key]


def render_editor(database_path: Path, project_id: int) -> None:
    st.markdown('### EDIT → SAVE VERSION → FINISH')
    primary = get_primary_response(database_path, project_id)
    project = get_project(database_path, project_id)
    versions = list_versions(database_path, project_id)
    current = next((version for version in versions if version.id == project.current_version_id), None)
    if primary is None:
        st.caption('No primary response selected. Choose one explicitly to begin editing.')
    else:
        base = current if current and current.derived_from_response_id == primary.id else None
        st.caption(f'Primary response #{primary.id} · Starting from ' + (f'version #{base.id}.' if base else 'the original response.'))
        draft_key = draft_state_key(project_id, primary.id, base.id if base else None)
        widget_key = f'editor_{draft_key}'
        # Separate non-widget state survives Streamlit removing widgets on project/run switches.
        if draft_key not in st.session_state:
            st.session_state[draft_key] = base.text if base else primary.text
        edited = st.text_area('Working copy', value=st.session_state[draft_key], height=300,
                              key=widget_key, on_change=_keep_draft, args=(widget_key, draft_key))
        state = 'Unsaved Working copy edits. ' if edited != (base.text if base else primary.text) else ''
        st.caption(state + 'Original responses stay unchanged. Unsaved edits stay in this browser session; save a version to preserve them across restarts.')
        save = st.button('Save version', key=f'save_version_{project_id}')
        finish = st.button('Finish / Save', key=f'finish_review_{project_id}')
        if save or finish:
            try:
                version_id = save_review_version(database_path, project_id, primary.id, edited, base.id if base else None)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state[f'review_notice_{project_id}'] = (
                    f'Finished · Current version: #{version_id}.' if finish
                    else f'Saved version #{version_id} is now the Current version.'
                )
                st.rerun()
    if notice := st.session_state.get(f'review_notice_{project_id}'):
        st.caption(notice)
    st.markdown('#### Version history')
    if not versions:
        st.caption('No saved versions yet.')
        return
    st.caption(f'Current version: #{project.current_version_id}' if current else 'No Current version.')
    with st.expander('Browse version history'):
        by_id = {version.id: version for version in versions}
        version_id = st.selectbox('Saved version', list(by_id), key=f'history_{project_id}', format_func=lambda value:
                                 f'#{value} · {by_id[value].created_at:%Y-%m-%d %H:%M:%S UTC} · response #{by_id[value].derived_from_response_id}')
        version = by_id[version_id]
        with st.expander('Inspect saved version'):
            st.caption(f'Operation: {version.operation} · Source response: {version.derived_from_response_id} · Base version: {version.derived_from_version_id}')
            st.text(version.text)
        if st.button('Set version as current', key=f'current_version_{project_id}', disabled=version.id == project.current_version_id):
            set_current_version(database_path, project_id, version.id)
            # Explicit version changes replace that version's cached draft and widget value.
            # Ordinary reruns still preserve unsaved edits through _keep_draft.
            target_draft = draft_state_key(project_id, version.derived_from_response_id, version.id)
            st.session_state[target_draft] = version.text
            st.session_state.pop(f'editor_{target_draft}', None)
            st.session_state.pop(f'review_notice_{project_id}', None)
            st.rerun()
