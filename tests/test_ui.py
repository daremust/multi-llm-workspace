from navigation_support import browser_app
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.db import list_projects

UI_PATH = Path(__file__).resolve().parents[1] / "app" / "ui.py"


def test_create_and_reopen_projects_without_api_key(app_environment):
    app = browser_app(str(UI_PATH)).run()
    assert not app.exception
    assert not any("No model calls happen automatically" in caption.value for caption in app.caption)
    assert any("**Tip:** Start with your source" in item.value for item in app.markdown)
    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value("First project")
    widget(app.sidebar.button, 'Create').click().run()
    assert not app.exception
    assert app.subheader[0].value == "First project"
    app.run()
    assert len(list_projects(app_environment)) == 1

    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value("Second project")
    widget(app.sidebar.button, 'Create').click().run()
    assert not app.exception
    assert app.subheader[0].value == "Second project"

    reopened = browser_app(str(UI_PATH)).run()
    assert not reopened.exception
    first = next(project for project in list_projects(app_environment) if project.name == "First project")
    reopened.sidebar.selectbox[0].select(first.id).run()
    assert reopened.subheader[0].value == "First project"
    assert len(list_projects(app_environment)) == 2


def test_blank_project_name_is_rejected(app_environment):
    app = browser_app(str(UI_PATH)).run()
    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value("   ")
    widget(app.sidebar.button, 'Create').click().run()
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

    app = browser_app(str(UI_PATH)).run()
    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value('Workspace')
    widget(app.sidebar.button, 'Create').click().run()
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

    reopened = browser_app(str(UI_PATH), project.id).run()
    assert not reopened.exception
    assert widget(reopened.text_area, 'Task / instructions').value == 'Summarize the sources'
    assert widget(reopened.selectbox, 'Output format').value == 'Custom'
    assert widget(reopened.text_area, 'Custom format / instructions').value == 'Three bullets'
    assert any(text.value == 'Original source' for text in reopened.text)
    assert any(text.value == 'A manually obtained response' for text in reopened.text)
    assert any('No API request' in caption.value for caption in reopened.caption)

    widget(reopened.sidebar.button, '+ Create project').click().run()
    reopened.sidebar.text_input[0].set_value('Another project')
    widget(reopened.sidebar.button, 'Create').click().run()
    assert widget(reopened.text_area, 'Task / instructions').value == ''
    assert not any(text.value in ('Original source', 'A manually obtained response') for text in reopened.text)
    assert any('Not configured' in c.value for c in reopened.caption)
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

    app = browser_app(str(UI_PATH)).run()
    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value('Validation')
    widget(app.sidebar.button, 'Create').click().run()
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


def test_progressive_disclosure_preserves_saved_and_unsaved_inputs(app_environment):
    from app.db import get_project

    app = browser_app(str(UI_PATH)).run()
    assert not next(e for e in app.expander if e.label == 'How this workspace works').proto.expanded
    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value('Disclosure')
    widget(app.sidebar.button, 'Create').click().run()
    project = list_projects(app_environment)[0]
    assert not next(e for e in app.expander if e.label.startswith('01  SOURCE')).proto.expanded
    assert not next(e for e in app.expander if e.label.startswith('02  TASK')).proto.expanded
    widget(app.text_area, 'Original source text').set_value('Saved original')
    widget(app.button, 'Save text source').click().run()
    widget(app.text_area, 'Task / instructions').set_value('Saved task')
    widget(app.text_area, 'Custom format / instructions').set_value('Saved custom format')
    widget(app.button, 'Save task and format').click().run()
    assert not app.exception
    for label in ('How this workspace works', '01  SOURCE', '02  TASK', 'Add / inspect manual responses'):
        assert not next(e for e in app.expander if e.label.startswith(label)).proto.expanded
    assert any('Saved task · Custom' in c.value for c in app.caption)
    widget(app.text_area, 'Task / instructions').set_value('Unsaved task').run()
    widget(app.text_area, 'Custom format / instructions').set_value('Unsaved format').run()
    app.run()
    assert widget(app.text_area, 'Task / instructions').value == 'Unsaved task'
    assert widget(app.text_area, 'Custom format / instructions').value == 'Unsaved format'
    assert any('Saved task · Custom' in c.value for c in app.caption)
    assert any('Unsaved task / format edits' in c.value for c in app.caption)
    assert get_project(app_environment, project.id).task == 'Saved task'
    widget(app.button, 'Save task and format').click().run()
    assert any('Unsaved task · Custom' in c.value for c in app.caption)
    assert get_project(app_environment, project.id).custom_output_format == 'Unsaved format'
    assert not any('Unsaved task / format edits' in c.value for c in app.caption)


