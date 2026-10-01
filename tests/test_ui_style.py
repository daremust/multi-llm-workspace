"""Theme configuration and presentation safeguards; browser geometry checked separately."""

from pathlib import Path
import tomllib

from app.ui_style import summary

ROOT = Path(__file__).resolve().parents[1]


def test_switchable_native_themes_and_local_defaults():
    config = tomllib.loads((ROOT / '.streamlit/config.toml').read_text())
    theme = config['theme']
    assert 'base' not in theme  # No forced Light/Dark instead of System.
    assert theme['font'] == 'monospace'
    assert theme['light']['backgroundColor'] != theme['dark']['backgroundColor']
    for name in ('light', 'dark'):
        assert theme[name]['linkColor'] == theme[name]['primaryColor']
        assert theme[name]['textColor'] != theme[name]['backgroundColor']
    assert config['server']['address'] == '127.0.0.1'
    assert config['browser']['gatherUsageStats'] is False


def test_saved_input_summary_is_bounded_plain_text():
    assert summary('One\n  two\tthree') == 'One two three'
    assert summary('x' * 200) == 'x' * 160 + '…'
    assert summary('<script>not HTML</script>') == '<script>not HTML</script>'


def test_section_summary_does_not_interpret_user_markdown():
    from app.ui_style import section_label
    assert section_label('02  TASK & OUTPUT', '*Task* [link](url)') == r'02  TASK & OUTPUT · \*Task\* \[link\]\(url\)'


def test_workflow_variants_share_four_icon_over_label_stages():
    from streamlit.testing.v1 import AppTest

    def preview(hero):
        from app.ui_style import render_workflow
        render_workflow(hero=hero)

    expected = [
        ('description', '01 INPUT'), ('auto_awesome', '02 GENERATE'),
        ('format_list_bulleted', '03 COMPARE'), ('tune', '04 OPTIONAL COMPUTE'),
    ]
    for hero in (False, True):
        app = AppTest.from_function(preview, args=(hero,)).run()
        assert not app.exception
        markup = app.markdown[0].value
        for icon, label in expected:
            assert f'class="workflow-icon" aria-hidden="true">{icon}</span>' in markup
            assert f'class="workflow-label">{label}</span>' in markup
        assert markup.count('class="workflow-arrow"') == 3
        assert ('workflow--hero' in markup) is hero



def test_outcome_labels_keep_failure_partial_and_uncertainty_distinct():
    from app.ui_style import status_label

    assert [status_label(s) for s in
            ('pending', 'running', 'succeeded', 'partial', 'failed', 'interrupted')] == [
        'Pending', 'Running', 'Succeeded', 'Partial', 'Failed', 'Interrupted / uncertain',
    ]
