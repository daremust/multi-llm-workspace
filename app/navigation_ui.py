"""Local project-ID fragment navigation."""
import re

import streamlit as st
import streamlit.components.v2 as components

NAV_JS = """
export default function({data, parentElement, setStateValue}) {
    parentElement.__navigationDispose?.();
    const state = parentElement.__navigationState ||= {revision: -1, sentInitial: false};
    const report = () => setStateValue('location', {
        hash: window.location.hash,
        revision: Math.max(0, state.revision),
        event: crypto.randomUUID()
    });
    if (!data.ready) {
        if (!state.sentInitial) {
            state.sentInitial = true;
            report();
        }
    } else if (data.revision > state.revision) {
        const url = new URL(window.location.href);
        url.hash = data.hash;
        if (url.href !== window.location.href) {
            window.history.replaceState(window.history.state, '', url);
        }
        state.revision = data.revision;
    }
    window.addEventListener('hashchange', report);
    const dispose = () => {
        window.removeEventListener('hashchange', report);
        if (parentElement.__navigationDispose === dispose) {
            delete parentElement.__navigationDispose;
        }
    };
    parentElement.__navigationDispose = dispose;
    return dispose;
}
"""


def resolve_hash(value, project_ids):
    match = re.fullmatch(r'#project-([1-9][0-9]{0,18})', value) if isinstance(value, str) else None
    project_id = int(match[1]) if match else None
    return project_id if project_id in project_ids else None


def select_project(state, project_id):
    state['project_id'] = project_id
    state['nav_revision'] = state.get('nav_revision', 0) + 1
    state.pop('project_action', None)


def accept_location(state, event, project_ids):
    if not isinstance(event, dict) or not isinstance(event.get('event'), str):
        return False
    if event['event'] == state.get('nav_event'):
        return False
    state['nav_event'] = event['event']
    if event.get('revision') != state.get('nav_revision', 0):
        return False  # An older browser report must not undo a newer selection.
    project_id = resolve_hash(event.get('hash'), project_ids)
    select_project(state, project_id)
    state['project_selector'] = project_id
    state['nav_ready'] = True
    return True


def render_navigation(projects):
    ids = {project.id for project in projects}
    state = st.session_state
    if state.get('nav_ready') and state.get('project_id') not in ids | {None}:
        select_project(state, None)
        state['project_selector'] = None
    project_id = state.get('project_id')
    component = components.component('workspace_navigation', js=NAV_JS)
    result = component(
        key='project_navigation', height=0,
        data={'ready': state.get('nav_ready', False),
              'revision': state.get('nav_revision', 0),
              'hash': f'#project-{project_id}' if project_id is not None else ''},
        on_location_change=lambda: None,
    )
    if accept_location(state, result.location, ids):
        st.rerun()
    if not state.get('nav_ready'):
        st.caption('Opening workspace…')
        st.stop()
