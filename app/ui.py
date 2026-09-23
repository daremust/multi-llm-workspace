"""Run from the repository root: python -m streamlit run app/ui.py."""

import sqlite3
from pathlib import Path

import streamlit as st
from pydantic import ValidationError

from app.config import load_settings
from app.db import (
    create_project, get_project, initialize_database, list_projects,
    add_text_source, add_webpage_source, list_sources, remove_source,
    save_project_inputs, add_manual_response, list_manual_responses,
)
from app.types import OUTPUT_FORMATS, Project


def main() -> None:
    st.set_page_config(page_title="Multi-LLM Workspace", layout="wide")
    st.markdown("""<style>
    /* Use the remaining viewport, with a small gutter beside the native sidebar. */
    .stMainBlockContainer { max-width: none; padding: 2.5rem 3rem 4rem; }
    [data-testid="stSidebar"] { min-width: 250px; max-width: 280px; }
    h1, h2, h3, input, textarea, button { font-family: inherit; }
    h1 { font-size: clamp(2rem, 3.5vw, 3.5rem); letter-spacing: -.04em; }
    h2 { font-size: 1.6rem; }
    hr { border: 0; border-top: 1px dashed #98a3b8; margin: 1.5rem 0; }
    .eyebrow, .stage-number { font-size: .85rem; letter-spacing: .06em; color: #475575; }
    .eyebrow { margin: 0 0 .5rem; }
    /* Blue comes from the native control theme; green is reserved for local status. */
    .workspace-status { text-align: right; color: #475575; font-size: .8rem; }
    .local-dot { color: #16803c; font-size: .75rem; margin-right: .45rem; }
    .local-label { color: #101626; }
    .local-note { color: #475575; font-size: .75rem; margin: .4rem 0 0 1.2rem; }
    [data-testid="stSidebarUserContent"] > [data-testid="stVerticalBlock"] {
        min-height: calc(100dvh - 8rem);
    }
    [data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.sidebar-local-status) {
        margin-top: auto; padding-top: 2rem;
    }
    .sidebar-title { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
    .sidebar-tagline { color: #475575; line-height: 1.6; margin-top: 1rem; }
    .workflow { display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr;
                gap: 1.5rem; align-items: center; padding: .75rem 0 1rem; }
    .workflow strong { display: block; margin-bottom: .8rem; color: #475575; }
    .workflow small { color: #475575; line-height: 1.7; }
    .workflow .current { color: #101626; }
    .workflow-arrow { color: #475575; font-size: 1.8rem; }
    .stage-heading { border-bottom: 1px dashed #98a3b8; padding-bottom: .8rem; margin-bottom: 1rem; }
    .stage-heading h3 { font-size: 1.1rem; margin: 0; padding: 0; letter-spacing: .04em; }
    .stage-number { display: block; margin-bottom: .5rem; }
    .stage-arrow { font-size: 1.7rem; margin: 1.5rem 0 1rem; color: #475575; }
    .source-entry { border-top: 1px dashed #98a3b8; margin: 1.5rem 0 1rem; }
    [data-testid="stExpander"] details { border: 0; border-bottom: 1px dashed #98a3b8; border-radius: 0; }
    @media (max-width: 850px) {
        .stMainBlockContainer { padding: 2rem 1.25rem; }
        .workflow { gap: .7rem; }
        .workflow small { display: none; }
        .workflow strong { font-size: .75rem; }
        .workflow-arrow { font-size: 1rem; }
    }
    </style>""", unsafe_allow_html=True)
    st.markdown(
        '<div class="workspace-status">v0.1.0 &nbsp; • &nbsp; '
        '<span class="local-dot" aria-hidden="true">●</span>'
        '<span class="local-label">Local</span></div>', unsafe_allow_html=True,
    )
    st.markdown('<p class="eyebrow">WELCOME TO</p>', unsafe_allow_html=True)
    st.title("Multi-LLM Workspace")
    st.write("Prepare source material, define a task, and choose an output format.")
    st.divider()
    st.markdown('<p class="eyebrow">HOW IT WORKS</p>', unsafe_allow_html=True)
    st.markdown("""<div class="workflow" aria-label="Workflow: Input, Generate, Compare, Export">
    <div><strong class="current">1. INPUT</strong><small>Add sources, a task,<br>and an output format.</small></div>
    <span class="workflow-arrow" aria-hidden="true">→</span>
    <div><strong>2. GENERATE</strong><small>Future stage<br>Not available yet.</small></div>
    <span class="workflow-arrow" aria-hidden="true">→</span>
    <div><strong>3. COMPARE</strong><small>Future stage<br>Not available yet.</small></div>
    <span class="workflow-arrow" aria-hidden="true">→</span>
    <div><strong>4. EXPORT</strong><small>Future stage<br>Not available yet.</small></div>
    </div>""", unsafe_allow_html=True)
    st.caption("INPUT is available now. Generate, Compare, and Export are future stages.")
    st.divider()
    try:
        settings = load_settings()
        initialize_database(settings.database_path)
        projects = list_projects(settings.database_path)
    except (OSError, sqlite3.Error, RuntimeError, ValidationError):
        st.error("Could not open the workspace. Check the local database path, permissions, and schema version.")
        st.stop()

    with st.sidebar:
        st.markdown('<div class="sidebar-title">Multi-LLM<br>Workspace<br>—</div>', unsafe_allow_html=True)
        st.markdown('<p class="sidebar-tagline">One task.<br>Multiple models.<br>Human judgment.</p>', unsafe_allow_html=True)
        st.divider()
        st.markdown("**Projects**")
        with st.form("create_project", clear_on_submit=True, border=False):
            name = st.text_input("Project name", max_chars=200)
            submitted = st.form_submit_button("Create project")
        if submitted:
            try:
                project = create_project(settings.database_path, name)
            except ValidationError:
                st.error("Enter a project name containing 1–200 characters.")
            except sqlite3.Error:
                st.error("Could not save the project. Check database permissions and try again.")
            else:
                st.session_state["project_id"] = project.id
                st.rerun()

        if projects:
            names = {project.id: project.name for project in projects}
            if st.session_state.get("project_id") not in names:
                st.session_state["project_id"] = projects[0].id
            st.selectbox(
                "Open project", options=list(names), key="project_id",
                format_func=lambda project_id: f"{names[project_id]} (#{project_id})",
            )

        st.markdown(
            '<div class="sidebar-local-status">'
            '<span class="local-dot" aria-hidden="true">●</span>'
            '<span class="local-label">Local workspace</span>'
            '<p class="local-note">Your data stays on your Mac.</p></div>',
            unsafe_allow_html=True,
        )

    if not projects:
        st.markdown("### QUICK START")
        st.write("Create your first project in the sidebar, then follow the inputs down the page.")
        st.markdown("Sources → Task → Output format → Optional manual response")
        st.caption("Your saved work stays on this device.")
        st.divider()
        st.caption("No model calls are made at this stage.")
        return
    try:
        current = get_project(settings.database_path, st.session_state["project_id"])
    except sqlite3.Error:
        st.error("Could not read the selected project. Try reopening the application.")
        return
    if current is None:
        st.warning("The selected project is no longer available. Reopen the application to refresh the list.")
        return
    st.subheader(current.name)
    st.caption(f"Project #{current.id} · Created {current.created_at:%Y-%m-%d %H:%M UTC}")
    try:
        render_workspace(settings.database_path, current)
    except sqlite3.Error:
        st.error("Could not read or save workspace data. Check database permissions and try again.")
    st.divider()
    st.caption("No model calls are made at this stage.")


