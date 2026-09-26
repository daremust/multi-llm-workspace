import pytest

from app.db import (
    connect, create_generation_run, create_project, get_project, list_run_requests,
    mark_request_running, save_completion, finish_generation_run,
)
from app.openrouter import Completion
from app.review import (
    get_primary_response, list_versions, rate_response, select_primary_response,
    save_review_version, set_current_version,
)


def seed_run(path, project_id, count=3, failed=False):
    run_id = create_generation_run(path, {'project_id': project_id}, {'model_ids': [f'author/model-{i}' for i in range(count)]}, 'test', None)
    for i, request in enumerate(list_run_requests(path, run_id)):
        mark_request_running(path, request.id)
        result = (Completion(error='Timeout: uncertain; no retry.', uncertain=True) if failed and i == count - 1 else
                  Completion(text=f'Original response {i}', model=request.requested_model, input_tokens=10,
                             output_tokens=20, cost_usd=0, latency_ms=26100, finish_reason='stop'))
        save_completion(path, request.id, result, {'catalog_name': f'Model {i}', 'model_namespace': 'author'})
    finish_generation_run(path, run_id)
    return run_id


def counts(path):
    with connect(path) as connection:
        return tuple(connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                     for table in ('versions', 'project_events', 'runs', 'run_requests', 'responses'))


def test_review_persistence_immutability_and_idempotence(database_path):
    project = create_project(database_path, 'Review')
    run_id = seed_run(database_path, project.id, failed=True)
    responses = [r.response for r in list_run_requests(database_path, run_id) if r.response]
    first, second = responses
    assert get_primary_response(database_path, project.id) is None
    rate_response(database_path, project.id, first.id, 5)
    assert get_primary_response(database_path, project.id) is None
    rate_response(database_path, project.id, first.id, 2)
    assert list_run_requests(database_path, run_id)[0].response.human_rating == 2
    select_primary_response(database_path, project.id, second.id)
    assert get_primary_response(database_path, project.id).id == second.id
    assert not list_versions(database_path, project.id)
    select_primary_response(database_path, project.id, first.id)
    version1 = save_review_version(database_path, project.id, first.id, '  Human edit\n')
    version2 = save_review_version(database_path, project.id, first.id, 'Another edit', version1)
    versions = list_versions(database_path, project.id)
    assert versions[0].derived_from_version_id == version1
    assert versions[0].derived_from_response_id == first.id
    assert versions[1].text == '  Human edit\n'
    assert get_project(database_path, project.id).current_version_id == version2
    before = counts(database_path)
    assert save_review_version(database_path, project.id, first.id, 'Another edit', version2) == version2
    select_primary_response(database_path, project.id, first.id)
    rate_response(database_path, project.id, first.id, 2)
    set_current_version(database_path, project.id, version2)
    assert counts(database_path) == before
    set_current_version(database_path, project.id, version1)
    assert get_project(database_path, project.id).current_version_id == version1
    assert get_primary_response(database_path, project.id).text == first.text
    assert list_run_requests(database_path, run_id)[0].response.text == first.text
    # Fresh database connections supply all saved review state.
    assert list_versions(database_path, project.id)[0].id == version2
    with connect(database_path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('rating', [0, 6, True, '5', 1.5])
def test_invalid_rating(database_path, rating):
    project = create_project(database_path, 'Validation')
    run_id = seed_run(database_path, project.id, 1)
    response = list_run_requests(database_path, run_id)[0].response
    with pytest.raises(ValueError):
        rate_response(database_path, project.id, response.id, rating)
    assert counts(database_path)[1] == 0


def test_project_scope_and_explicit_selection(database_path):
    first = create_project(database_path, 'First')
    second = create_project(database_path, 'Second')
    run_id = seed_run(database_path, first.id, 1)
    response = list_run_requests(database_path, run_id)[0].response
    for operation in (
        lambda: select_primary_response(database_path, second.id, response.id),
        lambda: rate_response(database_path, second.id, response.id, 4),
        lambda: save_review_version(database_path, first.id, response.id, 'Without selection'),
        lambda: select_primary_response(database_path, first.id, 999999),
    ):
        with pytest.raises(ValueError):
            operation()
    select_primary_response(database_path, first.id, response.id)
    with pytest.raises(ValueError):
        save_review_version(database_path, first.id, response.id, '  ')
    version = save_review_version(database_path, first.id, response.id, 'Edited')
    with pytest.raises(ValueError):
        set_current_version(database_path, second.id, version)
    assert get_project(database_path, second.id).current_version_id is None
    assert get_primary_response(database_path, second.id) is None
    rate_response(database_path, first.id, response.id, 3)
    rate_response(database_path, first.id, response.id, None)
    assert get_primary_response(database_path, first.id).human_rating is None