def test_task_drafts_initialize_without_duplicate_state_warnings(app_environment, monkeypatch):
    from unittest.mock import Mock
    from streamlit.elements.lib import policies
    from app.db import create_project, get_project, initialize_database, save_project_inputs

    initialize_database(app_environment)
    first = create_project(app_environment, 'First draft')
    second = create_project(app_environment, 'Second draft')
    for project in (first, second):
        save_project_inputs(app_environment, project.id, f'Task {project.id}',
                            'Custom', f'Format {project.id}')

    original_check = policies.check_session_state_rules
    monkeypatch.setattr(policies, '_shown_default_value_warning', False)
    checked = set()

    def check_without_suppression(default_value, key, writes_allowed=True):
        # Reset before each affected widget: another widget/test cannot mask a warning.
        if key in {f'project_{p.id}_{field}' for p in (first, second)
                   for field in ('task', 'custom', 'format')}:
            policies._shown_default_value_warning = False
            checked.add(key)
            assert not policies.config.get_option('global.disableWidgetStateDuplicationWarning')
        return original_check(default_value, key, writes_allowed)

    monkeypatch.setattr(policies, 'check_session_state_rules', check_without_suppression)
    warning = Mock(wraps=policies._LOGGER.warning)
    monkeypatch.setattr(policies._LOGGER, 'warning', warning)

    def values(app, task, custom, output='Custom'):
        assert not app.exception
        assert widget(app.text_area, 'Task / instructions').value == task
        assert widget(app.selectbox, 'Output format').value == output
        if output == 'Custom':
            assert widget(app.text_area, 'Custom format / instructions').value == custom
        assert not any('created with a default value' in w.value
                       and any(f'"{key}"' in w.value for key in checked) for w in app.warning)

    app = browser_app(str(UI_PATH), first.id).run()
    values(app, f'Task {first.id}', f'Format {first.id}')
    app.run()
    widget(app.text_area, 'Task / instructions').set_value('Unsaved task').run()
    widget(app.text_area, 'Custom format / instructions').set_value('Unsaved format').run()
    app.run()
    values(app, 'Unsaved task', 'Unsaved format')
    widget(app.selectbox, 'Output format').select('Plain answer').run()
    app.run()
    values(app, 'Unsaved task', 'Unsaved format', 'Plain answer')
    widget(app.sidebar.button, 'Multi-LLM\nWorkspace').click().run()
    widget(app.sidebar.selectbox, 'Open project').select(first.id).run()
    values(app, 'Unsaved task', 'Unsaved format', 'Plain answer')
    widget(app.sidebar.selectbox, 'Open project').select(second.id).run()
    values(app, f'Task {second.id}', f'Format {second.id}')
    widget(app.sidebar.selectbox, 'Open project').select(first.id).run()
    values(app, 'Unsaved task', 'Unsaved format', 'Plain answer')
    widget(app.selectbox, 'Output format').select('Custom').run()
    values(app, 'Unsaved task', 'Unsaved format')
    widget(app.selectbox, 'Output format').select('Plain answer').run()
    fresh = browser_app(str(UI_PATH), first.id).run()
    values(fresh, f'Task {first.id}', f'Format {first.id}')
    for project in (first, second):
        saved = get_project(app_environment, project.id)
        assert saved.task == f'Task {project.id}'
        assert saved.custom_output_format == f'Format {project.id}'
        assert saved.output_format == 'Custom'
    assert len(checked) == 6
    assert not any('created with a default value' in str(call)
                   and any(key in str(call) for key in checked) for call in warning.call_args_list)


