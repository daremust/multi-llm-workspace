"""Run from the repository root: python -m streamlit run app/ui.py."""

import sqlite3

import streamlit as st
from pydantic import ValidationError

from app.config import load_settings
from app.db import create_project, get_project, initialize_database, list_projects


def main() -> None:
    st.set_page_config(page_title="Multi-LLM Workspace", layout="wide")
    st.title("Multi-LLM Workspace")
    st.caption("A local, human-guided workspace. Foundation: create and reopen projects.")
    try:
        settings = load_settings()
        initialize_database(settings.database_path)
        projects = list_projects(settings.database_path)
    except (OSError, sqlite3.Error, RuntimeError, ValidationError):
        st.error("Could not open the workspace. Check the local database path, permissions, and schema version.")
        st.stop()

    if settings.openrouter_api_key is None:
        st.info("OpenRouter API key is not configured. You can create and reopen local projects without it.")
    st.caption("No model API calls are available in this milestone.")

    with st.sidebar:
        st.header("Projects")
        with st.form("create_project", clear_on_submit=True):
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

    if not projects:
        st.write("Create your first project using the sidebar. Projects are saved locally in SQLite.")
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
    st.success("Project saved locally.")
    st.write("Sources, tasks, model responses, and review tools will be added in later milestones.")


if __name__ == "__main__":
    main()
