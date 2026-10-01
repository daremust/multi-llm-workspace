from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.db import create_project, get_project, initialize_database, list_run_requests
from app.review import get_primary_response, list_versions
from test_review import counts, seed_run

UI_PATH = Path(__file__).resolve().parents[1] / 'app' / 'ui.py'


def widget(elements, label):
    return next(element for element in elements if element.label == label)


@pytest.fixture
def review_environment(app_environment, monkeypatch):
    initialize_database(app_environment)
    def forbidden(*args, **kwargs):
        raise AssertionError('Human review must not call a provider or generation service.')
    monkeypatch.setattr('app.generation_ui.cached_catalog', forbidden)
    monkeypatch.setattr('app.generation_ui.generate', forbidden)
    monkeypatch.setattr('app.openrouter.OpenRouter.catalog', forbidden)
    monkeypatch.setattr('app.openrouter.OpenRouter.complete', forbidden)
    return app_environment


@pytest.mark.parametrize('count', [1, 2, 3, 4, 6])
def test_comparison_single_row_and_no_automatic_mutations(review_environment, count):
    project = create_project(review_environment, 'Comparison')
    seed_run(review_environment, project.id, count, failed=count >= 3)
    before = counts(review_environment)
    app = AppTest.from_file(str(UI_PATH)).run()
    assert not app.exception
    # One horizontal block with exactly N columns, rather than rows of two.
    comparison_rows = [block for block in app.get('flex_container')
                       if len(block.children) == count and all(child.type == 'column' for child in block.children.values())]
    if not comparison_rows:  # Streamlit versions using the horizontal-block test representation.
        comparison_rows = [block for block in app.get('horizontal') if len(block.children) == count]
    assert comparison_rows
    successes = count - 1 if count >= 3 else count
    assert len([area for area in app.selectbox if area.label == 'Human rating']) == successes
    assert len([button for button in app.button if button.label == 'Set as primary']) == successes
    if count >= 3:
        assert any('uncertain' in error.value for error in app.error)
    assert get_primary_response(review_environment, project.id) is None
    app.run()
    assert counts(review_environment) == before


def test_full_review_reruns_switching_and_reopen(review_environment):
    first = create_project(review_environment, 'First')
    second = create_project(review_environment, 'Second')
    run_id = seed_run(review_environment, first.id, 3, failed=True)
    app = AppTest.from_file(str(UI_PATH)).run()
    app.sidebar.selectbox[0].select(first.id).run()
    assert not app.exception
    widget(app.selectbox, 'Human rating').select(4).run()
    widget(app.button, 'Save rating').click().run()
    response = list_run_requests(review_environment, run_id)[0].response
    assert response.human_rating == 4
    assert get_primary_response(review_environment, first.id) is None
    widget(app.button, 'Set as primary').click().run()
    assert not app.exception
    assert widget(app.text_area, 'Working copy').value == response.text
    widget(app.text_area, 'Working copy').set_value('Unsaved human draft').run()
    assert any('Unsaved Working copy edits.' in c.value for c in app.caption)
    app.sidebar.selectbox[0].select(second.id).run()
    app.sidebar.selectbox[0].select(first.id).run()
    assert widget(app.text_area, 'Working copy').value == 'Unsaved human draft'
    widget(app.button, 'Save version').click().run()
    assert not app.exception
    version1 = get_project(review_environment, first.id).current_version_id
    before = counts(review_environment)
    app.run()
    assert counts(review_environment) == before
    widget(app.button, 'Finish / Save').click().run()
    assert counts(review_environment) == before
    widget(app.text_area, 'Working copy').set_value('Final human output').run()
    widget(app.button, 'Finish / Save').click().run()
    version2 = get_project(review_environment, first.id).current_version_id
    assert version1 != version2
    assert list_run_requests(review_environment, run_id)[0].response.text == response.text
    widget(app.selectbox, 'Saved version').select(version1).run()
    widget(app.button, 'Set version as current').click().run()
    assert get_project(review_environment, first.id).current_version_id == version1
    before = counts(review_environment)
    reopened = AppTest.from_file(str(UI_PATH)).run()
    reopened.sidebar.selectbox[0].select(first.id).run()
    assert not reopened.exception
    assert get_primary_response(review_environment, first.id).id == response.id
    assert widget(reopened.text_area, 'Working copy').value == 'Unsaved human draft'
    assert widget(reopened.selectbox, 'Human rating').value == 4
    assert len(list_versions(review_environment, first.id)) == 2
    assert counts(review_environment) == before


