"""Run from the repository root: python -m streamlit run app/ui.py."""

import sqlite3
import re
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from pydantic import SecretStr, ValidationError

from app.config import load_settings
from app.db import (
    create_project, get_project, initialize_database, list_projects, rename_project, delete_project,
    add_text_source, add_webpage_source, list_sources, remove_source,
    save_project_inputs, add_manual_response, list_manual_responses,
    list_generation_runs, list_run_requests,
)
from app.types import OUTPUT_FORMATS, Project
from app.generation_ui import render_generation, render_results, money
from app.generation import prepare_generation
from app.optional_ui import render_optional_compute
from app.ui_style import apply_style, render_workflow, summary, section_label, status_label
from app.review_ui import render_editor
from app.review import get_primary_response


def generation_summary(database_path: Path, project_id: int, api_key: SecretStr | None) -> str:
    """Read-only readiness/cost summary; never loads a catalog or dispatches."""
    selected = st.session_state.get(f'models_{project_id}', [])
    catalog = st.session_state.get('loaded_catalog')
    state = 'Choose models'
    if selected and catalog:
        try:
            plan = prepare_generation(database_path, project_id, catalog, selected,
                                      int(st.session_state.get(f'max_tokens_{project_id}', 1024)),
                                      st.session_state.get(f'temperature_{project_id}', 0.7))
        except ValueError:
            state = f'{len(selected)} models · Check inputs / catalog before generating'
        else:
            state = f'{len(selected)} API call{"s" if len(selected) != 1 else ""} · Estimated cost: {money(plan.estimate.cost_usd)}'
    if api_key is None:
        state += ' · API key not configured'
    return state


def main() -> None:
    st.set_page_config(page_title="Multi-LLM Workspace", layout="wide")
    apply_style()
    st.markdown(
        '<div class="workspace-status">v0.1.0 &nbsp; | &nbsp; '
        '<span class="local-dot" aria-hidden="true">●</span>'
        '<span class="local-label">Local</span></div>', unsafe_allow_html=True,
    )
    try:
        settings = load_settings()
        initialize_database(settings.database_path)
        projects = list_projects(settings.database_path)
    except (OSError, sqlite3.Error, RuntimeError, ValidationError):
        st.error("Could not open the workspace. Check the local database path, permissions, and schema version.")
        st.stop()

    # Keep keyed edits/parameters alive even when Home removes their widgets.
    for key in list(st.session_state):
        if workspace_state_key(key):
            st.session_state[key] = st.session_state[key]
    with st.sidebar:
        with st.container(key='sidebar_layout'):
            with st.container(key='sidebar_upper'):
                render_project_controls(settings.database_path, projects)
            with st.container(key='sidebar_lower'):
                st.markdown(
                    '<div class="sidebar-local-status">'
                    '<p class="sidebar-quote">“Better ideas come from more perspectives.”<br><br>'
                    '— Multi-LLM Workspace</p>'
                    '<span class="local-dot" aria-hidden="true">●</span>'
                    '<span class="local-label">Local workspace</span>'
                    '<p class="local-note">Workspace data is stored locally.</p></div>',
                    unsafe_allow_html=True,
                )

    if st.session_state.get('project_id') is None:
        sync_project_hash(home=True)
        with st.container(key='home_composition'):
            with st.container(key='home_hero'):
                st.title("Multi-LLM Workspace")
                st.write("Compare multiple models. Use more compute only when it’s useful.")
                render_workflow(hero=True)
                st.caption("Create or open a project in the sidebar.")
            render_help()
            render_tip()
        return
    try:
        current = get_project(settings.database_path, st.session_state["project_id"])
    except sqlite3.Error:
        st.error("Could not read the selected project. Try reopening the application.")
        return
    if current is None:
        st.warning("The selected project is no longer available. Reopen the application to refresh the list.")
        return
    render_workflow()
    st.subheader(current.name, anchor=False)
    sync_project_hash(home=False)
    try:
        render_workspace(settings.database_path, current, settings.openrouter_api_key)
    except sqlite3.Error:
        st.error("Could not read or save workspace data. Check database permissions and try again.")
    render_help()
    render_tip()


