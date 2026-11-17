"""All tests use temporary local data and prohibit outbound connections."""

import socket

import pytest

from app.db import initialize_database


@pytest.fixture(autouse=True)
def apptest_component_registry(monkeypatch):
    from apptest_compat import install_registry_adapter

    install_registry_adapter(monkeypatch)


@pytest.fixture(autouse=True)
def retained_widget_initialization_warning(monkeypatch):
    from streamlit.elements.lib import policies

    original = policies.check_session_state_rules
    monkeypatch.setattr(policies, '_shown_default_value_warning', False)

    def check(default_value, key, writes_allowed=True):
        is_editor = key is not None and key.startswith((
            'editor_review_draft_', 'max_tokens_', 'temperature_',
            'optional_max_tokens_', 'optional_temperature_',
        ))
        if is_editor:
            # Check every retained parameter/editor render, even if another widget already warned.
            policies._shown_default_value_warning = False
            assert not policies.config.get_option('global.disableWidgetStateDuplicationWarning')
        result = original(default_value, key, writes_allowed)
        if is_editor:
            assert not policies._shown_default_value_warning, f'Widget {key} initialized twice'
        return result

    monkeypatch.setattr(policies, 'check_session_state_rules', check)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in automated tests.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)


@pytest.fixture
def database_path(tmp_path):
    path = tmp_path / "data" / "workspace.db"
    initialize_database(path)
    return path


@pytest.fixture
def app_environment(monkeypatch, tmp_path):
    path = tmp_path / "ui" / "workspace.db"
    monkeypatch.setenv("WORKSPACE_DB_PATH", str(path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    return path



@pytest.fixture
def pricing_catalog():
    """Representative pricing shapes from the reported catalog, without network access."""
    from app.openrouter import parse_catalog
    from test_openrouter import model_entry

    shapes = {
        'openai/gpt-6-luna': {'web_search': '0.01', 'overrides': [
            {'min_prompt_tokens': 272000, 'prompt': '0.000002'}]},
        '~anthropic/claude-sonnet-latest': {'web_search': '0.01', 'input_cache_write_1h': '0.000004'},
        'google/gemini-3.8-flash': {'image': '0.000000825', 'audio': '0.000000825',
                                  'internal_reasoning': '0.000004125', 'web_search': '0.014'},
        'perplexity/sonar': {'web_search': '0.005'},
    }
    return parse_catalog({'data': [model_entry(model_id, pricing={
        'prompt': '0.000001', 'completion': '0.000002', 'input_cache_read': '0.0000001', **extras,
    }) for model_id, extras in shapes.items()]})
