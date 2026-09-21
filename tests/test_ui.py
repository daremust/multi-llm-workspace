from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.db import list_projects

UI_PATH = Path(__file__).resolve().parents[1] / "app" / "ui.py"


def test_create_and_reopen_projects_without_api_key(app_environment):
    app = AppTest.from_file(str(UI_PATH)).run()
    assert not app.exception
    assert "not configured" in app.info[0].value
    app.text_input[0].set_value("First project")
    app.button[0].click().run()
    assert not app.exception
    assert app.subheader[0].value == "First project"
    app.run()
    assert len(list_projects(app_environment)) == 1

    app.text_input[0].set_value("Second project")
    app.button[0].click().run()
    assert not app.exception
    assert app.subheader[0].value == "Second project"

    reopened = AppTest.from_file(str(UI_PATH)).run()
    assert not reopened.exception
    first = next(project for project in list_projects(app_environment) if project.name == "First project")
    reopened.selectbox[0].select(first.id).run()
    assert reopened.subheader[0].value == "First project"
    assert len(list_projects(app_environment)) == 2


def test_blank_project_name_is_rejected(app_environment):
    app = AppTest.from_file(str(UI_PATH)).run()
    app.text_input[0].set_value("   ")
    app.button[0].click().run()
    assert not app.exception
    assert "Enter a project name" in app.error[0].value
    assert list_projects(app_environment) == []


def test_database_error_is_safe_and_key_is_not_displayed(app_environment, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DB_PATH", str(app_environment.parent))
    monkeypatch.setenv("OPENROUTER_API_KEY", "never-display-this-key")
    app_environment.parent.mkdir(parents=True)
    app = AppTest.from_file(str(UI_PATH)).run()
    assert not app.exception
    assert "Could not open the workspace" in app.error[0].value
    assert "never-display-this-key" not in str(app)