def test_review_rerun_uses_loaded_catalog_without_network(review_environment):
    from app.openrouter import parse_catalog

    project = create_project(review_environment, 'Offline review')
    seed_run(review_environment, project.id, 1)
    app = AppTest.from_file(str(UI_PATH))
    app.session_state['loaded_catalog'] = parse_catalog({'data': []})
    app.run()
    widget(app.button, 'Set as primary').click().run()
    widget(app.button, 'Finish / Save').click().run()
    assert not app.exception
    assert len(list_versions(review_environment, project.id)) == 1


def test_switch_current_version_resets_working_copy(review_environment):
    project = create_project(review_environment, 'Version sync')
    seed_run(review_environment, project.id, 1)
    app = AppTest.from_file(str(UI_PATH)).run()
    widget(app.button, 'Set as primary').click().run()
    widget(app.text_area, 'Working copy').set_value('VERSION 1: London').run()
    widget(app.button, 'Save version').click().run()
    version1 = get_project(review_environment, project.id).current_version_id
    widget(app.text_area, 'Working copy').set_value('VERSION 2: Liverpool').run()
    widget(app.button, 'Save version').click().run()
    version2 = get_project(review_environment, project.id).current_version_id
    assert version1 != version2
    assert widget(app.text_area, 'Working copy').value == 'VERSION 2: Liverpool'
    before = counts(review_environment)
    widget(app.selectbox, 'Saved version').select(version1).run()
    widget(app.button, 'Set version as current').click().run()
    assert not app.exception
    assert get_project(review_environment, project.id).current_version_id == version1
    assert widget(app.text_area, 'Working copy').value == 'VERSION 1: London'
    assert counts(review_environment)[0] == before[0]
    widget(app.text_area, 'Working copy').set_value('Unsaved London draft').run()
    after_switch = counts(review_environment)
    app.run()
    assert widget(app.text_area, 'Working copy').value == 'Unsaved London draft'
    assert counts(review_environment) == after_switch
    widget(app.selectbox, 'Saved version').select(version2).run()
    widget(app.button, 'Set version as current').click().run()
    assert widget(app.text_area, 'Working copy').value == 'VERSION 2: Liverpool'
    widget(app.selectbox, 'Saved version').select(version1).run()
    widget(app.button, 'Set version as current').click().run()
    assert widget(app.text_area, 'Working copy').value == 'VERSION 1: London'


def test_workspace_hierarchy_keeps_review_state_on_reruns(review_environment):
    from app.db import add_text_source, save_project_inputs
    from app.review import select_primary_response, save_review_version

    project = create_project(review_environment, 'Quiet workspace')
    add_text_source(review_environment, project.id, 'Original source')
    save_project_inputs(review_environment, project.id, 'Saved task', 'Plain answer')
    run_id = seed_run(review_environment, project.id, 4, failed=True)
    response = list_run_requests(review_environment, run_id)[0].response
    select_primary_response(review_environment, project.id, response.id)
    version = save_review_version(review_environment, project.id, response.id, 'Saved version')
    before = counts(review_environment)
    app = AppTest.from_file(str(UI_PATH)).run()
    sections = [e for e in app.main.expander if e.label.startswith(
        ('01  ', '02  ', '03  ', '04  ', 'OPTIONAL COMPUTE', '05  '))]
    assert [e.label.split(' · ')[0] for e in sections] == [
        '01  SOURCE OF TRUTH', '02  TASK & OUTPUT', '03  GENERATE', '04  COMPARE', 'OPTIONAL COMPUTE', '05  FINAL']
    assert all(not e.proto.expanded for e in sections)
    assert 'Partial' in sections[3].label and 'Cost unknown' in sections[3].label
    assert len([b for b in sections[3].button if b.label == 'Set as primary']) == 3
    assert not any(t.label == 'Working copy' for t in sections[3].text_area)
    assert widget(sections[5].text_area, 'Working copy').value == 'Saved version'
    assert 'No automatic' not in sections[0].label
    widget(app.text_area, 'Working copy').set_value('Unsaved working copy').run()
    widget(app.text_area, 'Task / instructions').set_value('Unsaved task').run()
    app.run()
    assert not app.exception
    assert widget(app.text_area, 'Working copy').value == 'Unsaved working copy'
    assert counts(review_environment) == before
    assert get_primary_response(review_environment, project.id).id == response.id
    assert get_project(review_environment, project.id).current_version_id == version
    assert get_project(review_environment, project.id).task == 'Saved task'


