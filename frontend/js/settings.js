/**
 * Tutor Settings screen (SRS 3.2.5, 3.2.6). Shown only to administrators.
 *
 *  - evidence limits: partial limit and strong limit (0 to 1)
 *  - the 4 x 4 decision table: learner state x evidence state -> teaching action
 *    (the Insufficient column is fixed to State uncertainty, as the SRS requires)
 *  - Save makes a new table version; Reset goes back to the defaults
 *
 * Talks to: GET/PUT /api/admin/tutor-settings, POST /api/admin/tutor-settings/reset
 */

const TutorSettings = (() => {
    const PATH = '/api/admin/tutor-settings';
    const ui = {};
    let data = null;
    let selects = {};     // learner -> evidence -> <select>

    // ── helpers ─────────────────────────────────────────────────────

    function el(tag, props, ...kids) {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(props || {})) {
            if (v === undefined || v === null || v === false) continue;
            if (k === 'class') node.className = v;
            else if (k === 'text') node.textContent = v;
            else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
            else if (k === 'value' || k === 'disabled' || k === 'hidden') node[k] = v;
            else node.setAttribute(k, v === true ? '' : String(v));
        }
        for (const kid of kids.flat()) {
            if (kid === null || kid === undefined || kid === false) continue;
            node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
        }
        return node;
    }

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function pretty(value) {
        const text = String(value || '').replace(/_/g, ' ').trim();
        return text ? text.charAt(0).toUpperCase() + text.slice(1) : '-';
    }

    function notify(message, type) {
        if (typeof App !== 'undefined' && App && App.toast) App.toast(message, type || 'info');
    }

    function isAdmin(user) {
        const r = String((user && user.role) || '').toLowerCase();
        return r === 'administrator' || r === 'admin';
    }

    // ── layout ──────────────────────────────────────────────────────

    function buildView(view) {
        ui.version = el('p', { class: 'admin-muted' });
        ui.partial = el('input', { type: 'number', min: '0', max: '1', step: '0.01', class: 'admin-input settings-number', oninput: markDirty });
        ui.strong = el('input', { type: 'number', min: '0', max: '1', step: '0.01', class: 'admin-input settings-number', oninput: markDirty });
        ui.table = el('div', { class: 'admin-tablewrap' });
        ui.error = el('p', { class: 'admin-error', hidden: true });
        ui.save = el('button', { type: 'button', class: 'btn btn--primary', text: 'Save as new version', onclick: save });
        ui.reset = el('button', { type: 'button', class: 'btn', text: 'Reset to default', onclick: reset });
        ui.undo = el('button', { type: 'button', class: 'btn', text: 'Discard changes', onclick: () => render() });
        ui.history = el('div', { class: 'admin-tablewrap' });

        view.appendChild(el('section', { class: 'section admin-card' },
            el('div', { class: 'section__header' },
                el('h2', { class: 'section__title' }, el('span', { class: 'section__icon', text: '⚙' }), 'Tutor settings'),
                el('p', { class: 'section__desc', text: 'How the tutor rates the evidence and which teaching action it picks. Each saved change becomes a new table version, shown in the Tutor State panel and the log.' })),
            ui.version,

            el('h3', { class: 'admin-h3', text: 'Evidence limits' }),
            el('div', { class: 'settings-limits' },
                el('label', { class: 'admin-field' },
                    el('span', { class: 'admin-label', text: 'Partial limit' }), ui.partial,
                    el('span', { class: 'admin-muted', text: 'Best score below this → Insufficient (no answer).' })),
                el('label', { class: 'admin-field' },
                    el('span', { class: 'admin-label', text: 'Strong limit' }), ui.strong,
                    el('span', { class: 'admin-muted', text: 'Best score from this, with 2+ passages above the partial limit → Strong.' }))),

            el('h3', { class: 'admin-h3', text: 'Decision table' }),
            el('p', { class: 'admin-muted', text: 'Rows: learner state. Columns: evidence. The Insufficient column is always State uncertainty, so the tutor never answers without evidence.' }),
            ui.table,
            ui.error,
            el('div', { class: 'admin-actions settings-buttons' }, ui.save, ui.undo, ui.reset)));

        view.appendChild(el('section', { class: 'section admin-card' },
            el('h3', { class: 'admin-h3', text: 'Earlier versions' }),
            ui.history));
    }

    // ── data ────────────────────────────────────────────────────────

    async function load() {
        ui.error.hidden = true;
        try {
            data = await Auth.request('GET', PATH);
            render();
        } catch (err) {
            showError(err.status === 403 ? 'Only administrators can change the tutor settings.' : err.message);
        }
    }

    function collect() {
        const cells = {};
        for (const learner of data.learner_states) {
            cells[learner] = {};
            for (const evidence of data.evidence_states) {
                cells[learner][evidence] = selects[learner][evidence].value;
            }
        }
        return {
            partial_limit: parseFloat(ui.partial.value),
            strong_limit: parseFloat(ui.strong.value),
            cells,
        };
    }

    function check(body) {
        const { partial_limit: p, strong_limit: s } = body;
        if (Number.isNaN(p) || Number.isNaN(s)) return 'Enter both limits.';
        if (p < 0 || s > 1 || p > s) return 'The limits must be between 0 and 1, and the partial limit cannot be above the strong limit.';
        return null;
    }

    async function save() {
        if (!data) return;
        const body = collect();
        const problem = check(body);
        if (problem) return showError(problem);
        ui.error.hidden = true;
        ui.save.disabled = true;
        try {
            const before = data.version;
            data = await Auth.request('PUT', PATH, body);
            render();
            notify(data.version === before ? 'Nothing changed.' : `Saved as table version ${data.version}.`,
                   data.version === before ? 'info' : 'success');
        } catch (err) {
            showError(err.message);
        } finally {
            ui.save.disabled = false;
        }
    }

    async function reset() {
        if (typeof confirm === 'function' && !confirm('Go back to the default limits and decision table? This is saved as a new version.')) return;
        try {
            const before = data && data.version;
            data = await Auth.request('POST', `${PATH}/reset`);
            render();
            notify(data.version === before ? 'Already using the defaults.' : `Defaults restored as table version ${data.version}.`, 'success');
        } catch (err) {
            showError(err.message);
        }
    }

    function showError(text) {
        ui.error.textContent = text;
        ui.error.hidden = false;
    }

    // ── rendering ───────────────────────────────────────────────────

    function markDirty() {
        if (!data) return;
        const body = collect();
        const dirty = body.partial_limit !== data.partial_limit || body.strong_limit !== data.strong_limit ||
            JSON.stringify(body.cells) !== JSON.stringify(sortCells(data.cells));
        ui.save.disabled = !dirty;
        ui.undo.disabled = !dirty;
        for (const learner of data.learner_states) {
            for (const evidence of data.evidence_states) {
                const s = selects[learner][evidence];
                s.classList.toggle('settings-changed', s.value !== data.cells[learner][evidence]);
            }
        }
    }

    function sortCells(cells) {
        const out = {};
        for (const learner of data.learner_states) {
            out[learner] = {};
            for (const evidence of data.evidence_states) out[learner][evidence] = cells[learner][evidence];
        }
        return out;
    }

    function render() {
        if (!data) return;
        ui.error.hidden = true;
        const by = data.updated_by ? ` · changed by ${data.updated_by} on ${new Date(data.updated_at).toLocaleString()}` : ' · default settings';
        ui.version.textContent = `Table version ${data.version}${by}`;
        ui.partial.value = data.partial_limit;
        ui.strong.value = data.strong_limit;

        clear(ui.table);
        selects = {};
        const locked = data.locked || {};
        const rows = data.learner_states.map(learner => {
            selects[learner] = {};
            return el('tr', {},
                el('th', { class: 'settings-rowhead', text: pretty(learner) }),
                data.evidence_states.map(evidence => {
                    const fixed = locked[evidence];
                    const select = el('select', { class: 'admin-select settings-select', disabled: !!fixed, onchange: markDirty },
                        (fixed ? [fixed] : data.actions).map(a => el('option', { value: a, text: pretty(a) })));
                    select.value = data.cells[learner][evidence];
                    selects[learner][evidence] = select;
                    return el('td', {}, select);
                }));
        });
        ui.table.appendChild(el('table', { class: 'admin-table settings-table' },
            el('thead', {}, el('tr', {}, el('th', { text: 'Learner \\ Evidence' }),
                data.evidence_states.map(e => el('th', { text: pretty(e) })))),
            el('tbody', {}, rows)));

        renderHistory();
        markDirty();
    }

    function renderHistory() {
        clear(ui.history);
        const history = data.history || [];
        if (!history.length) {
            ui.history.appendChild(el('p', { class: 'admin-muted', text: 'No earlier versions yet.' }));
            return;
        }
        const rows = history.map(h => {
            const changed = [];
            for (const learner of data.learner_states) {
                for (const evidence of data.evidence_states) {
                    const old = h.cells && h.cells[learner] && h.cells[learner][evidence];
                    if (old && old !== data.cells[learner][evidence]) changed.push(`${pretty(learner)} / ${pretty(evidence)}: ${pretty(old)}`);
                }
            }
            return el('tr', {},
                el('td', { text: `v${h.version}` }),
                el('td', { text: `${h.partial_limit} / ${h.strong_limit}` }),
                el('td', { class: 'settings-diff', text: changed.length ? changed.join(' · ') : 'Same table as now' }),
                el('td', { text: h.updated_by ? `${h.updated_by}, ${new Date(h.updated_at).toLocaleString()}` : 'Default' }));
        });
        ui.history.appendChild(el('table', { class: 'admin-table' },
            el('thead', {}, el('tr', {}, ['Version', 'Limits (partial / strong)', 'Cells that differ from now', 'Saved by'].map(t => el('th', { text: t })))),
            el('tbody', {}, rows)));
    }

    // ── init ────────────────────────────────────────────────────────

    /** Call after login and before the tabs are wired up. Does nothing for non-administrators. */
    function init() {
        const me = (typeof Auth !== 'undefined' && Auth.getUser) ? Auth.getUser() : null;
        if (!isAdmin(me)) return;
        const nav = document.querySelector('.header__nav');
        const main = document.getElementById('app-main');
        if (!nav || !main) return;

        const button = el('button', { class: 'nav-btn', 'data-view': 'settings', text: 'Tutor Settings' });
        button.addEventListener('click', load);
        nav.appendChild(button);

        const view = el('div', { id: 'view-settings', class: 'view admin-root', hidden: true });
        main.appendChild(view);
        buildView(view);
    }

    return { init };
})();