def sync_project_hash(*, home: bool) -> None:
    """Mirror visible selection without routing, reloads, or new history entries.

    Read Streamlit's rendered heading ID so its existing slug rules remain authoritative.
    The observer handles the heading arriving after this zero-size component mounts.
    """
    st.html('<style>.st-key-project_url_sync { display: none; }</style>')
    with st.container(key='project_url_sync'):
        components.html("""<script>
const home = HOME_STATE;
const page = window.parent;
function sync() {
    const heading = page.document.querySelector('[data-testid="stMain"] h3[id]');
    if (!home && !heading) return;
    const url = new URL(page.location.href);
    url.hash = home ? '' : heading.id;
    if (url.href !== page.location.href) page.history.replaceState(page.history.state, '', url);
}
sync();
const observer = new MutationObserver(sync);
observer.observe(page.document.querySelector('[data-testid="stMain"]'), {childList: true, subtree: true});
window.addEventListener('unload', () => observer.disconnect());
</script>""".replace('HOME_STATE', 'true' if home else 'false'), height=0)


def workspace_state_key(key: str, project_id: int | None = None) -> bool:
    pid = str(project_id) if project_id is not None else r'\d+'
    return bool(re.fullmatch(
        rf'(project_{pid}_(task|format|custom|input_notice)|'
        rf'(models|max_tokens|temperature|free_models|run|history|review_notice|merge_inputs)_{pid}|'
        rf'optional_(operation|model|free|temperature|history)_{pid}|'
        rf'optional_(input|instructions|max_tokens)_{pid}_[a-z_]+|'
        rf'(editor_)?review_draft_{pid}_.*)', key))


def go_home() -> None:
    st.session_state['project_id'] = None
    st.session_state['project_selector'] = None
    st.session_state.pop('project_action', None)


def open_selected_project() -> None:
    st.session_state['project_id'] = st.session_state['project_selector']
    st.session_state.pop('project_action', None)


def render_project_controls(database_path: Path, projects: list[Project]) -> None:
    st.button("Multi-LLM\nWorkspace", key='workspace_home', on_click=go_home)
    st.markdown('<div class="sidebar-rule">—</div>', unsafe_allow_html=True)
    st.markdown('<p class="sidebar-tagline">One task.<br>Multiple models.<br>Human judgment.</p>', unsafe_allow_html=True)
    with st.container(key="sidebar_projects_heading"):
        st.markdown("**Projects**")
    if st.button('+ Create project', key='show_create_project'):
        st.session_state['creating_project'] = True
    if st.session_state.get('creating_project', False):
        with st.form('create_project', clear_on_submit=False, border=False):
            name = st.text_input('Project name', max_chars=200)
            left, right = st.columns(2)
            create = left.form_submit_button('Create')
            cancel = right.form_submit_button('Cancel')
        if cancel:
            st.session_state['creating_project'] = False
            st.rerun()
        if create:
            try:
                project = create_project(database_path, name)
            except ValidationError:
                st.error('Enter a project name containing 1–200 characters.')
            except sqlite3.Error:
                st.error('Could not save the project. Check database permissions and try again.')
            else:
                st.session_state['creating_project'] = False
                st.session_state['project_id'] = project.id
                st.session_state['project_selector'] = project.id
                st.session_state.pop('project_action', None)
                st.rerun()
    names = {project.id: project.name for project in projects}
    # Preserve the existing initial opening behavior, but explicit Home stays Home.
    if 'project_id' not in st.session_state:
        st.session_state['project_id'] = projects[0].id if projects else None
    if st.session_state['project_id'] is not None and st.session_state['project_id'] not in names:
        go_home()
    if st.session_state.get('project_selector') != st.session_state['project_id']:
        st.session_state['project_selector'] = st.session_state['project_id']
    st.caption('Open project')
    with st.container(key='project_control'):
        selector, actions = st.columns([5, 1], gap='small')
        selector.selectbox('Open project', list(names), index=None, key='project_selector',
                           format_func=lambda pid: f'{names[pid]} (#{pid})',
                           on_change=open_selected_project, label_visibility='collapsed')
        pid = st.session_state['project_id']
        with actions.popover('⋮', disabled=pid is None, help='Project actions'):
            if st.button('Rename', disabled=pid is None):
                st.session_state['project_action'] = ('rename', pid)
            if st.button('Delete project', disabled=pid is None):
                st.session_state['project_action'] = ('delete', pid)
    action = st.session_state.get('project_action')
    if not action or action[1] != pid or pid is None:
        return
    if action[0] == 'rename':
        with st.form(f'rename_project_{pid}', border=False):
            name = st.text_input('New project name', value=names[pid], max_chars=200)
            rename = st.form_submit_button('Save name')
            cancel = st.form_submit_button('Cancel rename')
        if cancel:
            st.session_state.pop('project_action', None)
            st.rerun()
        if rename:
            try:
                rename_project(database_path, pid, name)
            except ValidationError:
                st.error('Enter a project name containing 1–200 characters.')
            except (sqlite3.Error, ValueError):
                st.error('Could not rename the project.')
            else:
                st.session_state.pop('project_action', None)
                st.rerun()
    else:
        st.warning(f'Delete “{names[pid]}”? This permanently removes the local project and all its local data/history.')
        if st.button('Confirm delete project'):
            try:
                delete_project(database_path, pid)
            except sqlite3.Error:
                st.error('Could not delete the project. No partial deletion was saved.')
            else:
                for key in list(st.session_state):
                    if workspace_state_key(key, pid):
                        del st.session_state[key]
                # The selector has already rendered. Reset its value on the next run.
                st.session_state['project_id'] = None
                st.session_state.pop('project_action', None)
                st.rerun()
        if st.button('Cancel deletion'):
            st.session_state.pop('project_action', None)
            st.rerun()


