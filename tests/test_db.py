import sqlite3

import pytest
from pydantic import ValidationError

from app.db import connect, create_project, get_project, initialize_database, list_projects


def test_initialization_is_repeatable_and_preserves_projects(database_path):
    project = create_project(database_path, "  Example  ")
    initialize_database(database_path)
    assert get_project(database_path, project.id) == project
    assert project.name == "Example"
    assert project.created_at.utcoffset().total_seconds() == 0
    with connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"projects", "sources", "runs", "run_requests", "responses", "versions", "project_events"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_create_reopen_and_list_parameterized_names(database_path):
    assert list_projects(database_path) == []
    first = create_project(database_path, "Robert'); DROP TABLE projects;--")
    second = create_project(database_path, first.name)
    assert first.id != second.id
    assert [project.id for project in list_projects(database_path)] == [second.id, first.id]
    assert get_project(database_path, first.id).name == first.name
    assert get_project(database_path, 9999) is None


@pytest.mark.parametrize("name", ["", " \n ", "x" * 201])
def test_invalid_names_do_not_write_projects(database_path, name):
    with pytest.raises(ValidationError):
        create_project(database_path, name)
    assert list_projects(database_path) == []


def test_rejects_newer_schema_without_changing_it(database_path):
    with connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="Unsupported"):
        initialize_database(database_path)
    with connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99


def test_rejects_existing_unversioned_database(tmp_path):
    path = tmp_path / "unknown.db"
    with connect(path) as connection:
        connection.execute("CREATE TABLE existing (id INTEGER)")
    with pytest.raises(RuntimeError, match="unversioned"):
        initialize_database(path)
    with connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone()[0] == "existing"


def test_transaction_rolls_back(database_path):
    with pytest.raises(RuntimeError):
        with connect(database_path) as connection:
            connection.execute("INSERT INTO projects (name, created_at, updated_at) VALUES ('Rollback', 'now', 'now')")
            raise RuntimeError("abort")
    assert list_projects(database_path) == []


def test_current_version_must_belong_to_project(database_path):
    first = create_project(database_path, "First")
    second = create_project(database_path, "Second")
    with connect(database_path) as connection:
        version_id = connection.execute(
            "INSERT INTO versions (project_id, operation, text, created_at) VALUES (?, 'manual_edit', 'Draft', 'now')",
            (first.id,),
        ).lastrowid
        connection.execute("UPDATE projects SET current_version_id = ? WHERE id = ?", (version_id, first.id))
    with pytest.raises(sqlite3.IntegrityError):
        with connect(database_path) as connection:
            connection.execute("UPDATE projects SET current_version_id = ? WHERE id = ?", (version_id, second.id))


def test_schema_enforces_manual_provenance(database_path):
    project = create_project(database_path, "Provenance")
    with connect(database_path) as connection:
        connection.execute(
            "INSERT INTO responses (project_id, text, origin, claimed_provider, created_at) VALUES (?, 'Pasted', 'manual', 'claimed', 'now')",
            (project.id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with connect(database_path) as connection:
            connection.execute("UPDATE responses SET verified_provider = 'openrouter'")



def project_graph(path, project_id):
    tables = ('sources', 'runs', 'run_requests', 'responses', 'versions', 'project_events')
    with connect(path) as connection:
        return {table: [dict(row) for row in connection.execute(
            f'SELECT * FROM {table} WHERE project_id = ? ORDER BY id', (project_id,))] for table in tables}


def seed_project_graph(path, name):
    from app.db import add_text_source, add_manual_response, list_run_requests
    from app.review import select_primary_response, save_review_version, rate_response
    from test_review import seed_run
    project = create_project(path, name)
    add_text_source(path, project.id, 'Immutable source', 'Source')
    add_manual_response(path, project.id, 'External response')
    run = seed_run(path, project.id, 2)
    child = seed_run(path, project.id, 1)
    with connect(path) as connection:
        connection.execute('UPDATE runs SET parent_run_id = ? WHERE id = ?', (run, child))
    response = list_run_requests(path, run)[0].response
    select_primary_response(path, project.id, response.id)
    rate_response(path, project.id, response.id, 4)
    base = save_review_version(path, project.id, response.id, 'First version')
    save_review_version(path, project.id, response.id, 'Second version', base)
    return project


def test_rename_preserves_identity_and_owned_graph(database_path):
    from app.db import rename_project
    project = seed_project_graph(database_path, 'Before')
    before = get_project(database_path, project.id)
    graph = project_graph(database_path, project.id)
    rename_project(database_path, project.id, '  After  ')
    renamed = get_project(database_path, project.id)
    assert renamed.name == 'After'
    assert renamed.id == before.id and renamed.created_at == before.created_at
    assert renamed.current_version_id == before.current_version_id
    assert project_graph(database_path, project.id) == graph
    for name in ('', '  ', 'x' * 201):
        with pytest.raises(ValidationError):
            rename_project(database_path, project.id, name)
    assert get_project(database_path, project.id) == renamed


def test_delete_removes_only_selected_ownership_graph(database_path):
    from app.db import delete_project
    selected = seed_project_graph(database_path, 'Delete me')
    other = seed_project_graph(database_path, 'Keep me')
    before = get_project(database_path, other.id)
    graph = project_graph(database_path, other.id)
    delete_project(database_path, selected.id)
    assert get_project(database_path, selected.id) is None
    assert all(not rows for rows in project_graph(database_path, selected.id).values())
    assert get_project(database_path, other.id) == before
    assert project_graph(database_path, other.id) == graph
    with connect(database_path) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('PRAGMA user_version').fetchone()[0] == 1
        assert connection.execute('PRAGMA foreign_keys').fetchone()[0] == 1


def test_delete_failure_rolls_back_every_table(database_path):
    from app.db import delete_project
    project = seed_project_graph(database_path, 'Atomic delete')
    before = get_project(database_path, project.id)
    graph = project_graph(database_path, project.id)
    with connect(database_path) as connection:
        connection.execute("CREATE TRIGGER prevent_source_delete BEFORE DELETE ON sources BEGIN SELECT RAISE(ABORT, 'blocked'); END")
    with pytest.raises(sqlite3.IntegrityError):
        delete_project(database_path, project.id)
    assert get_project(database_path, project.id) == before
    assert project_graph(database_path, project.id) == graph
