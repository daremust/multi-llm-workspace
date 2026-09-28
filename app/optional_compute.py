"""Four explicit optional operations on human-chosen drafts, using the shared executor."""

from dataclasses import replace
from pathlib import Path

from app.db import connect, generation_inputs, get_project
from app.generation import GenerationPlan, prepare_requests
from app.openrouter import Catalog
from app.optional_prompts import FactCheck, OPERATIONS, PROMPT_VERSION, optional_messages
from app.review import get_primary_response


def default_output_tokens(operation: str) -> int:
    return 4096 if operation == 'fact_check' else 1024


def draft_inputs(database_path: Path, project_id: int) -> list[dict]:
    """Eligible persisted drafts; feedback and failed requests are never merge inputs."""
    with connect(database_path) as connection:
        responses = connection.execute(
            "SELECT r.id, r.text, q.run_id FROM responses r JOIN run_requests q ON q.id = r.run_request_id "
            "JOIN runs g ON g.id = q.run_id WHERE r.project_id = ? AND r.kind = 'draft' "
            "AND r.origin = 'api' AND q.status = 'succeeded' AND g.operation IN ('generation', 'rewrite', 'merge') ORDER BY r.id",
            (project_id,),
        ).fetchall()
        versions = connection.execute(
            'SELECT v.id, v.text, v.derived_from_response_id, q.run_id FROM versions v '
            'LEFT JOIN responses r ON r.id = v.derived_from_response_id '
            'LEFT JOIN run_requests q ON q.id = r.run_request_id WHERE v.project_id = ? ORDER BY v.id',
            (project_id,),
        ).fetchall()
        return [dict(kind='response', id=row['id'], response_id=row['id'], version_id=None,
                     run_id=row['run_id'], text=row['text']) for row in responses] + [
            dict(kind='version', id=row['id'], response_id=row['derived_from_response_id'],
                 version_id=row['id'], run_id=row['run_id'], text=row['text']) for row in versions]


def prepare_optional_operation(database_path: Path, project_id: int, catalog: Catalog,
                               model_id: str, operation: str, input_refs: list[tuple[str, int]],
                               instructions: str = '', working_text: str | None = None,
                               max_tokens: int | None = None, temperature: float = 0.7) -> GenerationPlan:
    if operation not in OPERATIONS:
        raise ValueError('Choose rewrite, merge, critique, or fact_check.')
    if len(set(input_refs)) != len(input_refs):
        raise ValueError('Choose distinct inputs.')
    if operation == 'merge':
        if len(input_refs) < 2 or working_text is not None:
            raise ValueError('Merge requires at least two explicitly selected saved responses/drafts.')
    elif len(input_refs) != 1:
        raise ValueError('Choose the current working copy or current saved version.')
    available = {(item['kind'], item['id']): item for item in draft_inputs(database_path, project_id)}
    if any(ref not in available for ref in input_refs):
        raise ValueError('An input is unavailable or does not belong to this project.')
    selected = [dict(available[ref]) for ref in input_refs]
    if operation != 'merge':
        project = get_project(database_path, project_id)
        primary = get_primary_response(database_path, project_id)
        item = selected[0]
        if working_text is not None:
            if primary is None or item['response_id'] != primary.id:
                raise ValueError('Working copy must derive from the current primary response.')
            expected_version = next((entry for entry in available.values()
                                     if entry['kind'] == 'version' and entry['id'] == project.current_version_id
                                     and entry['response_id'] == primary.id), None)
            expected = ('version', expected_version['id']) if expected_version else ('response', primary.id)
            if input_refs[0] != expected:
                raise ValueError('The working-copy base changed. Review the current draft before running.')
            item['text'] = working_text
            item['input_mode'] = 'working_copy'
        elif item['kind'] != 'version' or item['id'] != project.current_version_id:
            raise ValueError('Choose the current saved version, or explicitly use the working copy.')
    if any(not item['text'].strip() for item in selected):
        raise ValueError('Optional compute requires nonempty draft text.')
    snapshot = generation_inputs(database_path, project_id)
    if operation == 'fact_check' and not snapshot['sources']:
        raise ValueError('Source verification requires original project sources. Add a Source of Truth first.')
    snapshot.update(operation=operation, selected_inputs=selected, operation_instructions=instructions)
    if max_tokens is None:
        max_tokens = default_output_tokens(operation)
    plan = prepare_requests(snapshot, catalog, [model_id], optional_messages(operation, snapshot), max_tokens, temperature)
    prompt_version = f'{PROMPT_VERSION}:{operation}'
    if operation == 'fact_check':
        model = next(model for model in catalog.models if model.id == model_id)
        # response_format alone can mean JSON-object mode, not JSON Schema support.
        supported = 'structured_outputs' in (model.supported_parameters or ())
        plan.parameters['fact_check_output_mode'] = 'json_schema' if supported else 'prompt_only'
        if supported:
            # prepare_requests already requires compatible providers and forbids fallback.
            plan.parameters['requests'][model_id]['response_format'] = {
                'type': 'json_schema',
                'json_schema': {'name': 'fact_check', 'strict': True, 'schema': FactCheck.model_json_schema()},
            }
        prompt_version = 'optional-compute-v3:fact_check'
    parents = {item['run_id'] for item in selected if item['run_id'] is not None}
    return replace(plan, operation=operation, parent_run_id=next(iter(parents)) if len(parents) == 1 else None,
                   prompt_version=prompt_version)
