"""Bridge older AppTest runtimes that leave the v2 registry mocked."""

from functools import wraps
from unittest.mock import Mock, patch

from streamlit.components.v2.component_manager import BidiComponentManager
from streamlit.runtime import Runtime
from streamlit.testing.v1 import app_test


def ensure_component_registry(app, runtime):
    # Modern AppTest supplies its own real manager: leave it entirely alone.
    if isinstance(runtime.bidi_component_registry, Mock):
        manager = getattr(app, '_clipboard_test_registry', None)
        if manager is None:
            manager = BidiComponentManager()
            app._clipboard_test_registry = manager
        runtime.bidi_component_registry = manager


def install_registry_adapter(monkeypatch):
    original_run = app_test.AppTest._run
    original_runner = app_test.LocalScriptRunner

    @wraps(original_run)
    def run(app, *args, **kwargs):
        def runner(*runner_args, **runner_kwargs):
            # AppTest has created its runtime, but no application code has run yet.
            ensure_component_registry(app, Runtime.instance())
            return original_runner(*runner_args, **runner_kwargs)

        with patch.object(app_test, 'LocalScriptRunner', runner):
            return original_run(app, *args, **kwargs)

    monkeypatch.setattr(app_test.AppTest, '_run', run)