def test_workspace_visual_identity_and_native_symbols(app_environment):
    app = browser_app(str(UI_PATH)).run()
    assert app.title[0].value == 'Multi-LLM Workspace'
    assert any('Compare multiple models. Use more compute only when it’s useful.' in m.value for m in app.markdown)
    sidebar = [m.value for m in app.sidebar.markdown]
    assert any(b.label == 'Multi-LLM\nWorkspace' for b in app.sidebar.button)
    assert any('“Better ideas come from more perspectives.”' in m and '— Multi-LLM Workspace' in m for m in sidebar)
    assert any('Workspace data is stored locally.' in m for m in sidebar)
    widget(app.sidebar.button, '+ Create project').click().run()
    app.sidebar.text_input[0].set_value('Visual review')
    widget(app.sidebar.button, 'Create').click().run()
    workflow = ' '.join(m.value for m in app.markdown if 'aria-label="Workspace workflow"' in m.value)
    for text in ('01 INPUT', '02 GENERATE', '03 COMPARE', '04 OPTIONAL COMPUTE', 'aria-hidden="true">tune</span>'):
        assert text in workflow
    assert 'FINISH' not in workflow and 'EXPORT' not in workflow
    assert any('v0.1.0 &nbsp; | &nbsp;' in m.value for m in app.markdown)
    assert any('**Tip:** Start with your source and at least one model.' in m.value for m in app.markdown)
    assert any(c.value == 'You can fine-tune everything later.' for c in app.caption)
    assert not any('No model calls happen automatically' in c.value for c in app.caption)


def test_create_disclosure_cancel_and_submit_validation(app_environment):
    app = browser_app(str(UI_PATH)).run()
    assert not app.sidebar.text_input
    assert not app.error
    widget(app.sidebar.button, '+ Create project').click().run()
    assert widget(app.sidebar.text_input, 'Project name').value == ''
    assert not app.error
    widget(app.sidebar.button, 'Cancel').click().run()
    assert not app.sidebar.text_input
    assert list_projects(app_environment) == []
    widget(app.sidebar.button, '+ Create project').click().run()
    widget(app.sidebar.button, 'Create').click().run()
    assert any('Enter a project name' in e.value for e in app.error)
    widget(app.sidebar.text_input, 'Project name').set_value('Created explicitly')
    widget(app.sidebar.button, 'Create').click().run()
    assert not app.exception
    assert not app.sidebar.text_input
    assert app.subheader[0].value == 'Created explicitly'


def test_home_workflow_and_project_tip_help_order(app_environment):
    app = browser_app(str(UI_PATH)).run()
    assert any('class="workflow workflow--hero"' in m.value for m in app.main.markdown)
    brand = widget(app.sidebar.button, 'Multi-LLM\nWorkspace')
    assert not brand.proto.help
    widget(app.sidebar.button, '+ Create project').click().run()
    widget(app.sidebar.text_input, 'Project name').set_value('Tip placement')
    widget(app.sidebar.button, 'Create').click().run()
    nodes = list(app.main)
    assert app.subheader[0].proto.hide_anchor
    final = next(i for i, n in enumerate(nodes) if getattr(n, 'label', '').startswith('05  FINAL'))
    help_index = next(i for i, n in enumerate(nodes) if getattr(n, 'label', '') == 'How this workspace works')
    tip = next(i for i, n in enumerate(nodes) if n.type == 'markdown' and '**Tip:**' in n.value)
    assert final < help_index < tip
    assert not any('Appearance: use Streamlit' in c.value for c in app.caption)
    assert widget(app.sidebar.selectbox, 'Open project')
    assert any(p.proto.popover.label == '⋮' for p in app.get('popover'))
    widget(app.button, 'Dismiss').click().run()
    app.run()
    assert not any('**Tip:**' in m.value for m in app.markdown)
    assert not app.exception


def test_project_hash_sync_tracks_visible_selection_without_data_changes(app_environment):
    import json
    from app.db import create_project, initialize_database, get_project
    initialize_database(app_environment)
    project = create_project(app_environment, 'Test Project')
    before = get_project(app_environment, project.id)
    app = browser_app(str(UI_PATH), project.id).run()
    def fragment():
        return next(json.loads(e.proto.json)['hash'] for e in app.get('bidi_component')
                    if e.proto.component_name == 'workspace_navigation')
    assert fragment() == f'#project-{project.id}'
    widget(app.sidebar.button, 'Multi-LLM\nWorkspace').click().run()
    assert fragment() == ''
    assert app.session_state['project_id'] is None
    widget(app.sidebar.selectbox, 'Open project').select(project.id).run()
    assert fragment() == f'#project-{project.id}'
    assert get_project(app_environment, project.id) == before
    assert not app.exception
