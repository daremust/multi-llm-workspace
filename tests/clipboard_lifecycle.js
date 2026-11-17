// Dependency-free JS regression harness. Pass the actual COPY_JS source.
// Uses DOM/clipboard doubles; real browser permissions remain a manual check.
async function testClipboardLifecycle(source) {
    const check = (ok, message) => { if (!ok) throw new Error(message); };
    class Element {
        constructor() { this.children = []; this.textContent = ''; }
        setAttribute() {}
        appendChild(child) { child.parent = this; this.children.push(child); }
        remove() {
            if (this.parent) this.parent.children = this.parent.children.filter(x => x !== this);
            this.parent = null;
        }
    }
    const editor = {value: 'live unsaved text'};
    const document = {createElement: () => new Element(), querySelector: () => editor};
    const writes = [];
    const pending = [];
    const navigator = {clipboard: {writeText(text) {
        writes.push(text);
        return new Promise((resolve, reject) => pending.push({resolve, reject}));
    }}};
    const render = new Function('document', 'navigator', source.replace('export default function', 'return function'))(document, navigator);
    const hosts = [new Element(), new Element(), new Element()];
    const cleanups = [];
    for (let i = 0; i < 12; i++) {
        for (let kind = 0; kind < 3; kind++) {
            const host = hosts[kind];
            const old = host.children[0];
            const actions = [{label: `Copy selected (${i % 3})`, text: `payload ${i}`, disabled: i % 3 === 0}];
            if (kind === 0) actions.push({label: 'Copy all (3)', text: `context ${i}`});
            cleanups.push(render({parentElement: host, data: {actions, editor: kind === 2 ? 'scope' : null}}));
            check(host.children.length === 1, 'Duplicate rows after update');
            if (old) {
                check(!old.parent, 'Old row retained');
                check(old.children[1].onclick === null && old.children[1].onmousedown === null, 'Old listeners retained');
            }
            check(host.children[0].children.length === actions.length + 2, 'Wrong button/feedback count');
        }
    }
    const host = hosts[0];
    let row = host.children[0];
    check(row.children[1].textContent === 'Copy selected (2)', 'Stale count');
    let promise = row.children[2].onclick();
    check(writes.at(-1) === 'context 11', 'Stale payload');
    check(row.children.at(-1).textContent === '', 'Premature success');
    pending.shift().resolve(); await promise;
    check(row.children.at(-1).textContent === 'Copied', 'Missing success');
    const oldRow = row;
    promise = row.children[2].onclick();
    const dispose = render({parentElement: host, data: {actions: [{label: 'Copy all (3)', text: 'new context'}]}});
    row = host.children[0];
    pending.shift().resolve(); await promise;
    check(oldRow.children.at(-1).textContent === '', 'Stale async feedback');
    check(row.children.at(-1).textContent === '', 'Feedback carried across updates');
    cleanups[0]();
    check(host.children.length === 1, 'Old cleanup removed current row');
    // Out-of-order completions must report the latest action only.
    const first = row.children[1].onclick();
    const second = row.children[1].onclick();
    const earlier = pending.shift(), latest = pending.shift();
    latest.reject(Error('denied')); await second;
    earlier.resolve(); await first;
    check(row.children.at(-1).textContent.startsWith('Could not copy'), 'Old success overwrote denial');
    promise = hosts[2].children[0].children[1].onclick();
    check(writes.at(-1) === editor.value, 'Final copied stale data');
    pending.shift().resolve(); await promise;
    dispose(); dispose();
    check(host.children.length === 0 && !host.__workspaceClipboardDispose, 'Unmount cleanup failed');
    check(hosts[1].children.length === 1, 'Cross-component interference');
    return 'PASS: repeated bulk/icon/Final updates, latest counts/payloads, listener disposal, async feedback, denial, live editor, and isolated cleanup';
}

// Optional local runner: pipe COPY_JS into `node tests/clipboard_lifecycle.js`.
if (typeof module !== 'undefined' && require.main === module) {
    testClipboardLifecycle(require('fs').readFileSync(0, 'utf8'))
        .then(result => console.log(result))
        .catch(error => { console.error(error); process.exitCode = 1; });
}
