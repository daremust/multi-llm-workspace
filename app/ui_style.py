"""Small shared presentation layer; native Streamlit owns themes and controls."""

import re

import streamlit as st


CSS = """
.stMainBlockContainer { max-width: 96rem; margin-inline: auto; padding: 4rem 3rem 2rem; }
[data-testid="stSidebar"][aria-expanded="true"] { min-width: 250px; max-width: 280px; }
h1, h2, h3, input, textarea, button { font-family: inherit; }
h1 { font-size: clamp(2rem, 3.5vw, 3rem); letter-spacing: -.04em; }
h2 { font-size: 1.6rem; }
hr { border: 0; border-top: 1px dashed color-mix(in srgb, currentColor 35%, transparent); margin: 1rem 0; }
.eyebrow, .stage-number { font-size: .85rem; letter-spacing: .06em; opacity: .75; }
.workspace-status { text-align: right; font-size: .85rem; }
.local-dot { color: #219653; font-size: .95rem; margin-right: .45rem; }
.local-note { opacity: .75; font-size: .85rem; margin: .4rem 0 0 1.2rem; }
[data-testid="stSidebarUserContent"] { padding-top: 1.5rem; padding-bottom: 1rem; }
.st-key-sidebar_layout { gap: 32px; }
.st-key-sidebar_projects_heading { margin-top: 8px; }
.st-key-workspace_home button { background: transparent; border: 0; padding: 0; text-align: left; }
.st-key-workspace_home button p { white-space: pre-line; font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
.st-key-workspace_home button:hover { opacity: .8; }
.sidebar-rule { font-size: 1.5rem; }
/* Native vertical blocks contribute 16px; add only the remaining separation. */
.st-key-workspace_help { margin-top: 20px; margin-bottom: 6px; }
.st-key-home_composition { max-width: 72rem; margin-inline: auto; }
.st-key-home_hero { text-align: center; padding-top: clamp(1rem, 6vh, 4rem); }
.st-key-home_hero h1, .st-key-home_hero [data-testid="stMarkdownContainer"] p,
.st-key-home_hero [data-testid="stCaptionContainer"] { text-align: center; }
/* Explicit icon elements use the same bundled Material font as native symbols. */
.workflow { --workflow-icon: 24px; --workflow-label: 15px; --workflow-arrow: 15px;
    display: grid; grid-template-columns: 1fr auto 1fr auto 1fr auto 1.3fr;
    gap: .5rem; align-items: center; width: 100%; max-width: 70rem; margin: 0 auto .75rem; }
.workflow--hero { --workflow-icon: 30px; --workflow-label: 17px; --workflow-arrow: 17px; padding: 2.25rem 0; }
.workflow-stage { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; }
.workflow-icon { font-family: "Material Symbols Rounded"; font-size: var(--workflow-icon);
    font-weight: 400; font-style: normal; line-height: 1; letter-spacing: normal;
    text-transform: none; white-space: nowrap; font-feature-settings: "liga"; }
.workflow-label { font-size: var(--workflow-label); line-height: 1.3; font-weight: 600; }
.workflow-arrow { font-size: var(--workflow-arrow); opacity: .6; }
@media (max-width: 650px) {
    .workflow { grid-template-columns: repeat(4, minmax(0, 1fr)); }
    .workflow-arrow { display: none; }
}
/* Two native interactions share one surface; only the selectbox keeps a chevron. */
.st-key-project_control { border: 1px solid color-mix(in srgb, currentColor 20%, transparent); border-radius: .5rem; }
.st-key-project_control [data-testid="stHorizontalBlock"] { gap: 0; align-items: center; }
.st-key-project_control [data-baseweb="select"] > div { border: 0; border-radius: .5rem 0 0 .5rem; }
.st-key-project_control [data-testid="stPopoverButton"] { border: 0; border-radius: 0 .5rem .5rem 0; background: transparent; font-size: 1.4rem; }
.st-key-project_control [data-testid="stPopoverButton"] [aria-hidden="true"] { display: none; }
.st-key-project_control [data-testid="stColumn"] { min-width: 0; }

.sidebar-title { font-size: 1.5rem; font-weight: 700; line-height: 1.2; }
.sidebar-tagline { opacity: .75; line-height: 1.6; margin-top: 1rem; }
/* Native Markdown Material symbols use Streamlit's bundled icon font. */
.sidebar-quote { opacity: .7; line-height: 1.6; margin: 0 0 2rem; }
.st-key-workspace_tip { background: color-mix(in srgb, currentColor 4%, transparent); padding: .75rem 1rem; border-radius: .35rem; }
.stage-heading { border-bottom: 1px dashed color-mix(in srgb, currentColor 35%, transparent); padding-bottom: .5rem; margin-bottom: .5rem; }
.stage-heading h3 { font-size: 1.1rem; margin: 0; padding: 0; letter-spacing: .04em; }
.stage-number { margin-right: .75rem; }
.stage-arrow { font-size: 1.4rem; margin: .5rem 0; opacity: .65; }
[data-testid="stExpander"] details { border: 0; border-bottom: 1px dashed color-mix(in srgb, currentColor 35%, transparent); border-radius: 0; }
/* Keep comparison readable: one row, with horizontal scrolling when needed. */
.st-key-review_comparison [data-testid="stHorizontalBlock"] {
    flex-wrap: nowrap; overflow-x: auto; align-items: stretch; padding-bottom: .75rem;
}
.st-key-review_comparison [data-testid="stColumn"] { min-width: min(20rem, 80vw); flex: 1 0 0; }
@media (max-width: 850px) { .stMainBlockContainer { padding: 4rem 1.25rem 2rem; } }
"""


def apply_style() -> None:
    # Inherit the active native theme, including explicit overrides of System.
    # Never hide Streamlit's toolbar, running indicator, or keyboard focus styles.
    st.markdown(f'<style>{CSS}</style>', unsafe_allow_html=True)


def render_workflow(*, hero: bool = False) -> None:
    stages = (
        ('description', '01 INPUT'), ('auto_awesome', '02 GENERATE'),
        ('format_list_bulleted', '03 COMPARE'), ('tune', '04 OPTIONAL COMPUTE'),
    )
    parts = []
    for icon, label in stages:
        parts.append(
            f'<div class="workflow-stage"><span class="workflow-icon" aria-hidden="true">{icon}</span>'
            f'<span class="workflow-label">{label}</span></div>'
        )
    arrow = '<span class="workflow-arrow" aria-hidden="true">→</span>'
    variant = ' workflow--hero' if hero else ''
    st.markdown(f'<div class="workflow{variant}" aria-label="Workspace workflow">'
                f'{arrow.join(parts)}</div>', unsafe_allow_html=True)


def summary(text: str, limit: int = 160) -> str:
    """A plain-text preview, never used as a replacement for saved input."""
    compact = ' '.join(text.split())
    return compact if len(compact) <= limit else compact[:limit].rstrip() + '…'


def section_label(title: str, state: str) -> str:
    """Keep user-authored summaries literal inside native Markdown labels."""
    escaped = re.sub(r'([\\`*_{}\[\]()#+.!|>~<])', r'\\\1', state)
    return f"{title} · {escaped}"


def status_label(status: str) -> str:
    """Presentation only: preserve the distinction between persisted outcomes."""
    return {
        'pending': 'Pending', 'running': 'Running', 'succeeded': 'Succeeded',
        'partial': 'Partial', 'failed': 'Failed', 'interrupted': 'Interrupted / uncertain',
    }.get(status, status)
