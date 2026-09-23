import pytest

from app.db import (
    add_manual_response, add_text_source, connect, create_project, get_project,
    initialize_database, list_manual_responses, list_sources, remove_source, save_project_inputs,
)
from app.types import OUTPUT_FORMATS


def test_workspace_survives_reopen(database_path):
    project = create_project(database_path, 'Workspace')
    original = '  Original\n\ntext <b>unchanged</b>  \n'
    source = add_text_source(database_path, project.id, original, 'Reference')
    save_project_inputs(database_path, project.id, '  Summarize this.\n', 'Custom', 'A short table')
    response = add_manual_response(database_path, project.id, original, ' Provider ', ' Model ')
    initialize_database(database_path)
    reopened = get_project(database_path, project.id)
    assert reopened.task == '  Summarize this.\n'
    assert reopened.output_format == 'Custom'
    assert reopened.custom_output_format == 'A short table'
    assert list_sources(database_path, project.id) == [source]
    assert source.content == original
    assert source.type == 'text'
    assert source.original_url is None
    assert source.created_at.tzinfo is not None
    assert list_manual_responses(database_path, project.id) == [response]
    assert response.text == original
    assert response.origin == 'manual'
    assert response.claimed_provider == 'Provider'
    assert response.claimed_model == 'Model'
    assert response.verified_provider is response.verified_model is response.run_request_id is None
    with connect(database_path) as connection:
        for table in ('runs', 'run_requests', 'versions', 'project_events'):
            assert connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
        row = connection.execute('SELECT * FROM responses').fetchone()
        assert row['generation_metadata_json'] == '{}'
        assert row['finish_reason'] is None
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('output_format', OUTPUT_FORMATS)
def test_output_formats(database_path, output_format):
    project = create_project(database_path, 'Formats')
    save_project_inputs(database_path, project.id, 'Task', output_format, 'Custom description')
    reopened = get_project(database_path, project.id)
    assert reopened.output_format == output_format
    assert reopened.custom_output_format == ('Custom description' if output_format == 'Custom' else '')


@pytest.mark.parametrize('output,custom', [('unknown', ''), ('Custom', ' \n')])
def test_invalid_format_does_not_partially_save(database_path, output, custom):
    project = create_project(database_path, 'Validation')
    with pytest.raises(ValueError):
        save_project_inputs(database_path, project.id, 'Changed', output, custom)
    assert get_project(database_path, project.id) == project


def test_isolation_removal_and_optional_claims(database_path):
    first = create_project(database_path, 'First')
    second = create_project(database_path, 'Second')
    source = add_text_source(database_path, first.id, 'Source')
    response = add_manual_response(database_path, first.id, 'Response')
    assert response.claimed_provider is response.claimed_model is None
    assert list_sources(database_path, second.id) == []
    assert list_manual_responses(database_path, second.id) == []
    remove_source(database_path, second.id, source.id)
    assert list_sources(database_path, first.id) == [source]
    remove_source(database_path, first.id, source.id)
    assert list_sources(database_path, first.id) == []


@pytest.mark.parametrize('add', [add_text_source, add_manual_response])
def test_blank_content_rejected(database_path, add):
    project = create_project(database_path, 'Empty')
    with pytest.raises(ValueError):
        add(database_path, project.id, ' \n ')
    assert list_sources(database_path, project.id) == []
    assert list_manual_responses(database_path, project.id) == []