def test_tip_dismissal_preserves_draft_and_review_state(review_environment):
    from app.review import select_primary_response, save_review_version

    project = create_project(review_environment, 'Actual project name')
    run = seed_run(review_environment, project.id, 1)
    response = list_run_requests(review_environment, run)[0].response
    select_primary_response(review_environment, project.id, response.id)
    version = save_review_version(review_environment, project.id, response.id, 'Saved version')
    app = AppTest.from_file(str(UI_PATH)).run()
    assert app.subheader[0].value == 'Actual project name'
    widget(app.text_area, 'Working copy').set_value('Unsaved working text').run()
    before = counts(review_environment)
    widget(app.button, 'Dismiss').click().run()
    app.run()
    assert not app.exception
    assert not any(b.label == 'Dismiss' for b in app.button)
    assert not any('**Tip:**' in m.value for m in app.markdown)
    assert widget(app.text_area, 'Working copy').value == 'Unsaved working text'
    assert counts(review_environment) == before
    assert get_project(review_environment, project.id).current_version_id == version
    assert get_primary_response(review_environment, project.id).id == response.id


def test_home_and_rename_preserve_edits_parameters_and_data(review_environment):
    from app.db import save_project_inputs
    from app.openrouter import parse_catalog
    from test_openrouter import model_entry
    from test_db import seed_project_graph, project_graph
    project = seed_project_graph(review_environment, 'Before rename')
    save_project_inputs(review_environment, project.id, 'Saved task', 'Custom', 'Saved format')
    before = get_project(review_environment, project.id)
    graph = project_graph(review_environment, project.id)
    app = AppTest.from_file(str(UI_PATH))
    app.session_state['loaded_catalog'] = parse_catalog({'data': [model_entry('author/free', '0', '0')]})
    app.run()
    widget(app.text_area, 'Working copy').set_value('Unsaved working text').run()
    widget(app.text_area, 'Task / instructions').set_value('Unsaved task').run()
    widget(app.text_area, 'Custom format / instructions').set_value('Unsaved format').run()
    widget(app.multiselect, 'Models').set_value(['author/free']).run()
    widget(app.number_input, 'Maximum output tokens per model').set_value(2048).run()
    widget(app.sidebar.button, 'Multi-LLM\nWorkspace').click().run()
    assert not app.exception
    assert app.session_state['project_id'] is None
    assert app.title[0].value == 'Multi-LLM Workspace'
    assert get_project(review_environment, project.id) == before
    assert project_graph(review_environment, project.id) == graph
    assert not app.sidebar.expander  # Help now follows the main content.
    assert any(e.label == 'How this workspace works' for e in app.main.expander)
    app.run()
    widget(app.sidebar.selectbox, 'Open project').select(project.id).run()
    assert widget(app.text_area, 'Working copy').value == 'Unsaved working text'
    assert widget(app.text_area, 'Task / instructions').value == 'Unsaved task'
    assert widget(app.text_area, 'Custom format / instructions').value == 'Unsaved format'
    assert widget(app.multiselect, 'Models').value == ['author/free']
    assert widget(app.number_input, 'Maximum output tokens per model').value == 2048
    widget(app.sidebar.button, 'Rename').click().run()
    widget(app.sidebar.text_input, 'New project name').set_value('Renamed project')
    widget(app.sidebar.button, 'Save name').click().run()
    assert not app.exception
    assert app.subheader[0].value == 'Renamed project'
    assert get_project(review_environment, project.id).name == 'Renamed project'
    assert project_graph(review_environment, project.id) == graph
    assert widget(app.text_area, 'Working copy').value == 'Unsaved working text'


def test_confirmed_delete_returns_home_and_preserves_other_project(review_environment):
    from test_db import seed_project_graph, project_graph
    from app.db import list_projects
    other = seed_project_graph(review_environment, 'Keep')
    selected = seed_project_graph(review_environment, 'Delete')
    graph = project_graph(review_environment, other.id)
    app = AppTest.from_file(str(UI_PATH)).run()
    widget(app.sidebar.button, 'Delete project').click().run()
    assert get_project(review_environment, selected.id) is not None
    assert any('all its local data/history' in w.value for w in app.warning)
    widget(app.sidebar.button, 'Cancel deletion').click().run()
    assert get_project(review_environment, selected.id) is not None
    widget(app.sidebar.button, 'Delete project').click().run()
    widget(app.sidebar.button, 'Confirm delete project').click().run()
    assert not app.exception
    assert app.session_state['project_id'] is None
    assert app.title[0].value == 'Multi-LLM Workspace'
    assert get_project(review_environment, selected.id) is None
    assert project_graph(review_environment, other.id) == graph
    assert [p.id for p in list_projects(review_environment)] == [other.id]
    assert not any('Delete (#' in option for option in widget(app.sidebar.selectbox, 'Open project').options)