def render_help() -> None:
    with st.container(key='workspace_help'):
        with st.expander('How this workspace works'):
            st.write('Save sources, task, and format. Choose models and generate explicitly. '
                     'Compare responses, select a primary, then edit and save a version.')
            st.write('Optional compute runs only when requested. Finish / Save keeps your current output locally.')
            st.caption('Explicit model actions send selected inputs to OpenRouter.')


def dismiss_tip() -> None:
    st.session_state['workspace_tip_dismissed'] = True


def render_tip() -> None:
    if st.session_state.get('workspace_tip_dismissed', False):
        return
    with st.container(key='workspace_tip'):
        message, action = st.columns([6, 1])
        with message:
            st.markdown(":blue[:material/lightbulb:] **Tip:** Start with your source and at least one model.")
            st.caption("You can fine-tune everything later.")
        action.button('Dismiss', key='dismiss_workspace_tip', on_click=dismiss_tip)


def render_workspace(database_path: Path, project: Project, api_key: SecretStr | None = None) -> None:
    prefix = f"project_{project.id}"
    sources = list_sources(database_path, project.id)
    with st.expander(section_label("01  SOURCE OF TRUTH", f"{len(sources)} sources saved")):
        st.caption("Inspect original sources or add source material.")
        if not sources:
            st.write("No sources yet. Add your first source below.")
        for source in sources:
            label = source.original_url if source.type == 'webpage' else 'pasted text'
            with st.expander(f"Source {source.id} — {source.title or label}"):
                st.caption(f"{label} · Saved {source.created_at:%Y-%m-%d %H:%M UTC}")
                if source.final_url and source.final_url != source.original_url:
                    st.caption(f"Fetched from: {source.final_url}")
                st.text(source.content)
                if st.button("Remove source", key=f"{prefix}_remove_{source.id}"):
                    remove_source(database_path, project.id, source.id)
                    st.rerun()

        st.markdown("**Add source material**")
        st.caption("Paste the original text or fetch a public page. Each saved source appears above for inspection.")
        paste_tab, webpage_tab = st.tabs(["Paste text", "Public webpage"])
        with paste_tab:
            with st.form(f"{prefix}_text", clear_on_submit=True, border=False):
                title = st.text_input("Source label (optional)")
                text = st.text_area("Original source text", height=180)
                if st.form_submit_button("Save text source"):
                    try:
                        add_text_source(database_path, project.id, text, title)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()
        with webpage_tab:
            st.caption("Public HTTP/HTTPS pages only. For LinkedIn or pages requiring sign-in, paste text.")
            with st.form(f"{prefix}_webpage", clear_on_submit=True, border=False):
                url = st.text_input("Webpage URL")
                if st.form_submit_button("Fetch and save webpage"):
                    try:
                        with st.spinner("Fetching readable page text…"):
                            add_webpage_source(database_path, project.id, url)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        st.rerun()

    task_summary = (summary(project.task, 64) or "Not configured") + " · " + project.output_format
    with st.expander(section_label("02  TASK & OUTPUT", task_summary)):
        st.caption("Saved state is shown above. Save task and format together before generating with edits.")
        task = st.text_area("Task / instructions", value=project.task, key=f"{prefix}_task", height=150)
        output = st.selectbox("Output format", OUTPUT_FORMATS,
                              index=OUTPUT_FORMATS.index(project.output_format), key=f"{prefix}_format")
        custom = ''
        if output == 'Custom':
            custom = st.text_area("Custom format / instructions", value=project.custom_output_format,
                                  key=f"{prefix}_custom")
        if st.button("Save task and format", key=f"{prefix}_save"):
            try:
                save_project_inputs(database_path, project.id, task, output, custom)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state[f"{prefix}_input_notice"] = "Saved locally · Task and output format"
                st.rerun()

        if (task != project.task or output != project.output_format or custom != project.custom_output_format):
            st.caption("Unsaved task / format edits · Save task and format before generating with these changes.")
        elif notice := st.session_state.get(f"{prefix}_input_notice"):
            st.caption(notice)

    with st.expander(section_label("03  GENERATE", generation_summary(database_path, project.id, api_key))):
        render_generation(database_path, project.id, api_key)

    runs = list_generation_runs(database_path, project.id)
    if runs:
        latest = runs[0]
        requests = list_run_requests(database_path, latest.id)
        response_count = sum(request.response is not None for request in requests)
        compare_summary = f"Latest run #{latest.id} · {response_count} responses · {status_label(latest.status)}"
        if any(request.cost_usd is None for request in requests):
            compare_summary += " · Cost unknown"
    else:
        compare_summary = "No generation runs yet"
    with st.expander(section_label("04  COMPARE", compare_summary)):
        render_results(database_path, project.id)
        if not runs:
            st.caption("Generate responses to compare. Manual external responses remain available below.")
        render_manual_responses(database_path, project.id)

    render_optional_compute(database_path, project.id, api_key)

    # Read again: explicit adoption in Optional Compute may have changed the current version.
    current = get_project(database_path, project.id)
    final_summary = f"Version #{current.current_version_id}" if current.current_version_id else "No saved version"
    primary = get_primary_response(database_path, project.id)
    if primary:
        final_summary += f" · primary response #{primary.id}"
    with st.expander(section_label("05  FINAL", final_summary)):
        render_editor(database_path, project.id)


def render_manual_responses(database_path: Path, project_id: int) -> None:
    prefix = f"project_{project_id}"
    with st.expander("Add / inspect manual responses"):
        with st.form(f"{prefix}_manual", clear_on_submit=True, border=False):
            response_text = st.text_area("Manual response text", height=180)
            provider = st.text_input("Claimed provider (optional)")
            model = st.text_input("Claimed model (optional)")
            if st.form_submit_button("Save manual response"):
                try:
                    add_manual_response(database_path, project_id, response_text, provider, model)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.caption("Saved locally · Manual response")
        for response in list_manual_responses(database_path, project_id):
            with st.expander(f"Manual response {response.id}"):
                st.caption("Origin: manual · No API request in this application. API usage and cost: not tracked (manual response).")
                st.caption(f"Claimed provider: {response.claimed_provider or 'unspecified'} · "
                           f"Claimed model: {response.claimed_model or 'unspecified'} · Provenance: unverified")
                st.text(response.text)


if __name__ == "__main__":
    main()
