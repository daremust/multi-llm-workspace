from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.db import list_projects

UI_PATH = Path(__file__).resolve().parents[1] / "app" / "ui.py"


def test_create_and_reopen_projects_without_api_key(app_environment):
    app = AppTest.from_file(str(UI_PATH)).run()
    assert not app.exception
    assert any("No model calls" in caption.value for caption in app.caption)
    app.sidebar.text_input[0].set_value("First project")
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert app.subheader[0].value == "First project"
    app.run()
    assert len(list_projects(app_environment)) == 1

    app.sidebar.text_input[0].set_value("Second project")
    app.sidebar.button[0].click().run()
    assert not app.exception
    assert app.subheader[0].value == "Second project"

    reopened = AppTest.from_file(str(UI_PATH)).run()
    assert not reopened.exception
    first = next(project for project in list_projects(app_environment) if project.name == "First project")
    reopened.sidebar.selectbox[0].select(first.id).run()
    assert reopened.subheader[0].value == "First project"
    assert len(list_projects(app_environment)) == 2


def test_blank_project_name_is_rejected(app_environment):
    app = AppTest.from_file(str(UI_PATH)).run()
    app.sidebar.text_input[0].set_value("   ")
    app.sidebar.button[0].click().run()
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


def widget(elements, label):
    return next(element for element in elements if element.label == label)


def test_full_input_workspace_reopens_and_isolates_projects(app_environment):
    from app.db import get_project, list_manual_responses, list_sources

    app = AppTest.from_file(str(UI_PATH)).run()
    app.sidebar.text_input[0].set_value('Workspace')
    app.sidebar.button[0].click().run()
    project = list_projects(app_environment)[0]
    widget(app.text_input, 'Source label (optional)').set_value('Reference')
    widget(app.text_area, 'Original source text').set_value('  Original source\n')
    widget(app.button, 'Save text source').click().run()
    assert not app.exception
    assert list_sources(app_environment, project.id)[0].content == '  Original source\n'
    widget(app.text_area, 'Task / instructions').set_value('Summarize the sources')
    widget(app.text_area, 'Custom format / instructions').set_value('Three bullets')
    widget(app.button, 'Save task and format').click().run()
    widget(app.text_area, 'Manual response text').set_value('A manually obtained response')
    widget(app.text_input, 'Claimed provider (optional)').set_value('Claimed provider')
    widget(app.text_input, 'Claimed model (optional)').set_value('Claimed model')
    widget(app.button, 'Save manual response').click().run()
    assert not app.exception
    assert list_manual_responses(app_environment, project.id)[0].run_request_id is None

    reopened = AppTest.from_file(str(UI_PATH)).run()
    assert not reopened.exception
    assert widget(reopened.text_area, 'Task / instructions').value == 'Summarize the sources'
    assert widget(reopened.selectbox, 'Output format').value == 'Custom'
    assert widget(reopened.text_area, 'Custom format / instructions').value == 'Three bullets'
    assert any(text.value == 'Original source' for text in reopened.text)
    assert any(text.value == 'A manually obtained response' for text in reopened.text)
    assert any('No API request' in caption.value for caption in reopened.caption)

    reopened.sidebar.text_input[0].set_value('Another project')
    reopened.sidebar.button[0].click().run()
    assert widget(reopened.text_area, 'Task / instructions').value == ''
    assert not reopened.text
    reopened.sidebar.selectbox[0].select(project.id).run()
    assert widget(reopened.text_area, 'Task / instructions').value == 'Summarize the sources'
    widget(reopened.selectbox, 'Output format').select('Email').run()
    assert not any(area.label == 'Custom format / instructions' for area in reopened.text_area)
    widget(reopened.button, 'Save task and format').click().run()
    assert get_project(app_environment, project.id).output_format == 'Email'
    widget(reopened.button, 'Remove source').click().run()
    assert not reopened.exception
    assert list_sources(app_environment, project.id) == []


def test_ui_validation_and_fetch_error(app_environment, monkeypatch):
    from app.webpage import WebpageError
    from app.db import list_sources

    app = AppTest.from_file(str(UI_PATH)).run()
    app.sidebar.text_input[0].set_value('Validation')
    app.sidebar.button[0].click().run()
    widget(app.button, 'Save task and format').click().run()
    assert 'Describe the custom' in app.error[0].value
    widget(app.button, 'Save text source').click().run()
    assert 'cannot be empty' in app.error[0].value
    widget(app.button, 'Save manual response').click().run()
    assert 'cannot be empty' in app.error[0].value

    def fail(url):
        raise WebpageError('No readable text was found.')
    monkeypatch.setattr('app.webpage.fetch_webpage', fail)
    widget(app.text_input, 'Webpage URL').set_value('https://example.org')
    widget(app.button, 'Fetch and save webpage').click().run()
    assert 'No readable text' in app.error[0].value
    assert not app.exception
    assert list_sources(app_environment, list_projects(app_environment)[0].id) == []
