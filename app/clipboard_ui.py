"""Local clipboard presentation and allowlisted historical copy payloads."""

import json

import streamlit.components.v2 as components

from app.types import RunRequest

COPY_JS = """
export default function({data, parentElement}) {
    // A data update can reuse the host without running unmount cleanup.
    // Dispose only our previous render; leave Streamlit's host/styles intact.
    parentElement.__workspaceClipboardDispose?.();
    const root = document.createElement('div');
    root.className = 'copy-row';
    const heading = document.createElement('span');
    heading.textContent = data.heading || '';
    root.appendChild(heading);
    const feedback = document.createElement('span');
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    let alive = true;
    let actionSequence = 0;
    const buttons = [];
    for (const action of data.actions) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = action.icon ? '⧉' : action.label;
        button.title = action.label;
        button.setAttribute('aria-label', action.label);
        button.disabled = action.disabled;
        // Keep editor focus: copying must not commit its value via blur/rerun.
        button.onmousedown = event => event.preventDefault();
        button.onclick = async () => {
            if (!alive || button.disabled) return;
            const sequence = ++actionSequence;
            feedback.textContent = '';
            try {
                let text = action.text;
                if (data.editor) {
                    const editor = document.querySelector('.st-key-' + data.editor + ' textarea');
                    if (!editor) throw new Error('Editor unavailable');
                    text = editor.value;
                }
                await navigator.clipboard.writeText(text);
                if (alive && sequence === actionSequence) feedback.textContent = 'Copied';
            } catch (_) {
                if (alive && sequence === actionSequence) feedback.textContent = 'Could not copy. Allow clipboard access or select and copy the text manually.';
            }
        };
        buttons.push(button);
        root.appendChild(button);
    }
    root.appendChild(feedback);
    parentElement.appendChild(root);
    const dispose = () => {
        alive = false;
        for (const button of buttons) {
            button.onclick = null;
            button.onmousedown = null;
        }
        root.remove();
        // A delayed cleanup for an older render must not affect its replacement.
        if (parentElement.__workspaceClipboardDispose === dispose) {
            delete parentElement.__workspaceClipboardDispose;
        }
    };
    parentElement.__workspaceClipboardDispose = dispose;
    return dispose;
}
"""

def _copy_component():
    # Register in the active runtime, including fresh AppTest/server runtimes.
    # Registration of the same definition is idempotent.
    return components.component(
        'workspace_clipboard', js=COPY_JS,
        css=''' .copy-row { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem; }
        button { font:inherit; color:inherit; background:transparent; border:1px solid currentColor;
                 border-radius:.35rem; padding:.2rem .5rem; cursor:pointer; }
        button:disabled { opacity:.45; cursor:default; }
        [role=status] { font-size:.85em; } ''',
    )


def copy_controls(key: str, actions: list[dict], *, heading: str = '', editor: str | None = None) -> None:
    _copy_component()(key=key, data={'heading': heading, 'actions': actions, 'editor': editor})


def copy_action(label: str, text: str, *, disabled: bool = False, icon: bool = False) -> dict:
    return dict(label=label, text=text, disabled=disabled, icon=icon)


def model_name(request: RunRequest) -> str:
    metadata = json.loads(request.response.generation_metadata_json) if request.response else {}
    return metadata.get('catalog_name') or request.requested_model


def selection_key(project_id: int, run_id: int, request_id: int) -> str:
    return f'copy_selected_{project_id}_{run_id}_{request_id}'


def bulk_payload(requests: list[RunRequest], snapshot: dict | None = None) -> str:
    successful = [r for r in requests if r.status == 'succeeded' and r.response is not None]
    if not successful:
        return ''
    parts = []
    if snapshot is not None:
        parts += ['ORIGINAL TASK', snapshot.get('task', ''),
                  'ADDITIONAL INSTRUCTIONS', snapshot.get('additional_instructions', ''),
                  'OUTPUT FORMAT', snapshot.get('output_format', '')]
        if snapshot.get('output_format') == 'Custom':
            parts.append(snapshot.get('custom_output_format', ''))
        parts.append('ORIGINAL SOURCES')
        for source in snapshot.get('sources', []):
            parts.append(f'Source #{source.get("id", "?")}: {source.get("title") or "Untitled source"}')
            for field in ('original_url', 'final_url'):
                url = source.get(field)
                if url and (field == 'original_url' or url != source.get('original_url')):
                    parts.append(f'{"Fetched URL" if field == "final_url" else "URL"}: {url}')
            parts.append(source.get('content', ''))
    for request in successful:
        parts += ['---', f'MODEL: {model_name(request)} ({request.requested_model})', request.response.text]
    return '\n\n'.join(parts)