def step(number: str, title: str, explanation: str, *, first: bool = False) -> None:
    # These headings contain only fixed UI copy; project/source text uses native components.
    if not first:
        st.markdown('<div class="stage-arrow" aria-hidden="true">↓</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="stage-heading"><span class="stage-number">{number}</span>'
        f'<h3>{title}</h3></div>', unsafe_allow_html=True,
    )
    st.caption(explanation)


def render_workspace(database_path: Path, project: Project) -> None:
    prefix = f"project_{project.id}"
    step("01", "SOURCE OF TRUTH", "The original material that future generations will be grounded in.", first=True)
    sources = list_sources(database_path, project.id)
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

    st.markdown('<div class="source-entry"></div>', unsafe_allow_html=True)
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

    step("02", "TASK", "Describe what you want to accomplish with these sources.")
    task = st.text_area("Task / instructions", value=project.task, key=f"{prefix}_task", height=150)
    step("03", "OUTPUT FORMAT", "Choose a format, or describe your own. Save the task and format together.")
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
            st.caption("Saved locally · Task and output format")

    step("04 · OPTIONAL", "MANUAL RESPONSE", "Paste a response you obtained elsewhere. Provider and model labels are unverified claims.")
    with st.form(f"{prefix}_manual", clear_on_submit=True, border=False):
        response_text = st.text_area("Manual response text", height=180)
        provider = st.text_input("Claimed provider (optional)")
        model = st.text_input("Claimed model (optional)")
        if st.form_submit_button("Save manual response"):
            try:
                add_manual_response(database_path, project.id, response_text, provider, model)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.caption("Saved locally · Manual response")
    for response in list_manual_responses(database_path, project.id):
        with st.expander(f"Manual response {response.id}"):
            st.caption("Origin: manual · No API request in this application. API usage and cost: not tracked (manual response).")
            st.caption(f"Claimed provider: {response.claimed_provider or 'unspecified'} · "
                       f"Claimed model: {response.claimed_model or 'unspecified'} · Provenance: unverified")
            st.text(response.text)


if __name__ == "__main__":
    main()
