import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from streamlit.components.v2.component_manager import BidiComponentManager
from streamlit.testing.v1 import AppTest

from app.clipboard_ui import COPY_JS
from apptest_compat import ensure_component_registry


def test_mock_registry_replaced_and_scoped_to_apptest():
    first, second = SimpleNamespace(), SimpleNamespace()
    runtime = SimpleNamespace(bidi_component_registry=MagicMock())
    ensure_component_registry(first, runtime)
    manager = runtime.bidi_component_registry
    assert isinstance(manager, BidiComponentManager)
    runtime.bidi_component_registry = MagicMock()
    ensure_component_registry(first, runtime)
    assert runtime.bidi_component_registry is manager
    runtime.bidi_component_registry = MagicMock()
    ensure_component_registry(second, runtime)
    assert runtime.bidi_component_registry is not manager


def test_native_registry_untouched():
    app = SimpleNamespace()
    manager = BidiComponentManager()
    runtime = SimpleNamespace(bidi_component_registry=manager)
    ensure_component_registry(app, runtime)
    assert runtime.bidi_component_registry is manager
    assert not hasattr(app, '_clipboard_test_registry')


def test_real_clipboard_mounts_across_reruns_and_independent_apps():
    script = '''
import streamlit as st
from streamlit.runtime import Runtime
from app.clipboard_ui import copy_controls, copy_action
st.session_state['registry'] = Runtime.instance().bidi_component_registry
include = st.checkbox('Context')
for key in ('individual', 'bulk', 'final'):
    copy_controls(key, [copy_action('Copy', 'context' if include else 'plain')])
'''
    first = AppTest.from_string(script).run()
    assert not first.exception
    manager = first.session_state['registry']
    assert isinstance(manager, BidiComponentManager)
    assert manager.get('workspace_clipboard').js_content == COPY_JS
    if getattr(first, '_bidi_component_manager', None) is not None:
        assert manager is first._bidi_component_manager
        assert not hasattr(first, '_clipboard_test_registry')
    for value in (True, False, True):
        first.checkbox[0].set_value(value).run()
        assert not first.exception
        assert first.session_state['registry'] is manager
        components = first.get('bidi_component')
        assert len(components) == 3
        assert all(component.proto.js_content == COPY_JS for component in components)
        assert all(json.loads(component.proto.json)['actions'][0]['text'] ==
                   ('context' if value else 'plain') for component in components)
    second = AppTest.from_string(script).run()
    assert not second.exception
    assert second.session_state['registry'] is not manager
    # Interleaving separate AppTest instances must not replace either registry.
    first.run()
    assert not first.exception
    assert first.session_state['registry'] is manager
